"""Holdings priced in holdings.yaml rather than fetched: cash, and unlisted
managed funds whose APIR codes no price feed knows.

What matters: they are never sent to yfinance, they count in full towards
value and weights, they never show or alert on a weekly move they cannot have,
an undated price is refused, and an old one is flagged without failing."""

from harness import check, done, equal, heading
import contextlib
import io
import os
import re
import tempfile
from datetime import date

import yaml

import main as entry
from monitor import prices as prices_mod
from monitor.alerts import evaluate
from monitor.compute import build_portfolio, movers
from monitor.config import AlertRule, ConfigLoader, load_config, parse_config
from monitor.prices import Quote, fetch_prices
from monitor.render import render_html, render_text

RUN = date(2026, 10, 3)

PORTFOLIO = """
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: VGS.AX, units: 10, target_weight: 30, asset_type: etf}
  - {ticker: VAN0003AU, units: 1000, target_weight: 20, asset_type: managed_fund,
     price: 2.5, price_as_of: 2026-10-01}
  - {ticker: CASH, units: 5000, target_weight: 50, asset_type: cash,
     price: 1.00, price_as_of: 2026-10-03}
targets: {tolerate_unallocated_pct: 0}
alerts:
  - {name: Big weekly move, type: price_move, abs_change_pct: 10}
"""


def cfg(text=PORTFOLIO):
    # The same loader load_config uses, so dates arrive as text, as they do
    # in a real run.
    return parse_config(yaml.load(text, Loader=ConfigLoader))


def holding_row(html, ticker):
    """The cells of one holding's table row, tags stripped."""
    for row in re.findall(r"<tr>(.*?)</tr>", html):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
        if cells and f"<strong>{ticker}</strong>" in cells[0]:
            return [re.sub(r"<[^>]+>", " ", c) for c in cells]
    raise AssertionError(f"no row for {ticker}")


heading("config: a `price` makes a holding manual, and it needs a date")
c = cfg()
equal("no complaints", c.config_errors, [])
equal("only the listed holding is fetched", c.fetch_tickers, ["VGS.AX"])
equal("but every holding is still a holding", c.tickers, ["VGS.AX", "VAN0003AU", "CASH"])
cash = c.holdings[2]
check("CASH is manual", cash.manual_price and cash.price == 1.0)
equal("the YAML date becomes a date", cash.price_as_of, date(2026, 10, 3))
c = parse_config(yaml.safe_load(PORTFOLIO))
equal("and so does one PyYAML already parsed", c.holdings[2].price_as_of, date(2026, 10, 3))
equal("stale_price_days defaults to 14", c.stale_price_days, 14.0)

c = cfg(PORTFOLIO.replace("price: 1.00, price_as_of: 2026-10-03", "price: 1.00"))
equal("a price with no price_as_of is one error", len(c.config_errors), 1)
check("naming the holding", "CASH" in c.config_errors[0], c.config_errors[0])
check("naming the missing field", "price_as_of" in c.config_errors[0], c.config_errors[0])
check("saying the price is refused, not used",
      "refused" in c.config_errors[0] and "excluded from the total" in c.config_errors[0],
      c.config_errors[0])
check("the holding survives so it can be named in the table",
      "CASH" in c.tickers and c.holdings[2].price is None)
check("and is still never fetched", "CASH" not in c.fetch_tickers, str(c.fetch_tickers))

for bad, needle in [
    ("price: 1.00, price_as_of: last tuesday", "not an ISO date"),
    ("price: 1.00, price_as_of: 2026-13-45", "not an ISO date"),
    ("price: lots, price_as_of: 2026-10-03", "CASH.price"),
    ("price: 0, price_as_of: 2026-10-03", "above zero"),
    ("price: -1, price_as_of: 2026-10-03", "above zero"),
    ("price: .nan, price_as_of: 2026-10-03", "above zero"),
    ("price: null, price_as_of: 2026-10-03", "CASH.price"),
]:
    c = cfg(PORTFOLIO.replace("price: 1.00, price_as_of: 2026-10-03", bad))
    check(f"{bad!r}: reported", any(needle in e and "CASH" in e for e in c.config_errors),
          str(c.config_errors))
    check(f"{bad!r}: no price is used", c.holdings[2].price is None)
    check(f"{bad!r}: still not fetched", "CASH" not in c.fetch_tickers)

bad_file = os.path.join(tempfile.mkdtemp(), "bad-date.yaml")
with open(bad_file, "w") as fh:
    fh.write(PORTFOLIO.replace("2026-10-03", "2026-13-45"))
try:
    c = load_config(bad_file)
    check("an impossible date in the file does not crash the loader", True)
except Exception as exc:  # PyYAML raises ValueError on these if left to itself
    check("an impossible date in the file does not crash the loader", False, repr(exc))
check("it is reported against the holding",
      any("CASH" in e and "2026-13-45" in e for e in c.config_errors), str(c.config_errors))

c = cfg(PORTFOLIO.replace("targets: {tolerate_unallocated_pct: 0}",
                          "targets: {tolerate_unallocated_pct: 0, stale_price_days: 30}"))
equal("stale_price_days is read", c.stale_price_days, 30.0)
c = cfg(PORTFOLIO.replace("targets: {tolerate_unallocated_pct: 0}",
                          "targets: {tolerate_unallocated_pct: 0, stale_price_days: soon}"))
check("a non-numeric stale_price_days is reported",
      any("stale_price_days" in e for e in c.config_errors), str(c.config_errors))
equal("and falls back to 14", c.stale_price_days, 14.0)

heading("the fetcher is never asked about a manual-price holding")
asked = []


def recorder(ticker):
    asked.append(ticker)
    raise RuntimeError("HTTP Error 429: Too Many Requests")


c = cfg()
fetch_prices(c.fetch_tickers, fetcher=recorder, pause=0)
equal("asked about VGS.AX alone", asked, ["VGS.AX"])

asked.clear()
original = prices_mod._yf_history
prices_mod._yf_history = recorder
path = os.path.join(tempfile.mkdtemp(), "h.yaml")
with open(path, "w") as fh:
    fh.write(PORTFOLIO)
out, err = io.StringIO(), io.StringIO()
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = entry.main(["--dry-run", "--config", path,
                           "--state", os.path.join(os.path.dirname(path), "s.json")])
finally:
    prices_mod._yf_history = original
equal("through main.py too, yfinance saw VGS.AX alone", asked, ["VGS.AX"])
equal("and the run succeeded", code, 0)
check("only VGS.AX is reported as a failed fetch",
      "PRICE FETCH FAILED — VGS.AX" in err.getvalue()
      and "FAILED — CASH" not in err.getvalue()
      and "FAILED — VAN0003AU" not in err.getvalue(), err.getvalue())

heading("value and weights: a manual price counts in full")
# VGS 10 @ 150 = 1,500; VAN0003AU 1,000 @ 2.50 = 2,500; CASH 5,000 @ 1 = 5,000.
# Total 9,000. Weights 16.67 / 27.78 / 55.56.
c = cfg()
quotes = {"VGS.AX": Quote("VGS.AX", price=150.0)}
p = build_portfolio(c, quotes, previous_prices={}, previous_run=date(2026, 9, 26), today=RUN)
rows = {r.ticker: r for r in p.rows}
equal("total includes cash and the fund", p.total_value, 9000.0, 1e-9)
equal("CASH value", rows["CASH"].value, 5000.0, 1e-9)
equal("CASH weight", rows["CASH"].actual_weight, 5000 / 9000 * 100, 1e-9)
equal("fund weight", rows["VAN0003AU"].actual_weight, 2500 / 9000 * 100, 1e-9)
equal("fund drift", rows["VAN0003AU"].drift_pp, 2500 / 9000 * 100 - 20, 1e-9)
equal("nothing reported as missing", p.failed_tickers, [])
check("rows know they are manual", rows["CASH"].fixed_price and not rows["VGS.AX"].fixed_price)

heading("no weekly change, ever — not even with a stored price to compare")
# Simulate a state.json that somehow holds a price for CASH and the fund.
p = build_portfolio(c, quotes,
                    previous_prices={"VGS.AX": 100.0, "CASH": 0.5, "VAN0003AU": 2.5},
                    previous_run=date(2026, 9, 26), today=RUN)
rows = {r.ticker: r for r in p.rows}
equal("the listed holding does move", rows["VGS.AX"].week_change_pct, 50.0, 1e-9)
check("CASH has no week change (not +100%)", rows["CASH"].week_change_pct is None)
check("the fund has no week change (not 0%)", rows["VAN0003AU"].week_change_pct is None)
equal("only the listed holding is a mover", [r.ticker for r in movers(p, 3)], ["VGS.AX"])

heading("price_move never fires on a manual price, and says why")
report = evaluate(c, p)
equal("only VGS.AX triggers", [t.ticker for t in report.triggered], ["VGS.AX"])
skipped = {n.ticker: n.reason for n in report.not_evaluated}
check("CASH is reported as not evaluated, by name", "CASH" in skipped, str(skipped))
check("the fund too", "VAN0003AU" in skipped, str(skipped))
check("with the reason", "set by hand" in skipped["CASH"], skipped["CASH"])

level = c.alerts + [AlertRule("Cash floor", "price_level", ["CASH"], {"below": 2})]
c.alerts = level
report = evaluate(c, p)
floor = [t for t in report.triggered if t.rule_name == "Cash floor"]
check("price_level still works against a manual price", len(floor) == 1)
check("and its arithmetic says the price was manual, not a close",
      "manual price" in floor[0].arithmetic and "2026-10-03" in floor[0].arithmetic,
      floor[0].arithmetic)
c.alerts = level[:1]

heading("the email: '—' for the week, never 0.00%, and the date shown")
html = render_html(c, p, evaluate(c, p), None, RUN)
text = render_text(c, p, evaluate(c, p), None, RUN)
for ticker in ("CASH", "VAN0003AU"):
    cells = holding_row(html, ticker)
    equal(f"{ticker}: week column is a dash", cells[2].strip(), "—")
    check(f"{ticker}: no percentage in the week column", "%" not in cells[2], cells[2])
check("the listed holding's week still shows", "+50.0%" in holding_row(html, "VGS.AX")[2])
check("the fund's date is beside it", "as of 2026-10-01" in holding_row(html, "VAN0003AU")[0])
check("cash's date is beside it", "as of 2026-10-03" in holding_row(html, "CASH")[0])
check("labelled as set by hand", "set by hand" in holding_row(html, "CASH")[0])
equal("a fund price keeps its precision", holding_row(html, "VAN0003AU")[1].strip(), "2.50")
check("the footer explains manual prices", "not fetched at all" in html)
cash_line = next(line for line in text.splitlines() if line.startswith("CASH"))
check("text: CASH week is a dash", "week       —" in cash_line, cash_line)
check("text: no 0.0% anywhere for a manual holding", "+0.0%" not in text and "0.00%" not in text)
check("text: the date is shown", "set by hand, as of 2026-10-03" in text)

heading("staleness: a warning, at the boundary, and never a failure")
p14 = build_portfolio(c, quotes, previous_prices={}, previous_run=date(2026, 10, 10),
                      today=date(2026, 10, 15))  # fund priced 2026-10-01: exactly 14 days
r14 = {r.ticker: r for r in p14.rows}
equal("age is counted in days", r14["VAN0003AU"].price_age_days, 14)
check("14 days is not stale at a 14-day limit", r14["VAN0003AU"].price_stale is False)
check("no warning yet", not any("older than" in w for w in p14.warnings), str(p14.warnings))

p15 = build_portfolio(c, quotes, previous_prices={}, previous_run=date(2026, 10, 10),
                      today=date(2026, 10, 16))
r15 = {r.ticker: r for r in p15.rows}
check("15 days is stale", r15["VAN0003AU"].price_stale is True)
check("cash at 13 days is not", r15["CASH"].price_stale is False)
stale = [w for w in p15.warnings if "older than 14 days" in w]
equal("one warning", len(stale), 1)
check("naming the holding, its date and its age",
      "VAN0003AU" in stale[0] and "2026-10-01" in stale[0] and "15 days ago" in stale[0],
      stale[0])
check("and not cash, which is still fresh", "CASH" not in stale[0], stale[0])
check("the stale holding still counts in full", p15.total_value == 9000.0)
html = render_html(c, p15, evaluate(c, p15), None, date(2026, 10, 16))
check("the row says it is stale", "stale, 15 days old" in holding_row(html, "VAN0003AU")[0])
check("the warning is an amber box, not a red one",
      re.search(r'<p style="background:#fff4e5[^"]*">A manual price', html) is not None)

stale_cfg = PORTFOLIO.replace("2026-10-01", "2025-01-01")
with open(path, "w") as fh:
    fh.write(stale_cfg)
out, err = io.StringIO(), io.StringIO()
original_fetch = entry.prices.fetch_prices
entry.prices.fetch_prices = lambda tickers, **kw: {t: Quote(t, price=150.0) for t in tickers}
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = entry.main(["--dry-run", "--config", path,
                           "--state", os.path.join(os.path.dirname(path), "s.json")])
finally:
    entry.prices.fetch_prices = original_fetch
equal("a stale price does not fail the run", code, 0)
check("the email went out with the warning in it",
      "<div" in out.getvalue() and "older than 14 days" in out.getvalue())
check("and it is not a config problem", "CONFIG" not in err.getvalue(), err.getvalue())

heading("an undated price is named in the email, not quietly dropped")
with open(path, "w") as fh:
    fh.write(PORTFOLIO.replace("price: 1.00, price_as_of: 2026-10-03", "price: 1.00"))
out, err = io.StringIO(), io.StringIO()
entry.prices.fetch_prices = lambda tickers, **kw: {t: Quote(t, price=150.0) for t in tickers}
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = entry.main(["--dry-run", "--config", path,
                           "--state", os.path.join(os.path.dirname(path), "s.json")])
finally:
    entry.prices.fetch_prices = original_fetch
out, err = out.getvalue(), err.getvalue()
equal("the email still goes", code, 0)
check("stderr names CASH as a config problem",
      "CONFIG PROBLEM — CASH" in err and "price_as_of" in err, err)
check("the email lists it under holdings.yaml problems",
      "holdings.yaml needs attention" in out and "CASH: has a `price` but no `price_as_of`" in out)
check("the total says it excludes CASH", "excludes CASH" in out)
check("the row is marked as having no price", "no price" in holding_row(out, "CASH")[0])

done()
