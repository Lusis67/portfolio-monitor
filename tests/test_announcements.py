"""ASX announcements: what gets listed, what gets downloaded, and what is owned up to.

What matters: the five-item cap is detected and reported rather than read as
"nothing else happened"; one filing covering several funds is fetched once and
shown for each; registry housekeeping never spends the document budget; a
holding that moved gets its text even when the filer did not flag it
price-sensitive; and nothing here can fail the run."""

from harness import check, done, equal, heading
from datetime import date
from pathlib import Path

from monitor import announcements as ann

SINCE = date(2026, 10, 6)


def item(code, day, headline, key, ps=False):
    return {
        "announcementType": "X",
        "date": f"2026-10-{day:02d}T00:00:00.000Z",
        "documentKey": key,
        "fileSize": "10KB",
        "headline": headline,
        "isPriceSensitive": ps,
        "url": "",
    }


# A real, minimal PDF carrying one line of extractable text. Built by hand so
# the happy path is exercised for real rather than mocked around: pypdf parses
# this and returns the string below.
PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n"
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n"
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
    b"/Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n4 0 obj\n"
    b"<< /Length 69 >>\nstream\nBT /F1 12 Tf 20 100 Td "
    b"(NTA PER UNIT 30 September 2026 $2.3234) Tj ET\nendstream\nendobj\n5 0 obj\n"
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\nxref\n0 6\n"
    b"0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"0000000115 00000 n \n0000000241 00000 n \n0000000360 00000 n \ntrailer\n"
    b"<< /Size 6 /Root 1 0 R >>\nstartxref\n430\n%%EOF\n"
)
PDF_TEXT = "NTA PER UNIT 30 September 2026 $2.3234"

# The gateway answers an unknown key with JSON, so that is the honest default
# for a document the test did not bother to supply.
NOT_A_PDF = b'{"error":"no such document"}'


class Resp:
    def __init__(self, payload=None, body=None, status=200):
        self.status_code = status
        self._payload = payload
        self.content = body or b""

    def json(self):
        return self._payload


class FakeSession:
    """Serves announcement lists and documents, and counts what was asked for."""

    def __init__(self, lists, docs=None, doc_status=200):
        self.lists = lists
        self.docs = docs or {}
        self.doc_status = doc_status
        self.list_calls = []
        self.doc_calls = []

    def get(self, url, headers=None, timeout=None):
        if "/announcements" in url:
            code = url.split("/companies/")[1].split("/")[0]
            self.list_calls.append(code)
            if code not in self.lists:
                return Resp(status=404)
            return Resp(payload={"data": {"items": self.lists[code]}})
        key = url.split("/file/")[1]
        self.doc_calls.append(key)
        if self.doc_status != 200:
            return Resp(status=self.doc_status)
        return Resp(body=self.docs.get(key, NOT_A_PDF))


heading("the list is read, and unusable rows are dropped")
s = FakeSession({"AAA": [
    item("AAA", 10, "Half year results", "k1", ps=True),
    {"date": "2026-10-11T00:00:00.000Z", "headline": "no key", "documentKey": ""},
    {"documentKey": "k9", "headline": "no date"},
]})
items, err = ann.fetch_list("AAA.AX", session=s)
equal("only the usable row survives", len(items), 1)
equal("the ticker is kept as given", items[0].ticker, "AAA.AX")
equal("the date is parsed", items[0].when, date(2026, 10, 10))
check("the filer's flag is carried through", items[0].price_sensitive is True)
check("no error", err is None)

heading("a feed that fails is named, not raised")
s = FakeSession({})
items, err = ann.fetch_list("ZZZ.AX", session=s)
equal("no items", items, [])
check("the code is in the message", err is not None and "ZZZ" in err)
feed = ann.collect(["ZZZ.AX"], since=SINCE, session=s, pause=0)
equal("the run continues with nothing", feed.items, [])
equal("and the failure is recorded", len(feed.errors), 1)
check("warnings name it for the reader", any("ZZZ" in w for w in ann.warnings_for(feed)))

heading("the period start is respected")
s = FakeSession({"AAA": [
    item("AAA", 4, "Before the period", "old", ps=True),
    item("AAA", 6, "On the boundary", "edge", ps=True),
    item("AAA", 9, "Inside the period", "new", ps=True),
]})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("only what is newer than `since`", [a.headline for a in feed.items], ["Inside the period"])
feed = ann.collect(["AAA.AX"], since=None, session=s, pause=0)
equal("a first run shows everything", len(feed.items), 3)

heading("the five-item cap is detected and declared")
five_new = [item("AAA", d, f"Thing {d}", f"k{d}") for d in (9, 10, 11, 12, 13)]
s = FakeSession({"AAA": five_new})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("the code is marked truncated", feed.truncated, ["AAA"])
check("and the reader is told the list may be short",
      any("five" in w and "not listed" in w for w in ann.warnings_for(feed)))
# Five items whose oldest predates the period means we did see the whole period.
s = FakeSession({"AAA": [item("AAA", d, f"Thing {d}", f"k{d}") for d in (1, 10, 11, 12, 13)]})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("reaching back past `since` is not truncation", feed.truncated, [])

heading("one filing covering several funds is fetched once")
shared = [item("X", 9, "Outstanding Securities September 2026", "shared")]
s = FakeSession({"VGS": list(shared), "VVLU": list(shared)},
                docs={"shared": b"%PDF-1.4 body"})
feed = ann.collect(["VGS.AX", "VVLU.AX"], since=SINCE, session=s, pause=0)
equal("both holdings list it", len(feed.items), 2)
equal("but it is housekeeping, so nothing was downloaded", s.doc_calls, [])

heading("registry housekeeping never spends the budget")
s = FakeSession({"AAA": [
    item("AAA", 9, "Notification regarding unquoted securities - AAA", "b1"),
    item("AAA", 10, "Application for quotation of securities - AAA", "b2"),
    item("AAA", 11, "Notification of cessation of securities - AAA", "b3"),
    item("AAA", 12, "Change in substantial holding", "real", ps=True),
]})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("only the substantive one was downloaded", s.doc_calls, ["real"])
equal("all four are still listed", len(feed.items), 4)

heading("a holding that moved gets its text, flag or no flag")
# An ordinary headline on a holding nobody is explaining stays a headline.
s = FakeSession({"AAA": [item("AAA", 11, "Investor presentation", "pres", ps=False)]})
ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("unflagged and unprioritised: headline only", s.doc_calls, [])
s = FakeSession({"AAA": [item("AAA", 11, "Investor presentation", "pres", ps=False)]})
ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0, prioritise_tickers=["AAA.AX"])
equal("prioritised as a mover: downloaded", s.doc_calls, ["pres"])

heading("substance beats the filer's flag and the price")
# Both earlier signals missed this: unflagged by the filer, and in a week the
# stock moved 2.9% against a 3% threshold.
s = FakeSession({"CWY": [item("CWY", 11, "Update in relation to EQT infrastructure proposal", "eqt")]})
feed = ann.collect(["CWY.AX"], since=SINCE, session=s, pause=0)
equal("a takeover update is downloaded on its headline alone", s.doc_calls, ["eqt"])
for headline in ("Trading Halt", "FY26 results", "Change in substantial holding",
                 "Guidance update", "Scheme of arrangement"):
    s2 = FakeSession({"AAA": [item("AAA", 9, headline, "k")]})
    ann.collect(["AAA.AX"], since=SINCE, session=s2, pause=0)
    check(f"and so is {headline!r}", s2.doc_calls == ["k"])
s3 = FakeSession({"AAA": [item("AAA", 9, "Investor presentation", "k")]})
ann.collect(["AAA.AX"], since=SINCE, session=s3, pause=0)
equal("but an ordinary headline is still just a headline", s3.doc_calls, [])

heading("an NTA notice is always downloaded")
s = FakeSession({"KKC": [item("KKC", 9, "Net Tangible Asset Backing", "nta", ps=False)]})
feed = ann.collect(["KKC.AX"], since=SINCE, session=s, pause=0)
equal("fetched without being flagged or prioritised", s.doc_calls, ["nta"])

heading("the document budget bounds the run")
many = [item("AAA", 7 + i, f"Results part {i}", f"d{i}", ps=True) for i in range(5)]
s = FakeSession({"AAA": many})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0, max_documents=2)
equal("only the budget was spent", len(s.doc_calls), 2)
check("the cap is recorded", feed.document_budget_hit is True)
check("and declared to the reader", any("headlines only" in w for w in ann.warnings_for(feed)))
skipped = [a for a in feed.items if a.text_error and "limit" in a.text_error]
check("the undownloaded ones say why", len(skipped) == 3)

heading("a real PDF is read")
s = FakeSession({"KKC": [item("KKC", 9, "Net Tangible Asset Backing", "nta")]},
                docs={"nta": PDF})
feed = ann.collect(["KKC.AX"], since=SINCE, session=s, pause=0)
equal("the text comes back", feed.items[0].text, PDF_TEXT)
check("and no error with it", feed.items[0].text_error is None)
check("has_text agrees", feed.items[0].has_text is True)

heading("a document that is not a PDF keeps its headline")
s = FakeSession({"AAA": [item("AAA", 9, "Half year results", "k1", ps=True)]},
                docs={"k1": b'{"error":"nope"}'})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("still listed", len(feed.items), 1)
check("no text", feed.items[0].text is None)
check("and the reason is on the item", "did not come back as a PDF" in (feed.items[0].text_error or ""))

s = FakeSession({"AAA": [item("AAA", 9, "Half year results", "k1", ps=True)]}, doc_status=404)
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
check("an HTTP failure is reported on the item", "404" in (feed.items[0].text_error or ""))
equal("and the announcement survives", len(feed.items), 1)

heading("odds and ends")
equal("the ASX code drops the suffix", ann.asx_code("CWY.AX"), "CWY")
equal("and tolerates a bare code", ann.asx_code("cwy"), "CWY")
s = FakeSession({"AAA": [item("AAA", 9, "Results", "k1", ps=True),
                         item("AAA", 10, "Other", "k2")]})
feed = ann.collect(["AAA.AX"], since=SINCE, session=s, pause=0)
equal("price_sensitive filters to the flagged ones", [a.headline for a in feed.price_sensitive], ["Results"])
equal("items are oldest first", [a.when.day for a in feed.items], [9, 10])
check("a clean run warns about nothing", ann.warnings_for(feed) == [])

heading("the dependency that made every document fail on the runner")
# The first scheduled run read zero documents: ASX PDFs are AES-256 encrypted
# and pypdf declares `cryptography` as an extra, not a dependency. This
# container happened to have it installed for unrelated reasons, so every local
# test passed while the runner failed on all eight. The guard is structural
# because the failure is an install problem, not a code path.
req = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
check("requirements.txt asks for the crypto extra", "pypdf[crypto]" in req)
check("and not the bare package", "\npypdf>=" not in req)
check("with a comment saying why", "AES" in req and "encrypted" in req.lower())


# pypdf raises this while PARSING, not while fetching, so the fake is injected
# at the import the module does inside fetch_document_text. Raising it from the
# HTTP layer instead would test the wrong handler and pass for the wrong reason.
import sys
import types

_real_pypdf = sys.modules.get("pypdf")
_fake = types.ModuleType("pypdf")


def _boom(*args, **kwargs):
    raise RuntimeError("cryptography>=3.1 is required for AES algorithm")


_fake.PdfReader = _boom
sys.modules["pypdf"] = _fake
try:
    s_ = FakeSession({"KKC": [item("KKC", 9, "Net Tangible Asset Backing", "nta")]},
                     docs={"nta": PDF})
    feed = ann.collect(["KKC.AX"], since=SINCE, session=s_, pause=0)
finally:
    if _real_pypdf is not None:
        sys.modules["pypdf"] = _real_pypdf
    else:
        del sys.modules["pypdf"]

equal("the announcement still appears", len(feed.items), 1)
err = feed.items[0].text_error or ""
check("the reason names the missing package", "cryptography" in err)
check("and names the fix", "pypdf[crypto]" in err)
check("rather than a bare exception dump", "RuntimeError" not in err)

done()
