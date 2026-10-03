#!/usr/bin/env python3
"""Weekly ASX portfolio monitor — fetch, compute, explain, render, send.

    python main.py             fetch, compute, explain, render, send
    python main.py --dry-run   everything except the send; HTML to stdout

The dry run needs no Gmail credentials and no Anthropic key. It is how the
owner sees his first email before configuring anything.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from monitor import alerts as alerts_mod
from monitor import compute, explain as explain_mod, prices, render, state as state_mod
from monitor.config import ConfigError, load_config
from monitor.mailer import MailError, send


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except send; print the HTML to stdout",
    )
    parser.add_argument("--config", default="holdings.yaml")
    parser.add_argument("--state", default=state_mod.DEFAULT_PATH)
    parser.add_argument(
        "--no-explain",
        action="store_true",
        help="skip the Claude call even if something moved (useful offline)",
    )
    return parser.parse_args(argv)


def run(args) -> int:
    run_date = date.today()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR — {exc}", file=sys.stderr)
        return 2

    for err in config.config_errors:
        print(f"CONFIG PROBLEM — {err}", file=sys.stderr)

    stored = state_mod.load_state(args.state)
    # Only holdings without a `price` in holdings.yaml are fetched. CASH and the
    # unlisted funds are never sent to yfinance; see config.fetch_tickers.
    quotes = prices.fetch_prices(config.fetch_tickers)
    # Shout about failures here rather than inside the fetcher, so every path
    # into this function is equally loud.
    prices.warn_about_failures(quotes)

    portfolio = compute.build_portfolio(
        config,
        quotes,
        previous_prices=stored.prices,
        previous_run=stored.last_run,
        today=run_date,
    )
    if stored.load_error:
        portfolio.warnings.insert(
            0,
            f"The stored state was unreadable ({stored.load_error}), so this run is "
            "treated as a first run and shows no weekly change.",
        )

    report = alerts_mod.evaluate(config, portfolio)

    # ---- the one Claude call, and only if something actually moved ----
    explanation = None
    explain_warning = None
    moved = compute.movers(portfolio, config.note_threshold_pct)
    if moved and not args.no_explain:
        try:
            explanation = explain_mod.explain(moved, portfolio, config.note_threshold_pct)
        except explain_mod.ExplanationUnavailable as exc:
            # The API could not be reached. The email is the product; send it.
            explain_warning = (
                f"No written notes this week: the Claude API could not be reached "
                f"({exc}). The numbers above are unaffected."
            )
            print(f"EXPLANATION SKIPPED — {exc}", file=sys.stderr)
    elif moved and args.no_explain:
        explain_warning = (
            "--no-explain was set, so there are no written notes this week."
        )

    subject = render.subject(config, portfolio, report, run_date)
    html_body = render.render_html(
        config, portfolio, report, explanation, run_date, explain_warning=explain_warning
    )
    text_body = render.render_text(
        config, portfolio, report, explanation, run_date, explain_warning=explain_warning
    )

    if args.dry_run:
        print(html_body)
        print(f"\nDRY RUN — nothing sent, {args.state} not written.", file=sys.stderr)
        print(f"DRY RUN — subject would be: {subject}", file=sys.stderr)
        return 0

    try:
        recipients = send(subject, text_body, html_body, also_to=config.also_to)
    except MailError as exc:
        print(f"SEND FAILED — {exc}", file=sys.stderr)
        return 1
    print(f"Sent to {', '.join(recipients)}", file=sys.stderr)

    # State is written only after a successful send, and a failure to write it
    # is a warning, not an error: the email has already gone out.
    state_mod.save_state(quotes, run_date, args.state)
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except explain_mod.ExplanationTruncated as exc:
        # A truncated or refused brief must never be sent. Fail the run loudly
        # so the Actions job goes red and nobody gets a half-written email.
        print(f"ABORTED — {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
