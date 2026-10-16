"""Seeded helpers shared by the SYNTHETIC packet generator: names, identifiers, dates, money and text formats.

Everything here is made up. Institution names (banks, carriers, the credit bureau, lenders, CPA firms, card
processors) are invented word combinations chosen not to be the names of real companies (two earlier card processor
names, CARDSTREAM and TALLYPAY, turned out to be real payment companies and were replaced by invented words in
generator version synthetic-packets-v2). Every document that shows an institution also prints a "fictional" note:
the bank statement's "Fictional institution" line also covers the counterparties named in its rows (processors,
lenders, MCA funders), and the debt schedule says its lender names are fictional. FEINs use the never-issued "00-"
prefix, account numbers start with "99",
and e-mail addresses use the reserved example.com / example.org domains (RFC 2606).

All randomness comes from a random.Random and a Faker instance that the caller seeds per packet, so the same
packet seed always gives the same names and numbers.
"""

import hashlib
import math
import random
import unicodedata
from calendar import monthrange
from datetime import date, timedelta

from faker import Faker

MONTH_ABBREVIATIONS: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip
# Date formats used by the documents (statements, loss runs). Written out instead of strftime("%b") so the output
# never depends on the machine's locale.
DATE_FORMATS: tuple[str, ...] = ("mdy", "iso", "dmy_abbrev")

US_STATES: tuple[str, ...] = ("FL", "GA", "TX", "NC", "OH", "AZ", "TN", "PA", "IL", "CO", "MI", "WI", "MN", "VA")
DESCRIPTIVE_WORDS: tuple[str, ...] = (
    "Bright", "Harbor", "Summit", "Maple", "Golden", "Riverside", "Northside", "Blue Ridge", "Evergreen", "Prairie",
    "Lakeview", "Cedar", "Coastal", "Pioneer", "Granite", "Willow", "Keystone", "Meadow", "Beacon", "Sterling",
    "Copper", "Juniper", "Redwood", "Silver Creek", "Foxglove", "Ashford", "Brookside", "Cobble", "Driftwood",
)  # fmt: skip
SUFFIX_FOR_STRUCTURE: dict[str, tuple[str, ...]] = {
    "llc": ("LLC", "Group LLC", "Partners LLC"),
    "corporation": ("Inc.", "Corp.", "Company Inc."),
    "partnership": ("LP", "Partners LP"),
}
# Fictional institutions. Each one is printed with "(fictional)" or "Fictional institution" on its documents.
BANKS: tuple[str, ...] = ("Larkspur Valley Bank", "Quarry Lake Bank and Trust")
CREDIT_BUREAU = "Pinecrest Ledger Credit Bureau"
CPA_FIRMS: tuple[str, ...] = ("Abernathy and Quill CPAs", "Tolliver Marsh Accounting Group", "Wrenfield CPA Partners")
APPRAISERS: tuple[str, ...] = ("Stonebridge Valuation Partners", "Halloway Asset Appraisal", "Brindle Point Appraisers")
LENDERS: tuple[str, ...] = (
    "Copperline Equipment Finance", "Old Weir Credit Union", "Harrow Street Capital", "Bluestem Commercial Lending",
    "Fenwick Hollow Bank", "Tamarack Business Finance",
)  # fmt: skip
MCA_FUNDERS: tuple[str, ...] = ("Rapidleaf Merchant Funding", "Brookhaven Receivables Advance")
CARD_PROCESSORS: tuple[str, ...] = ("TERNWICK", "MERCHANTWAY", "MOSSGATE", "PAYLARK")
PAYROLL_PROVIDERS: tuple[str, ...] = ("PAYROLL GRIDWORKS", "PAYROLL BRIGHTPAY HR", "PAYROLL CLOVERDALE HR")
UTILITIES: tuple[str, ...] = ("CITY UTILITIES", "METRO POWER AND GAS", "COUNTY WATER DEPT", "FIBERNET TELECOM")
# Words for customer and supplier names in transaction descriptions. They avoid every keyword the deposit and
# withdrawal rules of docintel/deposits.py look for (TRANSFER, REFUND, RETURN, LOAN, MCA, NSF, CAPITAL, ...).
COUNTERPARTY_WORDS: tuple[str, ...] = (
    "ACORN", "BIRCHWOOD", "CANYON", "DELTA", "ELMWOOD", "FALCON", "GLACIER", "HILLTOP", "IRONGATE", "JASPER",
    "KINGSLEY", "LANTERN", "MAPLEWOOD", "NORTHGATE", "ORCHARD", "PARKSIDE", "QUAIL", "RIDGELINE", "STONEWALL",
    "TIMBER", "UNION SQUARE", "VALLEY", "WESTBROOK", "YARROW",
)  # fmt: skip
COUNTERPARTY_SUFFIXES: tuple[str, ...] = ("SUPPLY", "FOODS", "HOLDINGS", "SERVICES", "DISTRIBUTING", "PARTNERS")
BROKERAGES: tuple[str, ...] = ("Tidewater Lane Brokerage", "Corbel Street Insurance Brokers", "Pellham Risk Advisors")
INSPECTORS: tuple[str, ...] = ("Northlight Risk Engineering", "Basswood Loss Control Services")

_FAKER: Faker | None = None


def packet_seed(master_seed: int, packet_id: str) -> int:
    """A stable per-packet seed derived from the run's master seed and the packet id (32-bit)."""
    digest = hashlib.sha256(f"{master_seed}|{packet_id}".encode()).hexdigest()
    return int(digest[:8], 16)


def sub_seed(seed: int, purpose: str) -> int:
    """A stable seed for one purpose inside a packet (for example scanning one document)."""
    digest = hashlib.sha256(f"{seed}|{purpose}".encode()).hexdigest()
    return int(digest[:8], 16)


def seeded_faker(seed: int) -> Faker:
    """One shared en_US Faker instance, re-seeded for this packet (creating Faker objects is slow)."""
    global _FAKER
    if _FAKER is None:
        _FAKER = Faker("en_US")
    _FAKER.seed_instance(seed)
    return _FAKER


def ascii_text(text: str) -> str:
    """Drop accents and any non-ASCII character, so every document renders with the standard PDF fonts."""
    folded = unicodedata.normalize("NFKD", text)
    return folded.encode("ascii", "ignore").decode("ascii")


def log_uniform(rng: random.Random, low: float, high: float) -> float:
    """A value whose logarithm is uniform between log(low) and log(high)."""
    return math.exp(rng.uniform(math.log(low), math.log(high)))


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    """One key of `weights`, drawn with probability proportional to its weight (keys keep their order)."""
    keys = list(weights)
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def cents(value: float) -> float:
    """Round a dollar amount to cents."""
    return round(value + 0.0, 2)


def round_to(value: float, step: float) -> float:
    """Round to the nearest multiple of step (for example 1,000 for a loan amount)."""
    return float(round(value / step) * step)


def person_name(fake: Faker) -> str:
    """A made-up person name."""
    return ascii_text(f"{fake.first_name()} {fake.last_name()}")


def business_name(rng: random.Random, fake: Faker, kind_word: str, structure: str) -> str:
    """A made-up business name such as "Granite Dental Partners LLC" (half the time a surname instead)."""
    if rng.random() < 0.5:
        lead = rng.choice(DESCRIPTIVE_WORDS)
    else:
        lead = ascii_text(fake.last_name())
    suffix = rng.choice(SUFFIX_FOR_STRUCTURE.get(structure, ("LLC",)))
    return f"{lead} {kind_word} {suffix}"


def synthetic_fein(rng: random.Random) -> str:
    """A FEIN-shaped id with the never-issued prefix 00."""
    return f"00-{rng.randrange(0, 10**7):07d}"


def street_address(fake: Faker, state: str) -> str:
    """A made-up one-line US street address in the given state."""
    street = ascii_text(fake.street_address())
    city = ascii_text(fake.city())
    return f"{street}, {city}, {state} {fake.zipcode()}"


def counterparty(rng: random.Random) -> str:
    """An upper-case customer or supplier name for transaction descriptions."""
    return f"{rng.choice(COUNTERPARTY_WORDS)} {rng.choice(COUNTERPARTY_SUFFIXES)}"


def month_start(day: date) -> date:
    """First day of the month of `day`."""
    return date(day.year, day.month, 1)


def month_end(day: date) -> date:
    """Last day of the month of `day`."""
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def add_months(day: date, months: int) -> date:
    """The first day of the month `months` after (negative: before) the month of `day`."""
    total = day.year * 12 + (day.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def random_day(rng: random.Random, start: date, end: date) -> date:
    """A uniformly drawn date between start and end, inclusive."""
    return start + timedelta(days=rng.randint(0, (end - start).days))


def format_date(day: date, style: str) -> str:
    """A date in one of the document styles: 'mdy' 03/14/2026, 'iso' 2026-03-14, 'dmy_abbrev' 14-Mar-2026."""
    if style == "mdy":
        return f"{day.month:02d}/{day.day:02d}/{day.year:04d}"
    if style == "dmy_abbrev":
        return f"{day.day:02d}-{MONTH_ABBREVIATIONS[day.month - 1]}-{day.year:04d}"
    return day.isoformat()


def money(value: float, symbol: bool = True) -> str:
    """'$1,234.56' (or '1,234.56' without the symbol); negative amounts print as '-$1,234.56'."""
    sign = "-" if value < 0 else ""
    prefix = "$" if symbol else ""
    return f"{sign}{prefix}{abs(value):,.2f}"


def percent(value: float, decimals: int = 1) -> str:
    """'35.0%'."""
    return f"{value:.{decimals}f}%"


def yes_no(value: bool) -> str:
    """'Yes' or 'No'."""
    return "Yes" if value else "No"


def code_label(code: str) -> str:
    """A code printed as words: 'line_of_credit' -> 'Line of credit' (the code is recovered by the reverse rule)."""
    words = code.replace("_", " ")
    return words[:1].upper() + words[1:]
