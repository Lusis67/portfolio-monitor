"""Weights, drift and the week's change.

Three rules run through all of this:
  * a missing price is never silently treated as zero,
  * nothing is divided by a total that might be zero, and
  * a holding whose price was typed into holdings.yaml gets no weekly change.
    It counts in full towards the value and the weights — that is what the
    field is for — but there is no previous close behind it, and a fabricated
    0.00% would claim the thing did not move, which the data cannot support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Config, Holding
from .prices import Quote, manual_quote


@dataclass
class Row:
    holding: Holding
    quote: Quote
    value: float | None = None
    actual_weight: float | None = None
    drift_pp: float | None = None
    prev_price: float | None = None
    week_change_pct: float | None = None
    # Only for a manually priced holding: how old its price is, in days, and
    # whether that is past `targets.stale_price_days`.
    price_age_days: int | None = None
    price_stale: bool = False
    # All-time, price-only. None whenever the holding has no entry price or no
    # current price — never 0, which would read as "bought at today's price".
    cost_base: float | None = None
    pl_abs: float | None = None
    pl_pct: float | None = None

    @property
    def ticker(self) -> str:
        return self.holding.ticker

    @property
    def target_weight(self) -> float:
        return self.holding.target_weight

    @property
    def priced(self) -> bool:
        return self.quote.ok

    @property
    def fixed_price(self) -> bool:
        """True when this row's price came from the config, not from a feed."""
        return self.quote.fixed

    @property
    def price_as_of(self):
        return self.quote.as_of

    @property
    def entry_price(self) -> float | None:
        return self.holding.entry_price


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
    # All-time P/L across the holdings that have BOTH an entry price and a
    # current price. `pl_missing` names the ones left out, so a partial total
    # can never be read as the whole portfolio's.
    total_cost_base: float | None = None
    total_pl_abs: float | None = None
    total_pl_pct: float | None = None
    pl_missing: list[str] = field(default_factory=list)

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
        if holding.manual_price:
            # Never fetched, so never looked up in `quotes` either.
            quote = manual_quote(holding)
        else:
            quote = quotes.get(holding.ticker) or Quote(
                ticker=holding.ticker, error="no price was fetched for this ticker"
            )
        row = Row(holding=holding, quote=quote)
        if quote.ok:
            row.value = holding.units * quote.price
            if holding.entry_price:
                row.cost_base = holding.units * holding.entry_price
                row.pl_abs = row.value - row.cost_base
                row.pl_pct = (quote.price - holding.entry_price) / holding.entry_price * 100.0
        rows.append(row)

    # The total covers priced holdings only. Holdings whose price failed are
    # excluded and named, rather than counted as zero.
    priced = [r for r in rows if r.priced]
    total = sum(r.value for r in priced) if priced else None

    # The P/L total covers only rows carrying both prices. Summing over a
    # subset and presenting it as the portfolio's would understate silently,
    # so the holdings left out are named and travel with the number.
    with_pl = [r for r in rows if r.pl_abs is not None and r.cost_base]
    total_cost_base = sum(r.cost_base for r in with_pl) if with_pl else None
    total_pl_abs = sum(r.pl_abs for r in with_pl) if with_pl else None
    total_pl_pct = (
        total_pl_abs / total_cost_base * 100.0
        if total_cost_base
        else None
    )
    pl_missing = [r.ticker for r in rows if r.pl_abs is None]

    for row in rows:
        if row.value is not None and total:
            row.actual_weight = row.value / total * 100.0
            row.drift_pp = row.actual_weight - row.target_weight

        if row.fixed_price:
            # A hand-written price has no previous close behind it. Leave
            # week_change_pct as None so the email prints "—": the one thing
            # that must never appear here is 0.00%.
            if row.price_as_of is not None:
                row.price_age_days = (today - row.price_as_of).days
                row.price_stale = row.price_age_days > config.stale_price_days
            continue

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
    stale = [r for r in rows if r.price_stale]
    if stale:
        detail = ", ".join(
            f"{r.ticker} priced {r.quote.price:g} on {r.price_as_of.isoformat()}, "
            f"{r.price_age_days} days ago"
            for r in stale
        )
        warnings.append(
            "A manual price in holdings.yaml is older than "
            f"{config.stale_price_days:g} days: {detail}. Those values are used as "
            "written and counted in full, so the total is only as current as they "
            "are. Cash especially drifts as it is spent — paste a fresh figure and "
            "date into holdings.yaml when you can."
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
        total_cost_base=total_cost_base,
        total_pl_abs=total_pl_abs,
        total_pl_pct=total_pl_pct,
        pl_missing=pl_missing,
    )


def movers(portfolio: Portfolio, threshold_pct: float) -> list[Row]:
    """Rows that moved more than `threshold_pct` — the ones worth explaining."""
    out = [
        r
        for r in portfolio.rows
        if r.week_change_pct is not None and abs(r.week_change_pct) > threshold_pct
    ]
    return sorted(out, key=lambda r: abs(r.week_change_pct), reverse=True)
