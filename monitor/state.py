"""state.json — last run date and last prices.

The workflow commits this file back to the repo after a successful send, so
next week's run has something to measure against. State is support, not the
product: if it cannot be read the run treats the week as a first run, and if
it cannot be written the email has already gone out and the failure is a
warning on stderr, not an exception.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date

SCHEMA = 1
DEFAULT_PATH = "state.json"


@dataclass
class State:
    last_run: date | None = None
    prices: dict[str, float] = field(default_factory=dict)
    load_error: str | None = None


def load_state(path: str = DEFAULT_PATH) -> State:
    if not os.path.exists(path):
        return State()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"STATE UNREADABLE — {path}: {exc}. Treating this as a first run.",
            file=sys.stderr,
        )
        return State(load_error=f"{path} could not be read ({exc})")

    last_run = None
    if raw.get("last_run"):
        try:
            last_run = date.fromisoformat(str(raw["last_run"]))
        except ValueError as exc:
            print(f"STATE UNREADABLE — bad last_run: {exc}", file=sys.stderr)
            return State(load_error=f"{path} has an unreadable last_run ({exc})")

    prices: dict[str, float] = {}
    for ticker, value in (raw.get("prices") or {}).items():
        try:
            prices[str(ticker)] = float(value)
        except (TypeError, ValueError):
            print(f"STATE UNREADABLE — bad stored price for {ticker}: {value!r}", file=sys.stderr)

    return State(last_run=last_run, prices=prices)


def save_state(quotes, run_date: date, path: str = DEFAULT_PATH) -> str | None:
    """Write state. Returns an error string instead of raising — the email
    has already been sent by the time this runs and is worth more than the file.

    Only prices that actually came back are stored. A ticker that failed keeps
    whatever it had, so next week compares against a real number or none at all.
    """
    existing = load_state(path)
    prices = dict(existing.prices)
    for ticker, quote in quotes.items():
        if getattr(quote, "ok", False):
            prices[ticker] = round(float(quote.price), 6)

    payload = {
        "schema": SCHEMA,
        "last_run": run_date.isoformat(),
        "prices": dict(sorted(prices.items())),
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
    except OSError as exc:
        message = f"could not write {path}: {exc}"
        print(f"STATE NOT SAVED — {message}. The email went out anyway.", file=sys.stderr)
        return message
    return None
