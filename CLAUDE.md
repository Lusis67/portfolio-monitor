# Notes for whoever works here next

A weekly ASX portfolio email. Read `README.md` first for what it does and
`STATUS.md` for where it stands.

## Shape

`main.py` is the only entry point and reads top to bottom: load config, fetch
prices, compute, evaluate alerts, explain, render, send, save state. Each of
those is one module under `monitor/`. There is no framework and no magic.

## The rules this code was built around

* **The email is the product.** If the web search fails, if the API is
  unreachable, if `state.json` cannot be written — send anyway and warn. The
  one exception is a truncated or refused Claude brief, which aborts the run
  rather than mail half a sentence.
* **A missing number is never a zero.** A price that failed to fetch is
  `None`, is excluded from the total, and is named in the email and on
  stderr. A tidy email reporting a portfolio worth zero is worse than no
  email.
* **Never divide by a total that might be zero.** The owner's config ships
  with `units: 0`, and that path is tested.
* **A rule that cannot run is reported by name.** Unknown alert types, missing
  parameters, rules that could not be evaluated for want of a price — all of
  them surface in the email. Nothing is skipped silently.
* **Every triggered alert shows its arithmetic**, so it can be checked rather
  than trusted.
* **Commentary found online is somebody's dated opinion**, attributed with
  source and date, and never allowed to overrule a computed number.

## Working on it

```bash
python main.py --dry-run                       # no credentials needed
for t in tests/test_*.py; do python "$t" || break; done
```

Tests are plain scripts — a line per check, exit non-zero on the first
failure. Add to them by writing more `check(...)` calls; there is nothing to
register. `tests/harness.py` is the whole framework.

Before touching the Claude call in `monitor/explain.py`, load the
`claude-api` skill. The model id, the thinking parameters and the server-tool
result shapes have all changed recently enough that writing them from memory
produces plausible, wrong code. The three stop reasons that look like success
and are not — `max_tokens`, `refusal`, `pause_turn` — each have a test.

## Phase 2

Not built, deliberately, and there are no stubs for it beyond the `nav_source`
field and the two reserved alert types already documented in `README.md`.
Those two types are rejected at config load with a message saying they are
phase 2, so nobody is misled into thinking a rule is running.
