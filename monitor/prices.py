"""Price fetching via yfinance.

yfinance is a Yahoo scraper, not an API with a contract. It returns HTTP 429
under rate limiting, and GitHub Actions runners get throttled. A fetch that
quietly returned nothing would render a tidy email reporting a portfolio worth
zero, which is far worse than no email at all — so a failure here is recorded
as a failure, named, and carried all the way into the email and onto stderr.
Nothing in this module ever substitutes a number for a missing one.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import date

try:  # pragma: no cover - import shape differs across yfinance versions
    from yfinance.exceptions import YFRateLimitError
except Exception:  # pragma: no cover
    class YFRateLimitError(Exception):
        pass


@dataclass
class Quote:
    ticker: str
    price: float | None = None
    as_of: date | None = None
    error: str | None = None
    rate_limited: bool = False

    @property
    def ok(self) -> bool:
        return self.price is not None


def _looks_rate_limited(exc: Exception) -> bool:
    if isinstance(exc, YFRateLimitError):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def fetch_quote(ticker: str, fetcher=None) -> Quote:
    """Fetch one last close. `fetcher` is injectable so tests never touch Yahoo."""
    try:
        frame = (fetcher or _yf_history)(ticker)
    except Exception as exc:  # yfinance raises a wide variety of things
        return Quote(
            ticker=ticker,
            error=f"{type(exc).__name__}: {exc}".strip(),
            rate_limited=_looks_rate_limited(exc),
        )

    if frame is None or len(frame) == 0:
        return Quote(
            ticker=ticker,
            error="yfinance returned no rows (delisted, wrong suffix, or throttled)",
        )

    try:
        last = frame.iloc[-1]
        price = float(last["Close"])
        stamp = frame.index[-1]
        as_of = stamp.date() if hasattr(stamp, "date") else None
    except Exception as exc:
        return Quote(ticker=ticker, error=f"unreadable price data: {exc}")

    if price != price or price <= 0:  # NaN or nonsense
        return Quote(ticker=ticker, error=f"yfinance returned an unusable close ({price!r})")

    return Quote(ticker=ticker, price=price, as_of=as_of)


def _yf_history(ticker: str):
    import yfinance as yf

    return yf.Ticker(ticker).history(period="5d", auto_adjust=False)


def fetch_prices(tickers, fetcher=None, pause: float = 0.4) -> dict[str, Quote]:
    """Fetch every ticker, one at a time, and never raise.

    Serial with a small pause: a burst of parallel requests is the quickest way
    to earn a 429 from Yahoo, and a weekly job has no reason to hurry.
    """
    quotes: dict[str, Quote] = {}
    for i, ticker in enumerate(tickers):
        if i and pause:
            time.sleep(pause)
        quotes[ticker] = fetch_quote(ticker, fetcher=fetcher)
    return quotes


def warn_about_failures(quotes: dict[str, Quote]) -> list[str]:
    """Print failures to stderr and return them as human-readable lines."""
    failures = [q for q in quotes.values() if not q.ok]
    lines = [f"{q.ticker}: {q.error}" for q in failures]
    for line in lines:
        print(f"PRICE FETCH FAILED — {line}", file=sys.stderr)
    if any(q.rate_limited for q in failures):
        print(
            "PRICE FETCH FAILED — Yahoo rate-limited this run (HTTP 429). "
            "Figures for the named tickers are missing, not zero.",
            file=sys.stderr,
        )
    return lines
