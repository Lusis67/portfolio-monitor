# STATUS

**Last updated:** 2026-10-06

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

**2026-10-06 — the first real run happened, and it worked.** A hand-triggered
`workflow_dispatch` fetched all six listed tickers live, sent the email, and
committed `state.json` back (`ac4a2fc..678bd70`). So yfinance, Gmail SMTP and
the commit-back step are proven against the real repo. **The Claude call is
still unproven**: `main.py` only calls it when something moved more than
`note_threshold_pct`, and a first run has no prior prices, so that branch did
not execute. The first scheduled run (Fri 22:00 UTC) is the first chance it can.

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

Nothing. Still unproven rather than broken: the Claude call has never hit the
real API, and no email has actually been sent. Both are covered by fakes.

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
