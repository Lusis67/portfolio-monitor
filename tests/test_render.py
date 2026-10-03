"""The email itself: loud about what's missing, legible on a phone."""

from harness import check, done, heading
from datetime import date

from monitor.alerts import evaluate
from monitor.compute import build_portfolio
from monitor.config import AlertRule, Config, Holding
from monitor.prices import Quote
from monitor.render import render_html, render_text, subject

RUN = date(2026, 10, 3)


def scenario(quotes, prev=None, prev_run=date(2026, 9, 26), units=(100, 100),
             targets=(50, 50), errors=None):
    cfg = Config("LeLusis", "AUD",
                 [Holding("BHP.AX", units[0], targets[0], "equity"),
                  Holding("VAS.AX", units[1], targets[1], "etf")],
                 [AlertRule("Drifted", "weight_drift", None, {"exceeds_pct_points": 5})],
                 0, [], 3)
    cfg.config_errors = errors or []
    p = build_portfolio(cfg, quotes, previous_prices=prev or {}, previous_run=prev_run,
                        today=RUN)
    return cfg, p, evaluate(cfg, p)


heading("a missing price is loud in both parts of the email")
cfg, p, alerts = scenario({
    "BHP.AX": Quote("BHP.AX", price=40.0),
    "VAS.AX": Quote("VAS.AX", error="HTTP Error 429: Too Many Requests", rate_limited=True),
})
html = render_html(cfg, p, alerts, None, RUN)
text = render_text(cfg, p, alerts, None, RUN)
check("HTML names the ticker that failed", "VAS.AX" in html)
check("HTML says the figures are incomplete", "incomplete, not small" in html)
check("HTML says the total excludes it", "excludes VAS.AX" in html)
check("HTML never shows a fabricated 0.00 for it",
      "<td style=\"padding:8px 4px;border-bottom:1px solid #e6e6e6;text-align:right;"
      "font-size:14px\">0.00</td>" not in html)
check("HTML marks the row", "no price" in html)
check("text names it", "no price" in text and "VAS.AX" in text)
check("text says the total excludes it", "excludes VAS.AX" in text)
check("rate limiting is named in the subject",
      "price(s) missing" in subject(cfg, p, alerts, RUN),
      subject(cfg, p, alerts, RUN))

heading("a first run says it is a snapshot")
cfg, p, alerts = scenario({"BHP.AX": Quote("BHP.AX", price=40.0),
                           "VAS.AX": Quote("VAS.AX", price=40.0)}, prev_run=None)
html = render_html(cfg, p, alerts, None, RUN)
text = render_text(cfg, p, alerts, None, RUN)
check("HTML says snapshot", "position snapshot" in html)
check("HTML shows no invented week change", "+0.0%" not in html and "-0.0%" not in html)
check("text says snapshot", "position snapshot" in text)
check("the subject says so", "first run, snapshot" in subject(cfg, p, alerts, RUN))

heading("config problems reach the reader, not just the log")
cfg, p, alerts = scenario({"BHP.AX": Quote("BHP.AX", price=40.0),
                           "VAS.AX": Quote("VAS.AX", price=40.0)},
                          errors=["alert 'Moon' : unknown type 'moon_phase' — NOT running"])
html = render_html(cfg, p, alerts, None, RUN)
check("HTML carries the config error", "moon_phase" in html)
check("under a heading that will be noticed", "holdings.yaml needs attention" in html)
check("text carries it", "moon_phase" in render_text(cfg, p, alerts, None, RUN))

heading("phone legibility")
cfg, p, alerts = scenario({"BHP.AX": Quote("BHP.AX", price=40.0),
                           "VAS.AX": Quote("VAS.AX", price=60.0)},
                          prev={"BHP.AX": 38.0, "VAS.AX": 60.0})
html = render_html(cfg, p, alerts, None, RUN)
check("no external assets", "<img" not in html and "http://" not in html
      and "<link" not in html and "<script" not in html)
check("no <style> block — several clients strip it", "<style" not in html)
check("the table is fluid, not fixed-pixel", 'style="width:100%' in html)
check("exactly five columns, which fit a phone", html.count("<th ") == 5)
check("no pixel widths anywhere in the table",
      "width:3" not in html.split("</table>")[0].replace("width:30%", ""))
check("the body is capped at a readable measure", "max-width:640px" in html)
check("every cell style is inline", html.count("style=") > 10)

heading("alerts show their arithmetic")
check("the trigger is there", "Drifted" in html)
check("with the sum behind it", "pp, which exceeds" in html, html[:0] or "")
text = render_text(cfg, p, alerts, None, RUN)
check("and in the plain-text part too", "which exceeds" in text)

heading("nothing moved")
cfg, p, alerts = scenario({"BHP.AX": Quote("BHP.AX", price=40.0),
                           "VAS.AX": Quote("VAS.AX", price=40.0)},
                          prev={"BHP.AX": 40.0, "VAS.AX": 40.0})
html = render_html(cfg, p, alerts, None, RUN)
check("says so rather than leaving a blank space",
      "Nothing moved more than 3%" in html, html)

heading("model text is escaped, never injected")


class FakeExplanation:
    notes = {"BHP.AX": "<script>alert(1)</script> & co"}
    leftover = ""
    search_errors = []
    degraded = False


html = render_html(cfg, p, alerts, FakeExplanation(), RUN)
check("script tags are escaped", "<script>alert(1)" not in html)
check("but the text is still there", "&lt;script&gt;" in html)

done()
