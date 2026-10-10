# STATUS

**Last updated:** 2026-10-10

## What this is

A weekly email about an ASX portfolio, read on a phone on Saturday morning.
It values each holding, compares actual weights against targets, runs the
owner's alert rules, asks Claude to explain material moves, and sends HTML
with a plain-text alternative. Phase 1 of two.

## Stack

Python 3.11, no framework. yfinance for prices, PyYAML for the one config
file, the Anthropic SDK for a single Claude Opus 5.5 call with web search,
stdlib smtplib for Gmail. GitHub Actions runs it weekly and commits
`state.json` back. No database, no test runner.

## Current state

The spine works end to end and now carries the owner's real portfolio: nine
holdings, AUD 36,382.29 on 2026-10-03, 55% of it cash. Cash and the two
unlisted Vanguard funds have no price feed, so they carry a hand-set `price`
and `price_as_of`. They are never fetched and count in full. They show "—"
for the week, never trip `price_move`, and are flagged once their price is
more than 14 days old.

## Recent activity

**2026-10-10 — every path in this project has now executed for real.** Run 2
(`event: schedule`, 2026-10-10 01:34 UTC, 3.6h behind its 22:00 slot — ordinary
private-repo throttling) was the first scheduled run, and the first in which
the Claude call could fire: `main.py` guards it with `if moved and ...`, and
`CWY.AX` moved **+3.76%** against the stored close, past the 3%
`note_threshold_pct`. So the gate demonstrably held. The call then succeeded —
no `EXPLANATION SKIPPED` on stderr, and the send step took 32s against run 1's
7s. `state.json` was committed again (`dfab831..21d8253`).

That closes the last unproven path. yfinance, the Claude call with web search,
Gmail SMTP and the commit-back step have all run against the real services.

**2026-10-06 — the first real run.** A hand-triggered `workflow_dispatch`
fetched all six listed tickers live, sent the email, and committed `state.json`
(`ac4a2fc..678bd70`). It did not reach the Claude call: a first run has no
prior prices, so nothing registered as moved.

**2026-10-06 — all-time P/L.** A holding may now carry `entry_price`, the
average price paid per unit. The row then shows an all-time percentage and
dollar figure beneath the weekly one, and the portfolio carries a total. It is
price-only and says so in the footer: no distributions, no franking, no
brokerage, so for an income holding like KKC it understates the real return.
A holding without an `entry_price` shows "—" and is excluded from the total,
which names what it left out rather than quietly understating. Nothing in the
committed `holdings.yaml` has one yet.

Added hand-set prices and committed the real `holdings.yaml`. A live dry run
fetched all six listed tickers at the expected closes. On the first attempt
Yahoo returned nothing for CWY.AX; the email named it and ran on, and a retry
was clean. Nine test scripts pass. A test now replaces yfinance at its lowest
level and asserts that `CASH`, `VAN0004AU` and `VAN0003AU` are never requested.

## Blocked

Nothing. One thing remains genuinely **UNKNOWN**: how broadly
`monitor/explain.py` converts failures into `ExplanationUnavailable`. The happy
path is now proven, and the call site in `main.py` catches that exception and
still sends the email — but whether *every* failure arrives as that exception
has not been established, so an unhandled one costing the whole email rather
than just the notes is not ruled out. It matters only when the API is down.

## Next up

- `[decide]` Set real target weights (see below).
- `[decide]` Add `entry_price` to the holdings you want an all-time P/L for.
  Nothing has one yet, so the email currently says so instead of showing a figure.
- `[auto]` Fetch the Vanguard managed fund prices properly. Unsolved:
  `www.vanguard.com.au/personal/api/products/personal/fund/<portId>/prices`
  returns data, but it is keyed on an internal portId, and a short probe did
  not find the APIR→portId mapping.
- `[auto]` Phase 2: ASX announcements and `price_sensitive_announcement`;
  ex-distribution handling; NAV premium/discount via `nav_source` and
  `premium_discount`.

## Open decisions

**Target weights are placeholders equal to the actual weights on 2026-10-03.**
Drift reads about zero everywhere, so the drift alert cannot fire, until real
targets are chosen. Two small mismatches with the brief: the total is
$36,382.29, not $36,382.30, and OCL is actually 2.35% (2.3 rounded) against
its 2.4 target. The 2.4 is what makes the targets sum to exactly 100.

**An undated manual price is refused, not used.** That holding drops out of
the total, named, until it is dated. The alternative was to use it under a
warning.

**Hand-priced holdings appear under "Rules that could not be checked" every
week** for `price_move`, because a rule that cannot run is reported. That is
correct, but it is a standing line. Say so if it reads as noise.

**A truncated or refused brief aborts the run** (exit 3, no email), while an
unreachable API sends the numbers anyway. Truncation could join the latter.

**Week change comes from `state.json`, not a five-day window.** A lost state
file costs one week's comparison.
