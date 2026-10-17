"""ASX company announcements, with the document text where it is worth reading.

The source is ASX's own research API, the same one asx.com.au's company pages
call:

    .../asx-research/1.0/companies/{code}/announcements   -> the list
    .../asx-research/1.0/file/{documentKey}               -> that announcement's PDF

The list item carries an empty `url` field, which is what made an earlier pass
conclude the documents were unreachable and park this whole feature. They are
reachable: `documentKey` is the handle, and `file/{documentKey}` on the same
host returns the PDF directly. No token, no scraper, no third-party mirror.

Three limits of the feed, all of them honest gaps rather than things to design
around:

  - **It returns exactly five items per code.** `pageSize`, `count`, `limit`,
    `page` and `itemsPerPage` are all ignored — tested. So a holding that files
    more than five announcements between two runs has older ones we cannot see.
    When the oldest item returned is still newer than the period start, that
    code goes in `truncated` and the email says the list may be incomplete.
    Silence there would read as "nothing else happened".
  - **A document can belong to several holdings.** VGS and VVLU came back with
    the same `documentKey`: Vanguard files one notice covering many funds. The
    document is fetched once and attributed to every holding that listed it.
  - **`isPriceSensitive` is the filer's own flag**, not a judgement. It is
    reported as what it is and never inferred from the headline.

ASX announcement PDFs are **AES-256 encrypted** with an empty user password,
so reading them needs pypdf's `crypto` extra. `requirements.txt` asks for
`pypdf[crypto]` for that reason and the comment there explains why; the bare
package installs happily and then fails on every single document.

Nothing in here can fail the run. A code that errors is named and skipped; a
document whose text cannot be read keeps its headline. An email with headlines
is worth more than no email.
"""

from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import requests

API = "https://asx.api.markitdigital.com/asx-research/1.0"
# asx.com.au sends a browser UA; the API answers a bare one too, but a request
# that looks like a browser is the one least likely to be shaped away.
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT = 20

# The feed's hard ceiling, measured rather than documented. Named so the
# truncation check reads as a fact about the API and not a magic number.
FEED_ITEMS = 5

# How many documents one run may download. Each is a separate request of tens
# to hundreds of KB, and the weekly job has a 20-minute budget it shares with
# prices, Claude and SMTP. Headlines are never capped — only the PDFs.
MAX_DOCUMENTS = 12

# Per-document text handed onward. A six-page notice is thousands of tokens and
# the interesting part of an ASX announcement is almost always its opening.
# Truncation is marked in the text so nothing reads as the whole document.
MAX_TEXT_CHARS = 6000


@dataclass
class Announcement:
    ticker: str
    when: date
    headline: str
    price_sensitive: bool
    document_key: str
    announcement_type: str = ""
    # Extracted document text. None means not fetched or not readable, which is
    # not the same as an empty announcement, so the two never collapse.
    text: str | None = None
    text_error: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.text)


@dataclass
class Feed:
    items: list[Announcement] = field(default_factory=list)
    # Codes whose fetch failed outright, with the reason, for the email to name.
    errors: list[str] = field(default_factory=list)
    # Codes where five items did not reach back to the period start, so there
    # may be announcements this run cannot see.
    truncated: list[str] = field(default_factory=list)
    documents_fetched: int = 0
    document_budget_hit: bool = False

    @property
    def price_sensitive(self) -> list[Announcement]:
        return [a for a in self.items if a.price_sensitive]

    @property
    def tickers(self) -> list[str]:
        out: list[str] = []
        for a in self.items:
            if a.ticker not in out:
                out.append(a.ticker)
        return out


def asx_code(ticker: str) -> str:
    """`CWY.AX` -> `CWY`. The API speaks plain ASX codes."""
    return ticker.split(".")[0].strip().upper()


def _parse_when(raw: str) -> date | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).date()
    except (TypeError, ValueError):
        return None


def fetch_list(ticker: str, session=None) -> tuple[list[Announcement], str | None]:
    """Every announcement the feed will admit to for one holding.

    Returns the items and an error string, never raises. The caller decides
    whether an error is worth telling the reader about.
    """
    code = asx_code(ticker)
    url = f"{API}/companies/{code}/announcements"
    try:
        get = (session or requests).get
        resp = get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return [], f"{code}: the announcements feed returned HTTP {resp.status_code}"
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - any failure here is the same failure
        return [], f"{code}: the announcements feed could not be read ({exc})"

    raw_items = ((payload or {}).get("data") or {}).get("items") or []
    out: list[Announcement] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        when = _parse_when(row.get("date"))
        key = str(row.get("documentKey") or "").strip()
        headline = str(row.get("headline") or "").strip()
        if when is None or not key or not headline:
            # A row missing any of these cannot be shown honestly or fetched.
            continue
        out.append(
            Announcement(
                ticker=ticker,
                when=when,
                headline=headline,
                price_sensitive=bool(row.get("isPriceSensitive")),
                document_key=key,
                announcement_type=str(row.get("announcementType") or "").strip(),
            )
        )
    return out, None


def fetch_document_text(document_key: str, session=None) -> tuple[str | None, str | None]:
    """The announcement's own PDF, as text. Returns (text, error)."""
    url = f"{API}/file/{document_key}"
    try:
        get = (session or requests).get
        resp = get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=TIMEOUT)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        body = resp.content
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    if not body[:5].startswith(b"%PDF"):
        # The gateway answers a bad key with JSON. Saying so beats handing a
        # pypdf stack trace to the reader.
        return None, "the document did not come back as a PDF"

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if "cryptography" in detail:
            # Every document fails this way at once, and the cause is an
            # install problem rather than anything about the document. Say so,
            # and name the fix, rather than leaving a reader to infer it from
            # eight identical lines.
            return None, (
                "the PDF is encrypted and the `cryptography` package is missing "
                "— install pypdf[crypto]"
            )
        return None, f"the PDF could not be read ({detail})"

    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = "\n".join(line for line in text.splitlines() if line.strip())
    if not text.strip():
        # A scanned notice with no text layer. Real, and not an error.
        return None, "the PDF carries no extractable text (likely a scan)"
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n[truncated]"
    return text, None


# Registry housekeeping. These are filed constantly, say nothing about the
# business, and would eat the document budget before anything readable got to
# it. Matched on the headline, which for these is highly conventional.
BOILERPLATE = (
    "notification regarding unquoted securities",
    "notification of cessation of securities",
    "application for quotation of securities",
    "outstanding securities",
    "issued units outstanding",
    "notification of issue, conversion or payment",
    "daily fund update",
    "change of director",
)


# Headlines that always warrant the document, whatever the filer flagged and
# whatever the price did. This list exists because the price-movement signal
# failed too: Cleanaway's EQT takeover update came back unflagged in a week the
# stock moved 2.9%, just under the 3% threshold, so it would have been a bare
# headline. A takeover does not stop mattering at 2.9%.
SUBSTANTIVE = (
    "takeover", "scheme of arrangement", "scheme booklet", "proposal", "offer",
    "acquisition", "acquire", "merger", "bid", "trading halt", "suspension",
    "guidance", "results", "outlook", "earnings", "substantial holder",
    "substantial holding", "distribution", "dividend", "capital raising",
    "placement", "entitlement", "profit", "downgrade", "upgrade",
)


def _is_substantive(headline: str) -> bool:
    lowered = headline.lower()
    return any(k in lowered for k in SUBSTANTIVE)


def _is_boilerplate(headline: str) -> bool:
    lowered = headline.lower()
    return any(b in lowered for b in BOILERPLATE)


def _carries_nta(headline: str) -> bool:
    lowered = headline.lower()
    return (
        "net tangible asset" in lowered
        or "net asset value" in lowered
        or "nta" in lowered.split()
    )


def _wants_text(a: Announcement, prioritise: set[str]) -> bool:
    """Which announcements are worth a download.

    Three signals, because the obvious one is not reliable. `isPriceSensitive`
    alone missed Cleanaway's "Update in relation to EQT infrastructure
    proposal" — a live takeover process, flagged by the filer as NOT price
    sensitive. Price movement alone missed it too, the following week, when CWY
    moved 2.9% against a 3% threshold. So: the headline's own subject matter
    (`SUBSTANTIVE`) decides first, then the filer's flag, then whether the
    holding moved enough that the email is explaining it anyway.

    NTA and net-asset notices are always fetched: they carry the number the
    premium/discount to NAV is computed from.

    Registry housekeeping is never fetched, whatever else is true of it.
    """
    if _carries_nta(a.headline):
        return True
    if _is_boilerplate(a.headline):
        return False
    if _is_substantive(a.headline):
        return True
    return a.price_sensitive or a.ticker in prioritise


def collect(
    tickers,
    since: date | None,
    session=None,
    pause: float = 0.3,
    max_documents: int = MAX_DOCUMENTS,
    prioritise_tickers=None,
) -> Feed:
    """Announcements for these holdings in the period, with text where it earns it.

    `since` is the previous run's date; items on or before it are dropped so a
    weekly email does not repeat last week's news. `since=None` is a first run:
    everything the feed returns is shown, because there is no period to be
    outside of.

    `prioritise_tickers` are the holdings that moved materially this period.
    Their announcements get their document text fetched even when the filer did
    not mark them price-sensitive — see `_wants_text`.
    """
    feed = Feed()
    prioritise = {str(t) for t in (prioritise_tickers or ())}
    seen_keys: set[str] = set()
    text_by_key: dict[str, tuple[str | None, str | None]] = {}

    for ticker in tickers:
        items, error = fetch_list(ticker, session=session)
        if error:
            feed.errors.append(error)
            print(f"ANNOUNCEMENTS — {error}", file=sys.stderr)
            continue

        if since is not None and len(items) >= FEED_ITEMS and items:
            oldest = min(a.when for a in items)
            if oldest > since:
                # Five items and the oldest is still inside the period: there
                # may be more behind them that the feed will not hand over.
                feed.truncated.append(asx_code(ticker))

        for a in items:
            if since is not None and a.when <= since:
                continue
            feed.items.append(a)
        if pause:
            time.sleep(pause)

    # Oldest first reads like a timeline; price-sensitive items are picked out
    # separately by the renderer rather than reordered here.
    feed.items.sort(key=lambda a: (a.when, a.ticker, a.headline))

    # Substance before housekeeping, so a tight document budget is spent on
    # what might actually explain a move.
    ordered = sorted(
        feed.items,
        key=lambda a: (
            0 if _carries_nta(a.headline) else 1 if a.price_sensitive else 2,
            a.when,
        ),
    )
    for a in ordered:
        if not _wants_text(a, prioritise):
            continue
        if a.document_key in text_by_key:
            # One filing covering several funds: fetched once, shown for each.
            a.text, a.text_error = text_by_key[a.document_key]
            continue
        if feed.documents_fetched >= max_documents:
            feed.document_budget_hit = True
            a.text_error = "not downloaded: this run's document limit was reached"
            continue
        text, err = fetch_document_text(a.document_key, session=session)
        feed.documents_fetched += 1
        seen_keys.add(a.document_key)
        text_by_key[a.document_key] = (text, err)
        a.text, a.text_error = text, err
        if err:
            print(
                f"ANNOUNCEMENTS — {a.ticker} {a.when.isoformat()} "
                f"\"{a.headline[:60]}\": {err}",
                file=sys.stderr,
            )
        if pause:
            time.sleep(pause)

    return feed


def warnings_for(feed: Feed) -> list[str]:
    """What the reader must be told before trusting the digest."""
    out: list[str] = []
    if feed.errors:
        out.append(
            "Announcements could not be fetched for "
            + ", ".join(e.split(":")[0] for e in feed.errors)
            + ". Those holdings may have filed something this period that is "
            "missing below."
        )
    if feed.truncated:
        out.append(
            "The ASX feed returns only the five most recent announcements per "
            "code, and five were not enough to cover this whole period for "
            + ", ".join(feed.truncated)
            + ". There may be older announcements from this period that are not "
            "listed."
        )
    if feed.document_budget_hit:
        out.append(
            f"More than {MAX_DOCUMENTS} announcements warranted a document "
            "download this run, so the later ones are shown as headlines only."
        )
    return out
