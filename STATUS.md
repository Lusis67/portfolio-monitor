# STATUS

**Last updated:** 2026-10-15

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

**2026-10-15 — ASX announcements work, and the blocker was wrong.** The feed
item's `url` field is empty, which an earlier pass took as "the documents are
unreachable" and parked the whole feature on. The handle is `documentKey`, and
`asx-research/1.0/file/{documentKey}` on the same host returns the PDF: HTTP
200, `application/pdf`. No token, no scraper, no third-party mirror. So step 2
of the original spec is done — headlines, the filer's price-sensitive flag, and
document text where it is worth reading — and **step 4 came with it**: KKC's
monthly NTA notice carries the NAV, so premium/discount needs no `nav_source`
URL. KKC is at about **11% discount to NAV** (price 2.06 against NAV 2.3097 as
at 2026-10-11).

Three real constraints, all measured:
- The feed returns **exactly five items per code**; `pageSize`, `count`,
  `limit`, `page` and `itemsPerPage` are all ignored. When five do not reach
  back to the period start the code is named as truncated and the email says
  the list may be incomplete.
- One filing can cover several funds (VGS and VVLU share a `documentKey`), so
  documents are fetched once and attributed to each holding.
- `isPriceSensitive` is unreliable. Cleanaway's EQT takeover update came back
  flagged NOT price-sensitive, and in the following week CWY moved 2.9% against
  a 3% threshold, so neither the flag nor price movement would have fetched it.
  A `SUBSTANTIVE` headline list now decides first.

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

Nothing. Two things are genuinely **UNKNOWN**.

A fund that files NTAs on more than one basis is not guarded against. KKC files
a weekly notice and a monthly one; all three read in testing were explicitly in
Australian dollars, and the figure chosen is the most recently **valued** one
rather than the most recently filed. But nothing checks the currency, so an
issuer publishing a USD NAV would be compared against an AUD price without
complaint. Not observed; not ruled out either.

And: how broadly
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
- `[auto]` **Step 3, ex-distribution handling, is the remaining Phase 2 item.**
  The data is already free: `asx-research/1.0/companies/{code}/key-statistics`
  returns `dateExDate`, `dateRecordDate`, `datePayDate`, `dividend` and
  `frankingPercent`. Nothing reads it yet, so an ex-date still shows as a loss.
- `[auto]` Feed the announcement text into the Claude call so the written notes
  can use it. The text reaches the email but not yet the model, so the notes
  explain a move without seeing the announcement that caused it.
- `[auto]` A `price_sensitive_announcement` alert rule. The schema reserves the
  type; nothing implements it.

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
