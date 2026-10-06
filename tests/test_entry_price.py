"""Entry price, and the all-time P/L built on it.

What matters: the arithmetic is right; a holding with no entry price shows
nothing rather than a zero; the portfolio total covers only what it can and
names what it left out; a hand-priced holding (cash, an APIR code) gets a P/L
like any other; and the email never implies the number includes income it does
not include."""

from harness import check, done, equal, heading
from datetime import date

import yaml

from monitor.compute import build_portfolio
from monitor.config import parse_config
from monitor.prices import Quote
from monitor.render import render_html, render_text

RUN = date(2026, 10, 6)

PORTFOLIO = """
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: WIN.AX, units: 100, target_weight: 25, asset_type: equity,
     entry_price: 4.00}
  - {ticker: LOSS.AX, units: 200, target_weight: 25, asset_type: equity,
     entry_price: 10.00}
  - {ticker: NOENTRY.AX, units: 50, target_weight: 25, asset_type: equity}
  - {ticker: CASH, units: 1000, target_weight: 25, asset_type: cash,
     price: 1.00, price_as_of: 2026-10-06, entry_price: 1.00}
targets: {tolerate_unallocated_pct: 0}
alerts: []
"""

QUOTES = {
    "WIN.AX": Quote(ticker="WIN.AX", price=5.00),
    "LOSS.AX": Quote(ticker="LOSS.AX", price=8.00),
    "NOENTRY.AX": Quote(ticker="NOENTRY.AX", price=3.00),
}

cfg = parse_config(yaml.safe_load(PORTFOLIO))
pf = build_portfolio(cfg, QUOTES, previous_prices={}, previous_run=None, today=RUN)
by = {r.ticker: r for r in pf.rows}

heading("the arithmetic")
equal("a gain in dollars", by["WIN.AX"].pl_abs, 100.0)          # 100 * (5 - 4)
equal("a gain in percent", round(by["WIN.AX"].pl_pct, 4), 25.0)
equal("a loss in dollars", by["LOSS.AX"].pl_abs, -400.0)        # 200 * (8 - 10)
equal("a loss in percent", round(by["LOSS.AX"].pl_pct, 4), -20.0)
equal("cost base is units times entry", by["WIN.AX"].cost_base, 400.0)

heading("a missing entry price is absent, not zero")
check("no P/L in dollars", by["NOENTRY.AX"].pl_abs is None)
check("no P/L in percent", by["NOENTRY.AX"].pl_pct is None)
check("no cost base", by["NOENTRY.AX"].cost_base is None)
check("it is still valued and weighted", by["NOENTRY.AX"].value == 150.0)
check("and it is named as uncovered", "NOENTRY.AX" in pf.pl_missing)

heading("a hand-priced holding gets a P/L like any other")
equal("cash bought at par has none to make", by["CASH"].pl_abs, 0.0)
check("and is not listed as uncovered", "CASH" not in pf.pl_missing)

heading("the portfolio total covers what it can, and says so")
equal("cost base sums the covered rows", pf.total_cost_base, 400.0 + 2000.0 + 1000.0)
equal("P/L sums the covered rows", pf.total_pl_abs, 100.0 - 400.0 + 0.0)
equal("percent is against cost, not value", round(pf.total_pl_pct, 4), round(-300.0 / 3400.0 * 100, 4))
equal("exactly the uncovered holdings are named", pf.pl_missing, ["NOENTRY.AX"])

heading("an unusable entry price is refused, and the holding survives")
bad = parse_config(yaml.safe_load("""
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: A.AX, units: 10, target_weight: 100, entry_price: 0}
targets: {tolerate_unallocated_pct: 0}
alerts: []
"""))
check("the error names the holding", any("A.AX" in e and "entry_price" in e for e in bad.config_errors))
check("and says what it costs", any("left out of the total" in e for e in bad.config_errors))
equal("the entry price is dropped", bad.holdings[0].entry_price, None)
equal("but the holding is still there", bad.holdings[0].units, 10.0)

notnum = parse_config(yaml.safe_load("""
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: B.AX, units: 10, target_weight: 100, entry_price: "abt four dollars"}
targets: {tolerate_unallocated_pct: 0}
alerts: []
"""))
check("a non-number is reported too", any("B.AX.entry_price" in e for e in notnum.config_errors))
equal("and dropped", notnum.holdings[0].entry_price, None)

heading("an unpriced holding has no P/L even with an entry price")
nopx = build_portfolio(
    cfg,
    {"WIN.AX": Quote(ticker="WIN.AX", error="feed failed")},
    previous_prices={}, previous_run=None, today=RUN,
)
win = {r.ticker: r for r in nopx.rows}["WIN.AX"]
check("no P/L without a current price", win.pl_abs is None)
check("and it is named as uncovered", "WIN.AX" in nopx.pl_missing)

heading("the email")
html = render_html(cfg, pf, None, None, RUN, None, None)
text = render_text(cfg, pf, None, None, RUN, None, None)
check("the entry price is shown", "from 4.00" in html)
check("the gain is shown", "+25.0%" in html)
check("the loss is shown", "-20.0%" in html)
check("the portfolio P/L is shown", "All-time" in html)
check("what it excludes is named", "NOENTRY.AX" in html and "no entry price" in html)
check("income is not implied", "does not count" in html and "distributions" in html)
check("still five columns", html.count("<th ") == 5)
check("text carries the entry price", "entry 4.00" in text)
check("text carries the caveat", "excludes distributions" in text)

heading("no entry prices at all")
none_cfg = parse_config(yaml.safe_load("""
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: C.AX, units: 10, target_weight: 100}
targets: {tolerate_unallocated_pct: 0}
alerts: []
"""))
none_pf = build_portfolio(none_cfg, {"C.AX": Quote(ticker="C.AX", price=2.0)},
                          previous_prices={}, previous_run=None, today=RUN)
check("no total is invented", none_pf.total_pl_abs is None)
none_html = render_html(none_cfg, none_pf, None, None, RUN, None, None)
check("the email says why instead", "no all-time p/l" in none_html.lower())
check("and the income caveat is not shown", "does not count" not in none_html)

done()
