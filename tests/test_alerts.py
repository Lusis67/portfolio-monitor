"""Every alert type, triggering and — the part that matters — not triggering
exactly at its boundary."""

from harness import check, done, equal, heading
from datetime import date

from monitor.alerts import evaluate
from monitor.compute import build_portfolio
from monitor.config import AlertRule, Config, Holding
from monitor.prices import Quote


def portfolio_with(prices, units=(100, 100), prev=None, targets=(50, 50)):
    cfg = Config(
        portfolio_name="T", currency="AUD",
        holdings=[
            Holding("BHP.AX", units[0], targets[0], "equity"),
            Holding("VAS.AX", units[1], targets[1], "etf"),
        ],
        alerts=[], tolerate_unallocated_pct=0, also_to=[], note_threshold_pct=3,
    )
    quotes = {
        t: (Quote(t, price=p) if p is not None else Quote(t, error="no data"))
        for t, p in prices.items()
    }
    return cfg, build_portfolio(cfg, quotes, previous_prices=prev or {},
                                previous_run=date(2026, 9, 26), today=date(2026, 10, 3))


def with_rules(cfg, *rules):
    cfg.alerts = list(rules)
    return cfg


heading("weight_drift")
# 100 @ 60 = 6,000 and 100 @ 40 = 4,000 → 60% / 40% against targets of 50/50,
# so drift is exactly ±10pp.
cfg, p = portfolio_with({"BHP.AX": 60.0, "VAS.AX": 40.0})
rule = AlertRule("Drifted", "weight_drift", None, {"exceeds_pct_points": 5})
report = evaluate(with_rules(cfg, rule), p)
equal("two holdings at ±10pp both trigger past 5pp", len(report.triggered), 2)
check("the arithmetic is shown",
      "10.0pp" in report.triggered[0].arithmetic and "5.0pp" in report.triggered[0].arithmetic,
      report.triggered[0].arithmetic)

boundary = AlertRule("Drifted", "weight_drift", None, {"exceeds_pct_points": 10})
equal("exactly 10pp does NOT trigger a >10pp rule",
      len(evaluate(with_rules(cfg, boundary), p).triggered), 0)
just_under = AlertRule("Drifted", "weight_drift", None, {"exceeds_pct_points": 9.999})
equal("9.999pp does trigger", len(evaluate(with_rules(cfg, just_under), p).triggered), 2)

narrowed = AlertRule("Drifted", "weight_drift", ["BHP.AX"], {"exceeds_pct_points": 5})
r = evaluate(with_rules(cfg, narrowed), p)
equal("applies_to narrows the rule", [t.ticker for t in r.triggered], ["BHP.AX"])

heading("price_move")
cfg, p = portfolio_with({"BHP.AX": 110.0, "VAS.AX": 95.0},
                        prev={"BHP.AX": 100.0, "VAS.AX": 100.0})
rule = AlertRule("Big move", "price_move", None, {"abs_change_pct": 5})
r = evaluate(with_rules(cfg, rule), p)
equal("+10% trips a 5% rule, -5% exactly does not",
      [t.ticker for t in r.triggered], ["BHP.AX"])
check("the arithmetic is shown",
      "+10.0%" in r.triggered[0].arithmetic, r.triggered[0].arithmetic)

exact = AlertRule("Big move", "price_move", None, {"abs_change_pct": 10})
equal("exactly +10% does NOT trigger a ±10% rule",
      len(evaluate(with_rules(cfg, exact), p).triggered), 0)

cfg, pd = portfolio_with({"BHP.AX": 89.0, "VAS.AX": 100.0},
                         prev={"BHP.AX": 100.0, "VAS.AX": 100.0})
r = evaluate(with_rules(cfg, AlertRule("Big move", "price_move", None,
                                       {"abs_change_pct": 10})), pd)
equal("-11% trips a ±10% rule", [t.ticker for t in r.triggered], ["BHP.AX"])

heading("price_level")
cfg, p = portfolio_with({"BHP.AX": 34.99, "VAS.AX": 90.0})
rule = AlertRule("Below my line", "price_level", ["BHP.AX"], {"below": 35.0})
r = evaluate(with_rules(cfg, rule), p)
equal("34.99 is below 35.00", [t.ticker for t in r.triggered], ["BHP.AX"])
check("the arithmetic is shown", "34.99" in r.triggered[0].arithmetic)

cfg, pe = portfolio_with({"BHP.AX": 35.0, "VAS.AX": 90.0})
equal("exactly 35.00 does NOT trigger a below-35 rule",
      len(evaluate(with_rules(cfg, rule), pe).triggered), 0)

above = AlertRule("Above", "price_level", ["VAS.AX"], {"above": 90.0})
equal("exactly 90.00 does NOT trigger an above-90 rule",
      len(evaluate(with_rules(cfg, above), pe).triggered), 0)
cfg, pa = portfolio_with({"BHP.AX": 35.0, "VAS.AX": 90.01})
equal("90.01 does", len(evaluate(with_rules(cfg, above), pa).triggered), 1)

heading("a rule that cannot be checked is reported, never dropped")
cfg, pn = portfolio_with({"BHP.AX": None, "VAS.AX": 40.0})
r = evaluate(with_rules(
    cfg,
    AlertRule("Drifted", "weight_drift", ["BHP.AX"], {"exceeds_pct_points": 5}),
    AlertRule("Big move", "price_move", ["BHP.AX"], {"abs_change_pct": 5}),
    AlertRule("Below", "price_level", ["BHP.AX"], {"below": 35.0}),
), pn)
equal("no false triggers off a missing price", len(r.triggered), 0)
equal("all three are reported as unevaluated", len(r.not_evaluated), 3)
check("and the reason names the missing price",
      all("no price" in ne.reason for ne in r.not_evaluated),
      str([ne.reason for ne in r.not_evaluated]))

cfg, pz = portfolio_with({"BHP.AX": 40.0, "VAS.AX": 40.0}, units=(0, 0), targets=(0, 0))
r = evaluate(with_rules(cfg, AlertRule("Drifted", "weight_drift", None,
                                       {"exceeds_pct_points": 5})), pz)
equal("a zero-unit portfolio triggers nothing", len(r.triggered), 0)
equal("and says why instead", len(r.not_evaluated), 2)

heading("a first run cannot produce a price_move alert out of nothing")
cfg = Config("T", "AUD", [Holding("BHP.AX", 10, 100, "equity")], [], 0, [], 3)
pfirst = build_portfolio(cfg, {"BHP.AX": Quote("BHP.AX", price=40.0)},
                         previous_prices={}, previous_run=None, today=date(2026, 10, 3))
r = evaluate(with_rules(cfg, AlertRule("Big move", "price_move", None,
                                       {"abs_change_pct": 1})), pfirst)
equal("nothing triggered", len(r.triggered), 0)
check("reported as unevaluated with a reason",
      len(r.not_evaluated) == 1 and "previous run" in r.not_evaluated[0].reason,
      str(r.not_evaluated))

done()
