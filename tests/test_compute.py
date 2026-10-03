"""Weights, drift, the zero-unit portfolio, missing prices, the first run."""

from harness import check, done, equal, heading
from datetime import date

from monitor.compute import build_portfolio, movers
from monitor.config import Config, Holding
from monitor.prices import Quote


def config(holdings, **kw):
    return Config(
        portfolio_name="T",
        currency="AUD",
        holdings=holdings,
        alerts=[],
        tolerate_unallocated_pct=kw.get("tolerate", 0),
        also_to=[],
        note_threshold_pct=kw.get("note_threshold", 3),
    )


heading("weights and drift, against hand-worked numbers")
# 100 BHP @ 40.00 = 4,000; 50 VAS @ 90.00 = 4,500; 25 ARG @ 10.00 = 250.
# Total 8,750. Weights: 4000/8750 = 45.714...%, 4500/8750 = 51.428...%,
# 250/8750 = 2.857...%. Targets 50 / 45 / 5 give drift -4.286 / +6.429 / -2.143.
cfg = config([
    Holding("BHP.AX", 100, 50, "equity"),
    Holding("VAS.AX", 50, 45, "etf"),
    Holding("ARG.AX", 25, 5, "lic"),
])
quotes = {
    "BHP.AX": Quote("BHP.AX", price=40.0),
    "VAS.AX": Quote("VAS.AX", price=90.0),
    "ARG.AX": Quote("ARG.AX", price=10.0),
}
p = build_portfolio(cfg, quotes, previous_prices={}, previous_run=date(2026, 9, 26),
                    today=date(2026, 10, 3))
equal("total value", p.total_value, 8750.0, 1e-9)
rows = {r.ticker: r for r in p.rows}
equal("BHP value", rows["BHP.AX"].value, 4000.0, 1e-9)
equal("BHP weight", rows["BHP.AX"].actual_weight, 45.714285714, 1e-6)
equal("VAS weight", rows["VAS.AX"].actual_weight, 51.428571428, 1e-6)
equal("ARG weight", rows["ARG.AX"].actual_weight, 2.857142857, 1e-6)
equal("BHP drift pp", rows["BHP.AX"].drift_pp, -4.285714285, 1e-6)
equal("VAS drift pp", rows["VAS.AX"].drift_pp, 6.428571428, 1e-6)
equal("ARG drift pp", rows["ARG.AX"].drift_pp, -2.142857142, 1e-6)
equal("weights sum to 100", sum(r.actual_weight for r in p.rows), 100.0, 1e-9)
check("drift is percentage points, not a ratio of the target",
      abs(rows["VAS.AX"].drift_pp - (51.428571428 - 45)) < 1e-6)

heading("week change from the stored prices, not invented")
p2 = build_portfolio(cfg, quotes, previous_prices={"BHP.AX": 32.0, "VAS.AX": 90.0},
                     previous_run=date(2026, 9, 26), today=date(2026, 10, 3))
rows2 = {r.ticker: r for r in p2.rows}
equal("BHP +25% from 32.00 to 40.00", rows2["BHP.AX"].week_change_pct, 25.0, 1e-9)
equal("VAS flat", rows2["VAS.AX"].week_change_pct, 0.0, 1e-9)
check("ARG has no stored price so no change is invented",
      rows2["ARG.AX"].week_change_pct is None)
check("not flagged as a first run", p2.first_run is False)

heading("zero-unit portfolio divides by nothing")
zero = config([Holding("BHP.AX", 0, 0, "equity"), Holding("VAS.AX", 0, 0, "etf")])
pz = build_portfolio(zero, {t: Quote(t, price=40.0) for t in ("BHP.AX", "VAS.AX")},
                     previous_prices={}, previous_run=None, today=date(2026, 10, 3))
equal("total is zero", pz.total_value, 0.0, 1e-9)
check("weights are absent, not zero", all(r.actual_weight is None for r in pz.rows))
check("drift is absent, not zero", all(r.drift_pp is None for r in pz.rows))
check("flagged unconfigured", pz.unconfigured is True)
check("says so plainly",
      any("No holdings are configured yet" in w for w in pz.warnings),
      f"warnings were {pz.warnings}")

heading("a missing price is loud, and never reads as a real number")
pm = build_portfolio(
    cfg,
    {
        "BHP.AX": Quote("BHP.AX", price=40.0),
        "VAS.AX": Quote("VAS.AX", error="HTTP Error 429: Too Many Requests", rate_limited=True),
        "ARG.AX": Quote("ARG.AX", price=10.0),
    },
    previous_prices={}, previous_run=date(2026, 9, 26), today=date(2026, 10, 3),
)
rows3 = {r.ticker: r for r in pm.rows}
check("the failed ticker has no value", rows3["VAS.AX"].value is None)
check("the failed ticker has no weight", rows3["VAS.AX"].actual_weight is None)
equal("the total excludes it rather than counting it as zero", pm.total_value, 4250.0, 1e-9)
equal("it is named", pm.failed_tickers, ["VAS.AX"])
check("the email will say the figures are incomplete",
      any("VAS.AX" in w and "incomplete" in w for w in pm.warnings),
      f"warnings were {pm.warnings}")
check("rate limiting is called out by name",
      any("429" in w for w in pm.warnings), f"warnings were {pm.warnings}")

heading("first run: a snapshot, and it says so")
pf = build_portfolio(cfg, quotes, previous_prices={}, previous_run=None,
                     today=date(2026, 10, 3))
check("flagged as a first run", pf.first_run is True)
check("no week change anywhere", all(r.week_change_pct is None for r in pf.rows))
check("the email says it is a snapshot",
      any("position snapshot" in w for w in pf.warnings), f"warnings were {pf.warnings}")
check("nothing to explain on a first run", movers(pf, 3) == [])

heading("a long gap is disclosed rather than called a week")
pg = build_portfolio(cfg, quotes, previous_prices={"BHP.AX": 40.0},
                     previous_run=date(2026, 8, 1), today=date(2026, 10, 3))
check("says how long the gap really was",
      any("63 days ago" in w for w in pg.warnings), f"warnings were {pg.warnings}")

heading("movers respect the threshold")
pv = build_portfolio(cfg, quotes,
                     previous_prices={"BHP.AX": 38.0, "VAS.AX": 90.5, "ARG.AX": 10.0},
                     previous_run=date(2026, 9, 26), today=date(2026, 10, 3))
names = [r.ticker for r in movers(pv, 3)]
equal("only the holding past the threshold", names, ["BHP.AX"])

done()
