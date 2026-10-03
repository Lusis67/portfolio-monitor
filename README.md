# portfolio-monitor

A weekly email about an ASX portfolio. Every Saturday morning it reports what
each holding did over the week, how the actual weights sit against the target
weights, which of the owner's own alert rules tripped, and a short written note
on anything that moved materially. It is built to be read on a phone.

This is **phase 1**. ASX announcements, ex-distribution handling and NAV
premium/discount are phase 2 and are not built — see `STATUS.md`.

## Running it

```bash
pip install -r requirements.txt

python main.py --dry-run     # everything except the send; HTML to stdout
python main.py               # fetch, compute, explain, render, send
```

**`--dry-run` needs no credentials at all** — no Gmail account, no Anthropic
key. It is how you see your first email before configuring anything. It also
writes no `state.json`, so it never disturbs the real weekly sequence.

Other flags: `--config` and `--state` point at alternative files, and
`--no-explain` skips the Claude call entirely (useful offline).

Exit codes: `0` sent (or dry run), `1` the send failed, `2` the config file is
unusable, `3` the explanation came back truncated or refused and the run
aborted rather than mail a half-written brief.

## Configuring it

Everything lives in `holdings.yaml`, which holds the owner's real portfolio.
Each holding takes:

| field | meaning |
| --- | --- |
| `ticker` | the identifier. For anything fetched, the yfinance symbol — ASX listings need the `.AX` suffix |
| `units` | how many you hold; fractions are fine |
| `target_weight` | the percent of the portfolio you intend it to be. If the targets don't sum to 100 (within `targets.tolerate_unallocated_pct`) the email says so; nothing is normalised |
| `asset_type` | free text, used only to group the table: `equity`, `etf`, `lic`, `managed_fund`, `cash`… |
| `price` | optional — see below |
| `price_as_of` | required with `price`: the ISO date that price was true |
| `nav_source` | phase 2, parsed and ignored |

### Holdings priced by hand

Some holdings have no price to fetch. Cash has no ticker, and an unlisted
managed fund like `VAN0004AU` is identified by an APIR code that no price feed
knows. Give such a holding a `price` and a `price_as_of`, and:

* **it is never fetched** — its ticker is never sent to yfinance;
* **it counts in full** towards the total, the weights and drift;
* **it has no weekly change.** The week column shows "—", never 0.00%, and
  `price_move` rules report it as "could not be checked" rather than run.
  A typed-in price has no previous close behind it, and a zero would claim
  the holding didn't move;
* **its date is shown** beside it, and once it is older than
  `targets.stale_price_days` (default 14) the email carries a warning. Only a
  warning — the run goes ahead with the old price, and says so.

Cash is just such a holding at `price: 1.00` with `asset_type: cash`. Its
balance drifts as you spend, so the staleness warning is a fair reminder to
update the figure.

A `price` with no `price_as_of`, an impossible date, or a price that isn't a
positive number is a **config error naming the holding**. That price is
refused, not used: the holding shows as "no price — excluded from the total"
until it is fixed, and it is still not fetched.

Alert rules are named types with parameters, not expressions. Phase 1
implements three:

| type | triggers when | parameters |
| --- | --- | --- |
| `weight_drift` | \|actual weight − target weight\| exceeds N percentage points | `exceeds_pct_points` |
| `price_move` | the week's change is beyond ±N percent | `abs_change_pct` |
| `price_level` | the last close crosses a price you name | `below` and/or `above` |

Any rule can be narrowed to particular holdings with `applies_to`. A rule
whose `type` is unknown, or whose parameters are missing, is **reported by
name in the email and on stderr** and does not run — a rule you think is
running and isn't is worse than no rule.

Two further types are reserved for phase 2 and not built:
`premium_discount` and `price_sensitive_announcement`. A rule using either is
reported as "phase 2, not built yet — NOT running" rather than as a typo.

Nothing in `holdings.yaml` is a secret, and no credential is ever read from it.

## Secrets

Set these three as GitHub repository secrets (Settings → Secrets and
variables → Actions):

| secret | what it is |
| --- | --- |
| `ANTHROPIC_API_KEY` | for the one Claude call that explains the movers |
| `GMAIL_ADDRESS` | the sending account, and the first recipient |
| `GMAIL_APP_PASSWORD` | a Google **app password**, not your account password |

Add further recipients under `email.also_to` in `holdings.yaml`.

## The schedule

`.github/workflows/weekly.yml` runs `cron: "0 22 * * 5"` — Friday 22:00 UTC,
which is Saturday 8am in Brisbane (UTC+10 year-round, no daylight saving).
GitHub throttles scheduled workflows on private repositories and often
delivers them hours late; that is expected, not a fault. `workflow_dispatch`
is enabled if you want it now.

After a successful send, the job commits the updated `state.json` back to the
repository, which is how next week knows what this week's prices were.

## Tests

No test runner. Each file is a plain script that prints a line per check and
exits non-zero on the first failure. None of them touch the network, and none
depend on the owner's numbers in `holdings.yaml`:

```bash
for t in tests/test_*.py; do python "$t" || break; done
```

## Honest limitations

* Prices come from Yahoo Finance via `yfinance`, which is a scraper, not an
  API with a contract. It returns HTTP 429 under rate limiting and Actions
  runners get throttled. Anything it fails to fetch is **named** in the email
  and on stderr, and is excluded from the total rather than counted as zero.
* Hand-set prices are only as current as the last time someone typed them
  in. Today that is cash and both Vanguard managed funds, about 61% of the
  portfolio by value. The email dates them and flags stale ones, but it cannot
  know what they are worth now.
* "This week" means "since the last recorded run". If a run is missed, the
  email says how long the period actually covers.
* The written notes come from a model with web search. Anything it found
  online is somebody's dated opinion and is attributed as such. The numbers
  are computed locally and are never overruled by them.
* Nothing here is financial advice.
