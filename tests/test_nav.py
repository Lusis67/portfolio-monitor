"""Premium/discount to NAV, read out of the fund's own NTA announcement.

What matters: the real ASX layout parses; a figure that is not a unit price is
refused rather than reported; the 'as at' date travels with the number, because
a NAV two weeks older than the price it is compared against is indicative and
must not read as contemporaneous; and a refusal always says why."""

from harness import check, done, equal, heading
from datetime import date

from monitor.announcements import Announcement
from monitor import nav as nav_mod

# Verbatim shape of the KKC monthly notice, which is what this was built against.
KKC = """The Trust Company (RE Services) Limited
ABN 45 003 278 831, AFSL 235150

14 October 2026

MONTHLY NET TANGIBLE ASSET UPDATE

Net Tangible Assets (NTA) backing per unit of the KKR Credit Income Fund (ASX: KKC).

AS AT CLOSE OF BUSINESS  NTA PER UNIT*
30 September 2026 $2.3234

*All figures are stated in Australian dollars (AUD) unless otherwise stated.
Contact Details: Unit Registry 1300 123 456
"""


def ann(ticker, day, headline, text=None, err=None):
    a = Announcement(
        ticker=ticker,
        when=date(2026, 10, day),
        headline=headline,
        price_sensitive=False,
        document_key=f"k{day}",
    )
    a.text, a.text_error = text, err
    return a


heading("the real layout parses")
value, err = nav_mod.parse_nav(KKC)
equal("the unit price", value, 2.3234)
check("with no error", err is None)
equal("and the reporting date, not the filing date", nav_mod._reporting_date(KKC), date(2026, 9, 30))

heading("other common phrasings")
equal("NTA per security", nav_mod.parse_nav("NTA per security $1.0450")[0], 1.045)
equal("amount before the label", nav_mod.parse_nav("Net tangible assets of $3.21 per unit")[0], 3.21)
equal("net asset value wording", nav_mod.parse_nav("Net Asset Value per unit $0.87")[0], 0.87)
equal("spaced dollar sign", nav_mod.parse_nav("NTA per unit $ 2.50")[0], 2.50)

heading("what is refused, and why")
value, err = nav_mod.parse_nav("")
check("empty text is refused", value is None and "no text" in err)
value, err = nav_mod.parse_nav("Quarterly update. Contact us on 1300 123 456.")
check("text with no NTA is refused", value is None and "no NTA per unit" in err)
value, err = nav_mod.parse_nav("Net tangible assets per unit $45000000")
check("a figure too large to be a unit price is refused", value is None)
check("and the message says what it found, readably",
      "45,000,000" in err and "not a unit price" in err)
value, err = nav_mod.parse_nav("NTA per unit $0.000")
check("zero is refused too", value is None)

heading("the newest readable notice wins")
items = [
    ann("KKC.AX", 7, "Net Tangible Asset Backing", text="NTA per unit $2.1000"),
    ann("KKC.AX", 13, "Net Tangible Asset Backing", text=KKC),
    ann("CWY.AX", 11, "Update in relation to EQT proposal", text="nothing to see"),
]
n = nav_mod.nav_from_announcements("KKC.AX", items)
check("it parsed", n.ok)
equal("the most recent figure", n.nav_per_unit, 2.3234)
equal("filed on the 13th", n.source_date, date(2026, 10, 13))
equal("but as at 30 September", n.as_of, date(2026, 9, 30))
equal("and names its source", n.source_headline, "Net Tangible Asset Backing")

heading("the freshest VALUATION wins, not the latest filing")
# KKC files a weekly NTA valued to within a day or two and a monthly one valued
# to month end, and the monthly is often filed later. Picking the newest filing
# therefore chose a figure two weeks staler than one already in hand.
weekly = "NTA per unit $2.3097 AS AT the end of day 11 October 2026"
monthly = "MONTHLY NET TANGIBLE ASSET UPDATE NTA PER UNIT AS AT 30 September 2026 $2.3234"
items = [
    ann("KKC.AX", 11, "Net Tangible Asset Backing", text=weekly),
    ann("KKC.AX", 13, "Net Tangible Asset Backing", text=monthly),   # filed later
]
n_ = nav_mod.nav_from_announcements("KKC.AX", items)
equal("the more recently valued figure is used", n_.nav_per_unit, 2.3097)
equal("and its reporting date", n_.as_of, date(2026, 10, 11))
check("even though the other was filed two days later",
      max(i.when for i in items) == date(2026, 10, 13))

heading("a notice with no readable as-at date cannot win on freshness it lacks")
items = [
    ann("KKC.AX", 13, "Net Tangible Asset Backing", text="NTA per unit $9.99"),
    ann("KKC.AX", 11, "Net Tangible Asset Backing",
        text="NTA per unit $2.3097 AS AT the end of day 11 October 2026"),
]
n_ = nav_mod.nav_from_announcements("KKC.AX", items)
equal("the dated one is preferred over an undated later filing", n_.nav_per_unit, 2.3097)

heading("an unparseable newest notice falls back rather than losing the figure")
items = [
    ann("KKC.AX", 7, "Net Tangible Asset Backing", text="NTA per unit $2.1000"),
    ann("KKC.AX", 13, "Net Tangible Asset Backing", text="layout changed entirely"),
]
n = nav_mod.nav_from_announcements("KKC.AX", items)
check("the older one is used", n.ok and n.nav_per_unit == 2.1)

heading("absences are reported, not guessed")
n = nav_mod.nav_from_announcements("CWY.AX", items)
check("a holding with no NTA notice", not n.ok and "no NTA announcement" in n.error)
n = nav_mod.nav_from_announcements(
    "KKC.AX", [ann("KKC.AX", 13, "Net Tangible Asset Backing", err="HTTP 404")]
)
check("a notice whose document failed", not n.ok)
check("carries the document's own reason", "404" in n.error)

heading("the premium, and its sign")
good = nav_mod.Nav(ticker="KKC.AX", nav_per_unit=2.3234, as_of=date(2026, 9, 30),
                   source_headline="Net Tangible Asset Backing")
pd = nav_mod.premium_discount("KKC.AX", price=2.02, nav=good, today=date(2026, 10, 15))
equal("a discount is negative", round(pd.premium_pct, 2), -13.06)
check("and reads as a discount", pd.is_discount is True)
equal("measured against NAV, not price", round((2.02 - 2.3234) / 2.3234 * 100, 2), -13.06)
pd_up = nav_mod.premium_discount("KKC.AX", price=2.50, nav=good, today=date(2026, 10, 15))
check("a premium is positive", pd_up.premium_pct > 0 and not pd_up.is_discount)

heading("the NAV's age travels with it")
equal("days between the as-at date and today", pd.stale_days, 15)
equal("the as-at date is carried, not the filing date", pd.nav_as_of, date(2026, 9, 30))
no_today = nav_mod.premium_discount("KKC.AX", price=2.02, nav=good, today=None)
check("no today means no claim about staleness", no_today.stale_days is None)

heading("nothing is computed from a missing half")
check("no NAV, no figure", nav_mod.premium_discount("X", 1.0, nav_mod.Nav(ticker="X", error="nope")) is None)
check("no price, no figure", nav_mod.premium_discount("X", None, good) is None)

done()
