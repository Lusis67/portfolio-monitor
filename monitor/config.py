"""Load and validate holdings.yaml.

Nothing in here reads a credential. The Anthropic key and the Gmail app
password come from the environment only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

# Alert types this phase understands.
IMPLEMENTED_ALERT_TYPES = ("weight_drift", "price_move", "price_level")

# Alert types the schema reserves for phase 2. They are named explicitly so a
# rule using one is reported as "not running yet" rather than as a typo.
PHASE_2_ALERT_TYPES = ("premium_discount", "price_sensitive_announcement")


@dataclass
class Holding:
    ticker: str
    units: float
    target_weight: float
    asset_type: str = "other"
    # nav_source is phase 2. It is parsed so the schema is stable, and
    # deliberately not used anywhere else.
    nav_source: str | None = None


@dataclass
class AlertRule:
    name: str
    type: str
    applies_to: list[str] | None
    params: dict[str, Any]

    def covers(self, ticker: str) -> bool:
        return self.applies_to is None or ticker in self.applies_to


@dataclass
class Config:
    portfolio_name: str
    currency: str
    holdings: list[Holding]
    alerts: list[AlertRule]
    tolerate_unallocated_pct: float
    also_to: list[str]
    note_threshold_pct: float
    # Problems that must be shouted about rather than swallowed: unknown alert
    # types, missing rule parameters, target weights that don't add up.
    config_errors: list[str] = field(default_factory=list)

    @property
    def tickers(self) -> list[str]:
        return [h.ticker for h in self.holdings]

    @property
    def is_unconfigured(self) -> bool:
        """True when the owner has not filled anything in yet."""
        if not self.holdings:
            return True
        return all(h.units == 0 for h in self.holdings)


class ConfigError(Exception):
    """The file is unusable — not merely questionable."""


def _as_float(value: Any, where: str, errors: list[str]) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{where}: {value!r} is not a number")
        return None


def _parse_holdings(raw: Any, errors: list[str]) -> list[Holding]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("`holdings` must be a list")

    holdings: list[Holding] = []
    seen: set[str] = set()
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            errors.append(f"holdings[{i}]: expected a mapping, got {type(row).__name__}")
            continue
        ticker = row.get("ticker")
        if not ticker:
            errors.append(f"holdings[{i}]: no `ticker`")
            continue
        ticker = str(ticker).strip()
        if ticker in seen:
            errors.append(f"holdings: {ticker} is listed more than once")
            continue
        seen.add(ticker)

        units = _as_float(row.get("units", 0), f"{ticker}.units", errors)
        target = _as_float(row.get("target_weight", 0), f"{ticker}.target_weight", errors)
        if units is None or target is None:
            continue
        if units < 0:
            errors.append(f"{ticker}: units is negative ({units})")
            continue

        holdings.append(
            Holding(
                ticker=ticker,
                units=units,
                target_weight=target,
                asset_type=str(row.get("asset_type", "other")),
                nav_source=row.get("nav_source"),
            )
        )
    return holdings


def _parse_alerts(raw: Any, tickers: list[str], errors: list[str]) -> list[AlertRule]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("`alerts` must be a list")

    rules: list[AlertRule] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            errors.append(f"alerts[{i}]: expected a mapping, got {type(row).__name__}")
            continue
        name = str(row.get("name") or f"alerts[{i}]")
        rtype = row.get("type")
        if not rtype:
            errors.append(f"alert {name!r}: no `type` — this rule is NOT running")
            continue
        rtype = str(rtype)

        if rtype in PHASE_2_ALERT_TYPES:
            errors.append(
                f"alert {name!r}: type {rtype!r} is phase 2 and not built yet — "
                "this rule is NOT running"
            )
            continue
        if rtype not in IMPLEMENTED_ALERT_TYPES:
            errors.append(
                f"alert {name!r}: unknown type {rtype!r} — this rule is NOT running. "
                f"Known types: {', '.join(IMPLEMENTED_ALERT_TYPES)}"
            )
            continue

        applies_to = row.get("applies_to")
        if applies_to is not None:
            if not isinstance(applies_to, list):
                errors.append(f"alert {name!r}: `applies_to` must be a list of tickers")
                continue
            applies_to = [str(t).strip() for t in applies_to]
            unknown = [t for t in applies_to if t not in tickers]
            if unknown:
                errors.append(
                    f"alert {name!r}: applies_to names {', '.join(unknown)}, "
                    "which are not in `holdings` — those parts of the rule do nothing"
                )

        params = {k: v for k, v in row.items() if k not in ("name", "type", "applies_to")}
        problem = _validate_params(name, rtype, params)
        if problem:
            errors.append(problem)
            continue

        rules.append(AlertRule(name=name, type=rtype, applies_to=applies_to, params=params))
    return rules


def _validate_params(name: str, rtype: str, params: dict[str, Any]) -> str | None:
    """Return an error string, or None when the rule is usable."""
    def numeric(key: str) -> bool:
        return isinstance(params.get(key), (int, float)) and not isinstance(params.get(key), bool)

    if rtype == "weight_drift":
        if not numeric("exceeds_pct_points"):
            return (
                f"alert {name!r}: weight_drift needs a numeric `exceeds_pct_points` — "
                "this rule is NOT running"
            )
    elif rtype == "price_move":
        if not numeric("abs_change_pct"):
            return (
                f"alert {name!r}: price_move needs a numeric `abs_change_pct` — "
                "this rule is NOT running"
            )
    elif rtype == "price_level":
        if not (numeric("below") or numeric("above")):
            return (
                f"alert {name!r}: price_level needs a numeric `below` and/or `above` — "
                "this rule is NOT running"
            )
    return None


def load_config(path: str = "holdings.yaml") -> Config:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(raw, source=path)


def parse_config(raw: Any, source: str = "holdings.yaml") -> Config:
    if raw is None:
        raise ConfigError(f"{source} is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"{source} must be a mapping at the top level")

    errors: list[str] = []
    portfolio = raw.get("portfolio") or {}
    targets = raw.get("targets") or {}
    email = raw.get("email") or {}

    holdings = _parse_holdings(raw.get("holdings"), errors)
    alerts = _parse_alerts(raw.get("alerts"), [h.ticker for h in holdings], errors)

    also_to = email.get("also_to") or []
    if not isinstance(also_to, list):
        errors.append("email.also_to must be a list of addresses — ignoring it")
        also_to = []

    cfg = Config(
        portfolio_name=str(portfolio.get("name") or "Portfolio"),
        currency=str(portfolio.get("currency") or "AUD"),
        holdings=holdings,
        alerts=alerts,
        tolerate_unallocated_pct=float(targets.get("tolerate_unallocated_pct") or 0),
        also_to=[str(a) for a in also_to],
        note_threshold_pct=float(email.get("note_threshold_pct", 3) or 0),
        config_errors=errors,
    )

    # Targets that don't add up are usually a typo. Say so; never normalise.
    # Skip the complaint when nothing is configured at all — "no holdings yet"
    # already covers it and two messages about the same emptiness is noise.
    if holdings and not cfg.is_unconfigured:
        total = sum(h.target_weight for h in holdings)
        gap = 100.0 - total
        if abs(gap) > cfg.tolerate_unallocated_pct:
            cfg.config_errors.append(
                f"target weights sum to {total:.1f}%, not 100% "
                f"({gap:+.1f} percentage points unallocated). Nothing has been "
                "normalised — the weights below are measured against the targets as written."
            )
    return cfg
