"""Weights, drift and the week's change.

Two rules run through all of this:
  * a missing price is never silently treated as zero, and
  * nothing is divided by a total that might be zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Config, Holding
from .prices import Quote


@dataclass
class Row:
    holding: Holding
    quote: Quote
    value: float | None = None
    actual_weight: float | None = None
    drift_pp: float | None = None
    prev_price: float | None = None
    week_change_pct: float | None = None

    @property
    def ticker(self) -> str:
        return self.holding.ticker

    @property
    def target_weight(self) -> float:
        return self.holding.target_weight

    @property
    def priced(self) -> bool:
        return self.quote.ok


@dataclass
class Portfolio:
    rows: list[Row]
    total_value: float | None
    currency: str
    # Everything the reader must be told before trusting a number.
    failed_tickers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    first_run: bool = False
    previous_run: date | None = None
    days_covered: int | None = None
    unconfigured: bool = False

    @property
    def priced_rows(self) -> list[Row]:
        return [r for r in self.rows if r.priced]


def build_portfolio(
    config: Config,
    quotes: dict[str, Quote],
    previous_prices: dict[str, float] | None = None,
    previous_run: date | None = None,
    today: date | None = None,
) -> Portfolio:
    previous_prices = previous_prices or {}
    first_run = previous_run is None
    today = today or date.today()

    rows: list[Row] = []
    for holding in config.holdings:
        quote = quotes.get(holding.ticker) or Quote(
            ticker=holding.ticker, error="no price was fetched for this ticker"
        )
        row = Row(holding=holding, quote=quote)
        if quote.ok:
            row.value = holding.units * quote.price
        rows.append(row)

    # The total covers priced holdings only. Holdings whose price failed are
    # excluded and named, rather than counted as zero.
    priced = [r for r in rows if r.priced]
    total = sum(r.value for r in priced) if priced else None

    for row in rows:
        if row.value is not None and total:
            row.actual_weight = row.value / total * 100.0
            row.drift_pp = row.actual_weight - row.target_weight

        prev = previous_prices.get(row.ticker)
        if prev is not None and row.quote.ok and prev > 0:
            row.prev_price = prev
            row.week_change_pct = (row.quote.price - prev) / prev * 100.0

    failed = [r.ticker for r in rows if not r.priced]
    warnings: list[str] = []
    if failed:
        warnings.append(
            "No price for " + ", ".join(failed) + ". These holdings are missing from "
            "the portfolio total and from every weight below — the figures are "
            "incomplete, not small."
        )
    if any(r.quote.rate_limited for r in rows):
        warnings.append(
            "Yahoo rate-limited this run (HTTP 429). That is the usual cause when a "
            "GitHub Actions run comes back short of prices."
        )

    unconfigured = config.is_unconfigured
    if unconfigured:
        warnings.append(
            "No holdings are configured yet — every row in holdings.yaml has "
            "units: 0, so there is no portfolio to value, weigh or drift. Fill in "
            "`units` and `target_weight` in holdings.yaml and this becomes a real report."
        )

    days = None
    if previous_run is not None:
        days = (today - previous_run).days
        if days > 10:
            warnings.append(
                f"The last run was {previous_run.isoformat()}, {days} days ago, so "
                '"this week" below actually covers that whole stretch.'
            )
        elif 0 <= days < 4:
            warnings.append(
                f"The last run was only {days} day(s) ago ({previous_run.isoformat()}), "
                'so "this week" below covers less than a week.'
            )

    if first_run:
        warnings.append(
            "This is the first run: there is no stored price from last week, so "
            "this email is a position snapshot and shows no weekly change. Next "
            "Saturday's email will have one."
        )

    return Portfolio(
        rows=rows,
        total_value=total,
        currency=config.currency,
        failed_tickers=failed,
        warnings=warnings,
        first_run=first_run,
        previous_run=previous_run,
        days_covered=days,
        unconfigured=unconfigured,
    )


def movers(portfolio: Portfolio, threshold_pct: float) -> list[Row]:
    """Rows that moved more than `threshold_pct` — the ones worth explaining."""
    out = [
        r
        for r in portfolio.rows
        if r.week_change_pct is not None and abs(r.week_change_pct) > threshold_pct
    ]
    return sorted(out, key=lambda r: abs(r.week_change_pct), reverse=True)
