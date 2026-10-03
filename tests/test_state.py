"""state.json round trips, and fails softly."""

from harness import check, done, equal, heading
from datetime import date
import json
import os
import tempfile

from monitor.prices import Quote
from monitor.state import load_state, save_state

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "state.json")

heading("no file at all is a first run")
s = load_state(path)
check("no last_run", s.last_run is None)
equal("no prices", s.prices, {})
check("and not an error", s.load_error is None)

heading("round trip")
err = save_state({"BHP.AX": Quote("BHP.AX", price=40.125),
                  "VAS.AX": Quote("VAS.AX", error="429", rate_limited=True)},
                 date(2026, 10, 3), path)
check("saved", err is None, str(err))
s = load_state(path)
equal("last_run", s.last_run, date(2026, 10, 3))
equal("only the price that actually came back is stored", s.prices, {"BHP.AX": 40.125})
check("a failed ticker is not stored as zero", "VAS.AX" not in s.prices)
raw = json.load(open(path))
equal("schema is recorded", raw["schema"], 1)

heading("a later run keeps last week's price for a ticker that failed this week")
save_state({"BHP.AX": Quote("BHP.AX", error="429", rate_limited=True),
            "VAS.AX": Quote("VAS.AX", price=100.0)}, date(2026, 10, 10), path)
s = load_state(path)
equal("BHP keeps its old price rather than vanishing", s.prices["BHP.AX"], 40.125)
equal("VAS is new", s.prices["VAS.AX"], 100.0)
equal("date moved on", s.last_run, date(2026, 10, 10))

heading("corrupt state is a first run, not a crash")
open(path, "w").write("{ this is not json")
s = load_state(path)
check("no last_run", s.last_run is None)
check("and it says why", s.load_error is not None and "could not be read" in s.load_error)

open(path, "w").write(json.dumps({"schema": 1, "last_run": "not-a-date", "prices": {}}))
s = load_state(path)
check("an unreadable date is reported, not guessed",
      s.last_run is None and s.load_error is not None)

open(path, "w").write(json.dumps({"schema": 1, "last_run": "2026-10-03",
                                  "prices": {"BHP.AX": "forty"}}))
s = load_state(path)
equal("a junk price is dropped rather than coerced", s.prices, {})
equal("the date still loads", s.last_run, date(2026, 10, 3))

heading("an unwritable path warns and returns, it does not raise")
err = save_state({"BHP.AX": Quote("BHP.AX", price=1.0)}, date(2026, 10, 3),
                 os.path.join(tmp, "no-such-dir", "state.json"))
check("returned an error string instead of raising", isinstance(err, str) and err)

done()
