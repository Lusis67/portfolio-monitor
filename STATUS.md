# STATUS

## What this is

A weekly email about an ASX portfolio, sent to its owner on Saturday morning
and read on a phone. It fetches last closes, compares each holding's actual
weight against the target weight, evaluates the owner's own alert rules, asks
Claude to explain anything that moved materially, and sends an HTML email with
a plain-text alternative. Phase 1 of two.

## Stack

Python 3.11, no framework. yfinance for prices, PyYAML for the single config
file, the Anthropic SDK for one call to Claude Opus 5.5 with the web search
tool, stdlib smtplib for Gmail. GitHub Actions runs it weekly and commits the
run state back to the repository. There is no database and no test runner.

## Current state

The spine is complete and works end to end. A dry run against the committed
config renders a full email without needing a single credential. Prices,
weights, drift, the three phase 1 alert types, the Claude call, both email
bodies, the Gmail send and the state file are all built. Seven test scripts
cover the arithmetic against hand-worked numbers, every alert boundary,
unknown and phase 2 alert types, the zero-unit portfolio, missing prices, the
first run, every bad stop reason from the API, and the git commit-back
including a simulated push race.

## Recent activity

Built from an empty repository in one pass. All tests pass. The dry run was
exercised against live Yahoo data and again against a forced price failure.

## Blocked

Nothing. Two things could not be verified from this container and are
unverified rather than broken: the Claude call has never run against the real
API (no key here, so it is covered by a fake client instead), and no email has
ever been sent (no Gmail credentials). Both paths are exercised by tests, but
a first real run will be the first real proof.

## Next up

- `[auto]` Fill in `holdings.yaml` with real units and target weights. Until
  then every email correctly reports that nothing is configured.
- `[decide]` Add the three GitHub secrets and let the first scheduled run go
  out, or dispatch it manually to see it sooner.
- `[auto]` Phase 2: ASX announcements, including the price-sensitive flag and
  the `price_sensitive_announcement` alert type.
- `[auto]` Phase 2: ex-distribution handling, so a distribution does not read
  as a price fall.
- `[auto]` Phase 2: NAV premium/discount for listed funds, using the
  `nav_source` field already in the schema, and the `premium_discount` alert.

## Open decisions

**A truncated or refused brief aborts the whole run.** The instruction was to
raise rather than send on `max_tokens` and on `refusal`, so that is what
happens: exit 3, no email, a red job. That trades one week's email for the
certainty of never sending a half-written one. The alternative — send the
numbers with a line saying the notes failed — is what already happens when the
API is merely unreachable. Say the word and truncation can join it.

**Week change comes from the stored prices, not from a five-day history
window.** It is what `state.json` is for, and it means the email never claims
a week it cannot evidence. The cost is that a lost `state.json` costs one
week's comparison.
