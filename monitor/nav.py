"""Premium or discount to NAV, read out of the fund's own NTA announcement.

A listed investment trust like KKC files a monthly "Net Tangible Asset
Backing" notice. That notice carries the number, so for these holdings the
premium/discount needs no `nav_source` URL and no scraping: it falls out of the
announcement feed that is already being read.

The parse is deliberately narrow. It looks for a dollar amount in the same
breath as NTA-per-unit language and refuses anything it is not sure of, because
the alternative — picking up a contact phone number, an ABN or last month's
figure — would put a wrong NAV in front of the owner, and a wrong NAV is worse
than no NAV. A refusal is reported, not swallowed.

The resulting figure is unaudited, approximate and as at the notice's own
reporting date, which is usually a week or more before the price it gets
compared against. The email says so; a premium computed across a two-week gap
is indicative and nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# "NTA PER UNIT ... 30 September 2026 $2.3234", "NTA per security $1.23",
# "net tangible assets per unit of $2.3234". The amount may precede or follow
# the label, so both orders are tried, nearest-first.
_LABEL = r"(?:nta|net\s+tangible\s+assets?|net\s+asset\s+value)"
_PER_UNIT = r"per\s+(?:unit|security|share|stapled\s+security)"
_MONEY = r"\$\s*([0-9]+(?:\.[0-9]+)?)"

_PATTERNS = (
    # label ... per unit ... $x   (the common ASX layout, incl. a date between)
    re.compile(_LABEL + r"[^$\n]{0,40}" + _PER_UNIT + r"[^$]{0,120}?" + _MONEY, re.I | re.S),
    # label ... $x ... per unit
    re.compile(_LABEL + r"[^$\n]{0,60}" + _MONEY + r"[^.\n]{0,40}" + _PER_UNIT, re.I),
    # per unit ... label ... $x
    re.compile(_PER_UNIT + r"[^$\n]{0,40}" + _LABEL + r"[^$]{0,120}?" + _MONEY, re.I | re.S),
)

# A unit price outside this range is not a unit price. ASX trusts and LICs sit
# in cents-to-low-dollars; a four-figure match is an ABN, a phone number or a
# total fund size, and guessing is the thing this module exists not to do.
MIN_NAV = 0.01
MAX_NAV = 1000.0


@dataclass
class Nav:
    ticker: str
    nav_per_unit: float | None = None
    as_of: date | None = None
    source_headline: str = ""
    source_date: date | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.nav_per_unit is not None


@dataclass
class PremiumDiscount:
    ticker: str
    price: float
    nav_per_unit: float
    # Positive is a premium to NAV, negative a discount. Percent of NAV.
    premium_pct: float
    nav_as_of: date | None
    source_headline: str
    stale_days: int | None = None

    @property
    def is_discount(self) -> bool:
        return self.premium_pct < 0


def parse_nav(text: str) -> tuple[float | None, str | None]:
    """The NTA per unit in this announcement, or why it could not be read."""
    if not text or not text.strip():
        return None, "the announcement has no text to read"
    flat = " ".join(text.split())
    for pattern in _PATTERNS:
        match = pattern.search(flat)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if MIN_NAV <= value <= MAX_NAV:
            return value, None
        # A match in the right shape but an impossible value is worth saying
        # out loud: it usually means the layout changed, or that an ABN or a
        # phone number sat where the price should be. Written out in full —
        # "4.5e+07" tells a reader nothing about what went wrong.
        shown = f"{int(value):,}" if value == int(value) else f"{value:,}"
        return None, (
            f"a figure of {shown} was found beside the NTA label, which is "
            f"outside {MIN_NAV:g}-{MAX_NAV:g} and so is not a unit price"
        )
    return None, "no NTA per unit could be found in the announcement text"


def nav_from_announcements(ticker: str, items) -> Nav:
    """The most recent readable NTA for one holding, from its announcements.

    `items` are that holding's announcements. The newest NTA notice wins; if it
    cannot be parsed the next one back is tried, because a changed layout in one
    month's notice should not lose the figure entirely.
    """
    candidates = [
        a
        for a in items
        if a.ticker == ticker
        and (
            "net tangible asset" in a.headline.lower()
            or "net asset value" in a.headline.lower()
            or "nta" in a.headline.lower().split()
        )
    ]
    if not candidates:
        return Nav(ticker=ticker, error="no NTA announcement in this period")

    # Chosen by REPORTING date, not filing date. KKC files a weekly NTA valued
    # to within a day or two and a monthly one valued to the month end, and the
    # monthly is often filed later. Taking the newest filing therefore picked a
    # figure two weeks staler than one already in hand. What the reader is owed
    # is the most recently *valued* NAV, so every readable candidate is parsed
    # and the freshest reporting date wins.
    parsed = []
    first_error = None
    for a in candidates:
        if not a.has_text:
            if first_error is None:
                first_error = a.text_error or "the NTA announcement has no text"
            continue
        value, err = parse_nav(a.text)
        if value is None:
            if first_error is None:
                first_error = err
            continue
        parsed.append((_reporting_date(a.text), value, a))

    if not parsed:
        return Nav(ticker=ticker, error=first_error or "the NTA could not be read")

    # A dated notice always beats an undated one. Falling back to the filing
    # date as a stand-in for the valuation date looks harmless and is not: a
    # filing date is never earlier than what it reports, so using it as a proxy
    # OVERSTATES freshness and can beat a notice that honestly says it is
    # valued to month end. Undated notices are therefore ranked behind every
    # dated one, and only used when nothing dated parsed.
    parsed.sort(key=lambda t: (t[0] is not None, t[0] or t[2].when), reverse=True)
    as_of, value, a = parsed[0]
    return Nav(
        ticker=ticker,
        nav_per_unit=value,
        as_of=as_of or a.when,
        source_headline=a.headline,
        source_date=a.when,
    )


_DATE_NEAR_NAV = re.compile(
    r"(\d{1,2})\s+"
    r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{4})",
    re.I,
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        "january february march april may june july august september october "
        "november december".split(),
        start=1,
    )
}


def _reporting_date(text: str) -> date | None:
    """The 'as at' date in the notice, which is not the day it was filed.

    An NTA filed on the 13th is usually "as at 30 September". Comparing a price
    to a NAV and calling them contemporaneous would overstate the figure's
    freshness, so the reporting date travels with it.
    """
    flat = " ".join(text.split())
    anchor = re.search(r"as\s+at[^.]{0,80}", flat, re.I)
    window = anchor.group(0) if anchor else flat[:400]
    match = _DATE_NEAR_NAV.search(window)
    if not match:
        return None
    try:
        return date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))
    except (KeyError, ValueError):
        return None


def premium_discount(ticker: str, price: float, nav: Nav, today: date | None = None) -> PremiumDiscount | None:
    """Where the market price sits against NAV. None when either is missing."""
    if not nav.ok or price is None or not nav.nav_per_unit:
        return None
    pct = (price - nav.nav_per_unit) / nav.nav_per_unit * 100.0
    stale = None
    if nav.as_of and today:
        stale = (today - nav.as_of).days
    return PremiumDiscount(
        ticker=ticker,
        price=price,
        nav_per_unit=nav.nav_per_unit,
        premium_pct=pct,
        nav_as_of=nav.as_of,
        source_headline=nav.source_headline,
        stale_days=stale,
    )
