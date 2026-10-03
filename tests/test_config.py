"""Config validation. The rule that matters: a rule the owner thinks is
running and isn't is worse than no rule, so nothing is ever skipped quietly."""

from harness import check, done, equal, heading

import yaml

from monitor.config import ConfigError, load_config, parse_config

BASE = """
portfolio: {name: T, currency: AUD}
holdings:
  - {ticker: BHP.AX, units: 10, target_weight: 60, asset_type: equity}
  - {ticker: VAS.AX, units: 5, target_weight: 40, asset_type: etf}
email: {also_to: [], note_threshold_pct: 3}
targets: {tolerate_unallocated_pct: 0}
alerts:
"""


def cfg(alerts_yaml=""):
    return parse_config(yaml.safe_load(BASE + alerts_yaml))


heading("an unknown alert type is reported by name")
c = cfg("""
  - {name: My invented rule, type: moon_phase, threshold: 3}
""")
equal("the rule is not quietly accepted", len(c.alerts), 0)
equal("one error", len(c.config_errors), 1)
check("names the rule", "My invented rule" in c.config_errors[0], c.config_errors[0])
check("names the bad type", "moon_phase" in c.config_errors[0], c.config_errors[0])
check("says it is not running", "NOT running" in c.config_errors[0], c.config_errors[0])
check("lists what it could have been",
      "weight_drift" in c.config_errors[0], c.config_errors[0])

heading("a phase 2 type is reported as not built, not as a typo")
c = cfg("""
  - {name: Trading at a discount, type: premium_discount, beyond_abs_pct: 2}
  - {name: Announcements, type: price_sensitive_announcement}
""")
equal("neither rule runs", len(c.alerts), 0)
equal("both reported", len(c.config_errors), 2)
check("says phase 2", all("phase 2" in e for e in c.config_errors), str(c.config_errors))
check("says NOT running", all("NOT running" in e for e in c.config_errors),
      str(c.config_errors))

heading("a rule missing its parameter is reported, not run with a default")
for alerts_yaml, needle in [
    ("\n  - {name: A, type: weight_drift}\n", "exceeds_pct_points"),
    ("\n  - {name: B, type: price_move}\n", "abs_change_pct"),
    ("\n  - {name: C, type: price_level, applies_to: [BHP.AX]}\n", "below"),
    ("\n  - {name: D, type: weight_drift, exceeds_pct_points: lots}\n", "exceeds_pct_points"),
]:
    c = cfg(alerts_yaml)
    equal(f"{needle}: rule dropped", len(c.alerts), 0)
    check(f"{needle}: reported by name", len(c.config_errors) == 1
          and needle in c.config_errors[0], str(c.config_errors))

heading("a rule with no type at all")
c = cfg("\n  - {name: Nameless, exceeds_pct_points: 5}\n")
check("reported", len(c.config_errors) == 1 and "Nameless" in c.config_errors[0],
      str(c.config_errors))

heading("applies_to naming a ticker that isn't held")
c = cfg("\n  - {name: E, type: price_level, applies_to: [XYZ.AX], below: 5}\n")
equal("the rule still runs for whatever it does cover", len(c.alerts), 1)
check("but the dead reference is reported",
      any("XYZ.AX" in e for e in c.config_errors), str(c.config_errors))

heading("targets that do not add up are reported, never normalised")
c = parse_config(yaml.safe_load("""
portfolio: {name: T}
holdings:
  - {ticker: BHP.AX, units: 10, target_weight: 60}
  - {ticker: VAS.AX, units: 5, target_weight: 30}
targets: {tolerate_unallocated_pct: 0}
"""))
check("reported", any("sum to 90.0%" in e for e in c.config_errors), str(c.config_errors))
check("says nothing was normalised",
      any("normalised" in e for e in c.config_errors), str(c.config_errors))
equal("the targets are untouched", [h.target_weight for h in c.holdings], [60.0, 30.0])

c = parse_config(yaml.safe_load("""
portfolio: {name: T}
holdings:
  - {ticker: BHP.AX, units: 10, target_weight: 60}
  - {ticker: VAS.AX, units: 5, target_weight: 30}
targets: {tolerate_unallocated_pct: 10}
"""))
equal("a tolerance of 10pp accepts a 10pp gap", len(c.config_errors), 0)

heading("the committed holdings.yaml")
# Structural checks only. The owner edits units, prices and dates by hand, and
# none of that should be able to break the tests.
c = load_config("holdings.yaml")
equal("parses with no complaints", c.config_errors, [])
equal("two usable alert rules", [a.type for a in c.alerts], ["weight_drift", "price_move"])
check("is a real portfolio, not the unconfigured template", c.is_unconfigured is False)
equal("note threshold", c.note_threshold_pct, 3.0)
equal("stale_price_days is read from targets", c.stale_price_days, 14.0)
manual = {h.ticker for h in c.holdings if h.manual_price}
check("cash and both unlisted funds carry a manual price",
      {"CASH", "VAN0004AU", "VAN0003AU"} <= manual, str(manual))
check("every manual price is dated",
      all(h.price is not None and h.price_as_of is not None
          for h in c.holdings if h.manual_price))
check("none of them is ever fetched", not manual & set(c.fetch_tickers),
      str(c.fetch_tickers))
check("every other holding is", set(c.fetch_tickers) == set(c.tickers) - manual)
check("the fetched ones are all ASX listings", all(t.endswith(".AX") for t in c.fetch_tickers),
      str(c.fetch_tickers))

heading("nav_source is parsed but goes no further (phase 2)")
c = parse_config(yaml.safe_load("""
portfolio: {name: T}
holdings:
  - {ticker: BHP.AX, units: 10, target_weight: 50}
  - {ticker: VAS.AX, units: 5, target_weight: 50, nav_source: "https://example.invalid/nav"}
"""))
check("kept on the holding that has it", c.holdings[1].nav_source is not None)
check("absent on the one that doesn't", c.holdings[0].nav_source is None)

heading("an unusable file raises rather than guessing")
try:
    load_config("definitely-not-here.yaml")
    check("missing file raises", False)
except ConfigError:
    check("missing file raises ConfigError", True)

done()
