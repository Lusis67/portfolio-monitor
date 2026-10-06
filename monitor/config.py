"""Load and validate holdings.yaml.

Nothing in here reads a credential. The Anthropic key and the Gmail app
password come from the environment only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
    # A holding may carry its price in the config instead of being fetched:
    # cash has no ticker to fetch, and an unlisted managed fund (an APIR code
    # like VAN0004AU) has no price feed at all. `manual_price` is True whenever
    # a `price` key was written, even if the value turned out to be unusable —
    # the ticker must never be sent to yfinance on the strength of a typo.
    manual_price: bool = False
    price: float | None = None
    price_as_of: date | None = None
    # Average price PAID per unit, in the portfolio currency. Optional, and
    # absent is a first-class state: a holding without one shows "—" for
    # all-time P/L and is left out of the portfolio P/L rather than being
    # treated as free. Zero would read as a gift.
    entry_price: float | None = None


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
    # How old a manual price may be before the email says so. A warning, not
    # an error: a stale price is still the best number available.
    stale_price_days: float = 14
    # Problems that must be shouted about rather than swallowed: unknown alert
    # types, missing rule parameters, target weights that don't add up.
    config_errors: list[str] = field(default_factory=list)

    @property
    def tickers(self) -> list[str]:
        """Every holding's identifier, fetched or not."""
        return [h.ticker for h in self.holdings]

    @property
    def fetch_tickers(self) -> list[str]:
        """The identifiers a price feed is asked about — and only those.

        A holding carrying a `price` is never fetched. CASH and the APIR codes
        would fail every week if they were, and a failure is reserved for
        things that actually went wrong.
        """
        return [h.ticker for h in self.holdings if not h.manual_price]

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

        manual_price = "price" in row
        price, price_as_of = None, None
        if manual_price:
            price, price_as_of = _parse_manual_price(ticker, row, errors)

        entry_price = None
        if row.get("entry_price") is not None:
            entry_price = _as_float(row.get("entry_price"), f"{ticker}.entry_price", errors)
            if entry_price is not None and not entry_price > 0:
                errors.append(
                    f"{ticker}: entry_price is {entry_price:g} — it must be above zero. "
                    "This holding shows no all-time P/L and is left out of the total."
                )
                entry_price = None

        holdings.append(
            Holding(
                ticker=ticker,
                units=units,
                target_weight=target,
                asset_type=str(row.get("asset_type", "other")),
                nav_source=row.get("nav_source"),
                manual_price=manual_price,
                price=price,
                price_as_of=price_as_of,
                entry_price=entry_price,
            )
        )
    return holdings


def _parse_manual_price(ticker: str, row: dict, errors: list[str]):
    """Read `price` and `price_as_of`, or report why they cannot be used.

    An undated manual price is refused rather than used. A number typed in by
    hand months ago and presented without its date silently becomes a lie, and
    the whole point of the field is to carry cash and the unlisted funds
    honestly. Refusing it costs one holding's value for a week; accepting it
    costs the reader's ability to tell a current price from an old one.

    The holding itself survives, priceless and named: it still appears in the
    email as "no price — excluded from the total", alongside the config error.
    It is never fetched, because `price` was written and the ticker is not
    something a feed could answer for.
    """
    price = _as_float(row.get("price"), f"{ticker}.price", errors)
    if price is not None and not price > 0:  # also catches NaN
        errors.append(
            f"{ticker}: price is {price:g} — a manual price must be above zero. "
            "This holding has NO price and is excluded from the total."
        )
        price = None

    raw_as_of = row.get("price_as_of")
    if raw_as_of is None:
        errors.append(
            f"{ticker}: has a `price` but no `price_as_of`. An undated manual "
            "price is refused, not used — this holding has NO price and is "
            "excluded from the total. Add `price_as_of: YYYY-MM-DD`."
        )
        return None, None

    as_of = raw_as_of if isinstance(raw_as_of, date) else None
    if as_of is None:
        try:
            as_of = date.fromisoformat(str(raw_as_of).strip())
        except ValueError:
            errors.append(
                f"{ticker}: price_as_of {raw_as_of!r} is not an ISO date "
                "(YYYY-MM-DD). The price is refused, not used — this holding "
                "has NO price and is excluded from the total."
            )
            return None, None

    if price is None:
        return None, None
    return price, as_of


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


DEFAULT_STALE_PRICE_DAYS = 14.0


def _stale_price_days(targets: dict, errors: list[str]) -> float:
    raw = targets.get("stale_price_days", DEFAULT_STALE_PRICE_DAYS)
    value = _as_float(raw, "targets.stale_price_days", errors)
    if value is None or value < 0:
        if value is not None:
            errors.append(
                f"targets.stale_price_days is {value:g} — using the default "
                f"{DEFAULT_STALE_PRICE_DAYS:g} days instead."
            )
        return DEFAULT_STALE_PRICE_DAYS
    return value


class ConfigLoader(yaml.SafeLoader):
    """SafeLoader that leaves dates as text.

    PyYAML turns anything date-shaped into a `date` itself, and on an
    impossible one (`price_as_of: 2026-13-45`) raises a bare ValueError from
    deep inside the loader — one typo would take down the whole run with a
    traceback and no email. Read as text, the date reaches
    `_parse_manual_price`, which reports it by holding and carries on.
    """


ConfigLoader.add_constructor("tag:yaml.org,2002:timestamp", ConfigLoader.construct_yaml_str)


def load_config(path: str = "holdings.yaml") -> Config:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.load(fh, Loader=ConfigLoader)
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

    stale_days = _stale_price_days(targets, errors)
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
        stale_price_days=stale_days,
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
