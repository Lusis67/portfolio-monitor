"""End to end, offline: the dry run, and what happens when pieces fail."""

from harness import check, done, equal, heading
import contextlib
import io
import json
import os
import tempfile

import main as entry
from monitor import explain as explain_mod
from monitor.prices import Quote

tmp = tempfile.mkdtemp()


def fake_prices(prices):
    def fetch(tickers, **kw):
        return {
            t: (Quote(t, price=prices[t]) if prices.get(t) is not None
                else Quote(t, error="HTTP Error 429: Too Many Requests", rate_limited=True))
            for t in tickers
        }
    return fetch


def run(argv, prices, explain=None):
    original_fetch = entry.prices.fetch_prices
    original_explain = entry.explain_mod.explain
    entry.prices.fetch_prices = fake_prices(prices)
    if explain is not None:
        entry.explain_mod.explain = explain
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = entry.main(argv)
    finally:
        entry.prices.fetch_prices = original_fetch
        entry.explain_mod.explain = original_explain
    return code, out.getvalue(), err.getvalue()


heading("the dry run needs no credentials at all")
for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "ANTHROPIC_API_KEY"):
    os.environ.pop(var, None)
state_path = os.path.join(tmp, "state.json")
code, out, err = run(
    ["--dry-run", "--state", state_path],
    {"BHP.AX": 61.21, "VAS.AX": 107.52},
)
equal("exit 0", code, 0)
check("HTML went to stdout", out.strip().startswith("<div") and "</div>" in out)
check("it is the committed portfolio", "LeLusis" in out)
check("nothing was sent", "DRY RUN — nothing sent" in err)
check("no state file was written", not os.path.exists(state_path))
check("the unconfigured portfolio is explained, not reported as zero dollars",
      "nothing is configured yet" in out)
check("no Claude call was attempted — nothing moved",
      "ANTHROPIC" not in err.upper())

heading("a missing price survives all the way into the email")
code, out, err = run(["--dry-run", "--state", state_path],
                     {"BHP.AX": 61.21, "VAS.AX": None})
equal("exit 0 — the email is still the product", code, 0)
check("stderr names the ticker", "PRICE FETCH FAILED — VAS.AX" in err, err)
check("stderr names the 429", "429" in err)
check("the email names it too", "VAS.AX" in out and "incomplete, not small" in out)

heading("a truncated brief aborts instead of sending")


def truncated(*a, **kw):
    raise explain_mod.ExplanationTruncated("the brief hit max_tokens")


seeded = os.path.join(tmp, "seeded.json")
json.dump({"schema": 1, "last_run": "2026-09-26",
           "prices": {"BHP.AX": 40.0, "VAS.AX": 100.0}}, open(seeded, "w"))
code, out, err = run(["--dry-run", "--state", seeded],
                     {"BHP.AX": 61.21, "VAS.AX": 107.52}, explain=truncated)
equal("exit 3", code, 3)
check("nothing was rendered", "<div" not in out, out[:200])
check("and it says why", "ABORTED" in err and "max_tokens" in err, err)

heading("an unreachable API costs the notes, not the email")


def unavailable(*a, **kw):
    raise explain_mod.ExplanationUnavailable("APIConnectionError: no route to host")


code, out, err = run(["--dry-run", "--state", seeded],
                     {"BHP.AX": 61.21, "VAS.AX": 107.52}, explain=unavailable)
equal("exit 0", code, 0)
check("the email went out", "<div" in out)
check("with the numbers intact", "61.21" in out)
check("and a warning in place of the notes",
      "could not be reached" in out, out[:400])

heading("the explanation is only requested when something actually moved")
calls = []


def record(rows, portfolio, threshold, **kw):
    calls.append([r.ticker for r in rows])
    raise explain_mod.ExplanationUnavailable("stopped here on purpose")


run(["--dry-run", "--state", seeded], {"BHP.AX": 40.0, "VAS.AX": 100.0}, explain=record)
equal("flat week: no call", calls, [])
run(["--dry-run", "--state", seeded], {"BHP.AX": 61.21, "VAS.AX": 100.0}, explain=record)
equal("one mover: called once, with only the mover", calls, [["BHP.AX"]])

heading("week-on-week arithmetic lands in the email")
check("+53.0% from 40.00 to 61.21 is shown",
      "+53.0%" in run(["--dry-run", "--state", seeded],
                      {"BHP.AX": 61.21, "VAS.AX": 100.0}, explain=unavailable)[1])

heading("a real send without credentials fails cleanly, and says how to look anyway")
code, out, err = run(["--state", os.path.join(tmp, "unused.json")],
                     {"BHP.AX": 61.21, "VAS.AX": 107.52})
equal("exit 1", code, 1)
check("names the missing secrets", "GMAIL_ADDRESS" in err and "GMAIL_APP_PASSWORD" in err)
check("points at --dry-run", "--dry-run" in err)
check("no state was written on a failed send",
      not os.path.exists(os.path.join(tmp, "unused.json")))

done()
