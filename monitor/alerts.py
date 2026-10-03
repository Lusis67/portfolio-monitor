"""The alert engine.

Rules are named types with parameters. Unknown types are rejected at config
load (see config.py) rather than skipped here — a rule the owner thinks is
running and isn't is worse than no rule at all.

Every trigger carries the numbers behind it, so the arithmetic can be checked
rather than trusted. A rule that could not be evaluated (no price, no prior
price) is reported too, as "not evaluated" — never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AlertRule, Config
from .compute import Portfolio, Row


@dataclass
class Trigger:
    rule_name: str
    rule_type: str
    ticker: str
    message: str
    # The arithmetic, spelled out: e.g. "|12.3 - 5.0| = 7.3pp > 5pp".
    arithmetic: str


@dataclass
class NotEvaluated:
    rule_name: str
    rule_type: str
    ticker: str
    reason: str


@dataclass
class AlertReport:
    triggered: list[Trigger]
    not_evaluated: list[NotEvaluated]


def _fmt(value: float, places: int = 2) -> str:
    return f"{value:,.{places}f}"


def _check_weight_drift(rule: AlertRule, row: Row):
    limit = float(rule.params["exceeds_pct_points"])
    if row.actual_weight is None:
        reason = (
            "no price for this holding" if not row.priced else "the portfolio has no value to weigh against"
        )
        return None, reason
    drift = row.actual_weight - row.target_weight
    if abs(drift) > limit:
        return (
            Trigger(
                rule_name=rule.name,
                rule_type=rule.type,
                ticker=row.ticker,
                message=(
                    f"{row.ticker} sits at {_fmt(row.actual_weight, 1)}% of the portfolio "
                    f"against a target of {_fmt(row.target_weight, 1)}%"
                ),
                arithmetic=(
                    f"|{_fmt(row.actual_weight, 1)}% − {_fmt(row.target_weight, 1)}%| = "
                    f"{_fmt(abs(drift), 1)}pp, which exceeds {_fmt(limit, 1)}pp"
                ),
            ),
            None,
        )
    return None, None


def _check_price_move(rule: AlertRule, row: Row):
    limit = float(rule.params["abs_change_pct"])
    if row.week_change_pct is None:
        reason = (
            "no price for this holding"
            if not row.priced
            else "no price stored from the previous run to compare against"
        )
        return None, reason
    change = row.week_change_pct
    if abs(change) > limit:
        direction = "up" if change > 0 else "down"
        return (
            Trigger(
                rule_name=rule.name,
                rule_type=rule.type,
                ticker=row.ticker,
                message=f"{row.ticker} is {direction} {_fmt(abs(change), 1)}% on the week",
                arithmetic=(
                    f"({_fmt(row.quote.price)} − {_fmt(row.prev_price)}) ÷ {_fmt(row.prev_price)} = "
                    f"{change:+.1f}%, beyond ±{_fmt(limit, 1)}%"
                ),
            ),
            None,
        )
    return None, None


def _check_price_level(rule: AlertRule, row: Row):
    if not row.priced:
        return None, "no price for this holding"
    price = row.quote.price
    below = rule.params.get("below")
    above = rule.params.get("above")
    if isinstance(below, (int, float)) and not isinstance(below, bool) and price < float(below):
        return (
            Trigger(
                rule_name=rule.name,
                rule_type=rule.type,
                ticker=row.ticker,
                message=f"{row.ticker} is below {_fmt(float(below))}",
                arithmetic=f"last close {_fmt(price)} < {_fmt(float(below))}",
            ),
            None,
        )
    if isinstance(above, (int, float)) and not isinstance(above, bool) and price > float(above):
        return (
            Trigger(
                rule_name=rule.name,
                rule_type=rule.type,
                ticker=row.ticker,
                message=f"{row.ticker} is above {_fmt(float(above))}",
                arithmetic=f"last close {_fmt(price)} > {_fmt(float(above))}",
            ),
            None,
        )
    return None, None


_CHECKS = {
    "weight_drift": _check_weight_drift,
    "price_move": _check_price_move,
    "price_level": _check_price_level,
}


def evaluate(config: Config, portfolio: Portfolio) -> AlertReport:
    triggered: list[Trigger] = []
    not_evaluated: list[NotEvaluated] = []

    by_ticker = {row.ticker: row for row in portfolio.rows}

    for rule in config.alerts:
        check = _CHECKS.get(rule.type)
        if check is None:
            # Unreachable: config.py refuses to build a rule of an unknown type.
            # Kept as a tripwire rather than a silent pass.
            raise AssertionError(f"alert type {rule.type!r} has no check implemented")
        for ticker, row in by_ticker.items():
            if not rule.covers(ticker):
                continue
            trigger, reason = check(rule, row)
            if trigger is not None:
                triggered.append(trigger)
            elif reason is not None:
                not_evaluated.append(
                    NotEvaluated(
                        rule_name=rule.name,
                        rule_type=rule.type,
                        ticker=ticker,
                        reason=reason,
                    )
                )

    return AlertReport(triggered=triggered, not_evaluated=not_evaluated)
