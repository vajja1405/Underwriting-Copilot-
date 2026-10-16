"""Seeded draws for a SYNTHETIC insurance submission: insured, locations, prior carriers, premiums and claims.

Distributions (Phase 2 spec section 2.2): 1-4 locations with total TIV log-uniform $0.5M-$20M (no location above
$9.5M); 3-5 fictional prior carriers covering the five experience years plus the current term; earned premiums
from the TIV and revenue, growing 2% a year; claims sized so each line's 5-year loss ratio lands in a mixture
around the 55% / 70% / 100% thresholds and spread over distinct policy years (experience_claims); paid + reserve =
incurred on every true claim.
"""

import math
import random
from datetime import date, timedelta

from faker import Faker

from uwcopilot.agent.applications import INSURANCE_CLASSES
from uwcopilot.agent.policy_insurance import MIN_COST_PER_SQFT, PROHIBITED_CLASSES, RESTRICTED_CLASSES
from uwcopilot.docintel.synthetic.common import (
    add_months,
    cents,
    log_uniform,
    random_day,
    street_address,
    weighted_choice,
)
from uwcopilot.docintel.synthetic.insurance_case import CarrierRun, ClaimData, LocationData, PolicyYear

APPLICATION_WINDOW = (date(2026, 8, 17), date(2026, 9, 30))
# Fictional prior carriers; the index is the loss-run layout (render_loss_run.LAYOUTS).
CARRIERS: tuple[str, ...] = (
    "Quillfeather Mutual Insurance Co.",
    "Bramblewood Casualty Company",
    "Lanternfield Indemnity Group",
    "Ostrander Bay Assurance",
    "Thistlecombe Fire and Casualty",
)
CARRIER_PREFIXES: tuple[str, ...] = ("QFM", "BWC", "LIG", "OBA", "TFC")
ALLOWED_CLASSES = tuple(
    code for code in INSURANCE_CLASSES if code not in PROHIBITED_CLASSES and code not in RESTRICTED_CLASSES
)
CLASS_GROUPS = {
    "allowed": ALLOWED_CLASSES,
    "restricted": tuple(sorted(RESTRICTED_CLASSES)),
    "prohibited": tuple(sorted(PROHIBITED_CLASSES)),
}
CLASS_WORDS = {
    "office": "Professional Offices", "retail_store": "Outfitters", "light_assembly": "Assembly",
    "dental_office": "Dental Group", "restaurant_no_fryer": "Bistro", "restaurant_fryer": "Grill",
    "warehouse": "Distribution", "habitational": "Apartments", "general_contractor": "Contracting",
    "woodworking": "Woodworks", "furniture_manufacturing": "Furniture", "plastics_manufacturing": "Plastics",
    "auto_repair": "Auto Care", "body_shop": "Collision", "fuel_station": "Fuel Stop",
}  # fmt: skip
CONTRACTOR_CLASSES = {"general_contractor", "demolition_contractor"}
EXPERIENCE_YEARS = 5
MAX_LOCATION_TIV = 9_500_000
MAX_CLAIM = 240_000.0
MAX_CLAIMS = {"property": 5, "general_liability": 3}
CLAIM_SHARE_OF_PREMIUM = 0.85
BENIGN_REMARKS: tuple[str, ...] = (
    "Roof was replaced after hail damage; invoices are available on request.",
    "Sprinkler system was inspected last spring with no deficiencies.",
    "The insured is adding a second shift in the first quarter.",
    "Prior carrier non-renewed the account because it left the state, not because of losses.",
    "Security cameras and a monitored alarm were installed last year.",
    "The building owner handles snow removal under the lease.",
    "Owner asked whether a higher deductible would lower the premium.",
)


def draw_class(rng: random.Random) -> str:
    """A class code: about 85% allowed, 10% restricted, 5% prohibited (HM-2.2, HM-2.3)."""
    return rng.choice(CLASS_GROUPS[weighted_choice(rng, {"allowed": 0.85, "restricted": 0.10, "prohibited": 0.05})])


def effective_date(application_date: date) -> date:
    """The first day of the month two months after the application month."""
    return add_months(application_date, 2)


def location_count(rng: random.Random, total: float) -> int:
    """1-4 locations, enough that none is above $9.5M."""
    drawn = int(weighted_choice(rng, {"1": 0.45, "2": 0.30, "3": 0.15, "4": 0.10}))
    return max(drawn, math.ceil(total / MAX_LOCATION_TIV))


def draw_location(
    rng: random.Random, fake: Faker, location_id: str, state: str, tiv: float, occupancy: str, effective_year: int
) -> LocationData:
    """One location whose building, contents and income values add up to about `tiv` (whole dollars)."""
    construction = rng.choice(sorted(MIN_COST_PER_SQFT))
    building = round(tiv * rng.uniform(0.60, 0.75))
    contents = round(tiv * rng.uniform(0.12, 0.25))
    income = max(0, round(tiv) - building - contents)
    cost = MIN_COST_PER_SQFT[construction] * (
        rng.uniform(0.80, 0.98) if rng.random() < 0.03 else rng.uniform(1.05, 1.6)
    )
    built = rng.randint(1920, 1949) if rng.random() < 0.05 else rng.randint(1955, 2020)
    roof_age = rng.randint(21, 30) if rng.random() < 0.05 else rng.randint(1, 18)
    total = building + contents + income
    return LocationData(
        location_id=location_id,
        address=street_address(fake, state),
        construction_class=construction,
        occupancy=occupancy,
        year_built=built,
        roof_year=max(built, effective_year - roof_age),
        stories=rng.randint(1, 4),
        square_feet=max(500, round(building / cost)),
        sprinklered=(rng.random() < 0.95) if total > 2_500_000 else (rng.random() < 0.40),
        protection_class=rng.randint(9, 10) if rng.random() < 0.03 else rng.randint(1, 8),
        building_value=float(building),
        contents_value=float(contents),
        business_income_value=float(income),
    )


def draw_locations(
    rng: random.Random, fake: Faker, state: str, class_code: str, effective_year: int
) -> list[LocationData]:
    """The property schedule rows."""
    total = log_uniform(rng, 500_000, 20_000_000)
    count = location_count(rng, total)
    weights = [rng.uniform(0.5, 1.5) for _ in range(count)]
    shares = [total * weight / sum(weights) for weight in weights]
    occupancy = INSURANCE_CLASSES[class_code]
    return [
        draw_location(rng, fake, f"L{index}", state, min(share, MAX_LOCATION_TIV), occupancy, effective_year)
        for index, share in enumerate(shares, start=1)
    ]


def segments(rng: random.Random, years: int, carriers: int) -> list[list[int]]:
    """Split year indexes 0..years-1 into `carriers` consecutive non-empty segments."""
    cuts = sorted(rng.sample(range(1, years), carriers - 1))
    bounds = [0, *cuts, years]
    return [list(range(bounds[index], bounds[index + 1])) for index in range(carriers)]


def target_loss_ratio(rng: random.Random) -> float:
    """A 5-year loss ratio drawn around the HM-4.2 thresholds (55% / 70%) and the 100% annual referral."""
    group = weighted_choice(rng, {"zero": 0.10, "low": 0.45, "near": 0.30, "high": 0.15})
    if group == "zero":
        return 0.0
    if group == "low":
        return rng.uniform(0.05, 0.45)
    if group == "near":
        return rng.choice((0.55, 0.70)) + rng.uniform(-0.05, 0.05)
    return rng.uniform(0.75, 1.10)


def new_claim(rng: random.Random, number: str, start: date, line: str, incurred: float, recent: bool) -> ClaimData:
    """A balanced claim (paid + reserve = incurred); recent claims are often still open."""
    reserve = cents(incurred * rng.uniform(0.2, 0.8)) if recent and rng.random() < 0.6 else 0.0
    return ClaimData(
        claim_number=number,
        date_of_loss=start + timedelta(days=rng.randint(10, 350)),
        line=line,
        paid=cents(incurred - reserve),
        reserve=reserve,
        incurred=cents(incurred),
    )


class ClaimNumbers:
    """Unique claim numbers per carrier prefix, like "QFM23-04817"."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: set[str] = set()

    def next(self, prefix: str, year: int) -> str:
        """A new claim number for a carrier and policy year."""
        while True:
            number = f"{prefix}{year % 100:02d}-{self.rng.randint(10000, 99999)}"
            if number not in self.used:
                self.used.add(number)
                return number


def experience_claims(
    rng: random.Random, line: str, starts: list[date], premiums: list[float], numbers: ClaimNumbers, prefixes: list[str]
) -> list[tuple[int, ClaimData]]:
    """(year index, claim) for one line, sized to a drawn 5-year loss ratio.

    At most one claim per policy year (up to 5 property and 3 general liability claims, the HM-4.3 frequency
    limits), in distinct years drawn at random, each sized in proportion to its year's premium (+-15%). With enough
    claims for the target (about 85% of a year's premium each), an annual loss ratio above 100% only appears when
    the 5-year target itself is high, or for general liability above about 50%.
    """
    ratio = target_loss_ratio(rng)
    total = ratio * sum(premiums)
    if total <= 0:
        return []
    count = min(MAX_CLAIMS[line], max(1, math.ceil(ratio * len(premiums) / CLAIM_SHARE_OF_PREMIUM)))
    years = sorted(rng.sample(range(len(starts)), count))
    weights = [premiums[index] * rng.uniform(0.85, 1.15) for index in years]
    claims = []
    for index, weight in zip(years, weights, strict=True):
        incurred = min(MAX_CLAIM, total * weight / sum(weights))
        recent = index >= len(starts) - 2
        claim = new_claim(rng, numbers.next(prefixes[index], starts[index].year), starts[index], line, incurred, recent)
        claims.append((index, claim))
    return claims


def premium_growth(index: int) -> float:
    """Premiums grow 2% a year (index 0 is the oldest experience year, 5 the current term)."""
    return 0.90 + 0.02 * index


def draw_runs(
    rng: random.Random, effective: date, application_date: date, lines: list[str], schedule_tiv: float, revenue: float
) -> list[CarrierRun]:
    """3-5 prior carriers over the 5 experience years and the current term, with premiums and claims."""
    starts = [date(effective.year - EXPERIENCE_YEARS - 1 + index, effective.month, effective.day) for index in range(6)]
    carrier_count = int(weighted_choice(rng, {"3": 0.40, "4": 0.40, "5": 0.20}))
    chosen = rng.sample(range(len(CARRIERS)), carrier_count)
    owner_of_year: list[int] = [0] * 6
    for position, years in enumerate(segments(rng, 6, carrier_count)):
        for year in years:
            owner_of_year[year] = chosen[position]
    property_rate = rng.uniform(0.0025, 0.006)
    gl_base = max(1_500.0, revenue * rng.uniform(0.003, 0.009))
    premiums = {
        "property": [float(round(schedule_tiv * property_rate * premium_growth(index))) for index in range(6)],
        "general_liability": [float(round(gl_base * premium_growth(index))) for index in range(6)],
    }
    prefixes = [CARRIER_PREFIXES[owner_of_year[index]] for index in range(6)]
    numbers = ClaimNumbers(rng)
    claims: list[tuple[int, ClaimData]] = []
    for line in lines:
        claims.extend(experience_claims(rng, line, starts[:5], premiums[line][:5], numbers, prefixes))
    for _ in range(int(weighted_choice(rng, {"0": 0.6, "1": 0.3, "2": 0.1}))):
        line = rng.choice(lines)
        incurred = rng.uniform(1_000, 25_000)
        claim = new_claim(rng, numbers.next(prefixes[5], starts[5].year), starts[5], line, incurred, recent=True)
        claim.date_of_loss = min(claim.date_of_loss, application_date - timedelta(days=10))
        claims.append((5, claim))
    runs = []
    for carrier_index in sorted(set(owner_of_year), key=owner_of_year.index):
        years = [index for index in range(6) if owner_of_year[index] == carrier_index]
        runs.append(
            CarrierRun(
                carrier=CARRIERS[carrier_index],
                layout=carrier_index,
                policy_number=f"{CARRIER_PREFIXES[carrier_index]}-{rng.randint(1_000_000, 9_999_999)}",
                years=[
                    PolicyYear(
                        starts[index],
                        premiums["property"][index] if "property" in lines else None,
                        premiums["general_liability"][index] if "general_liability" in lines else None,
                    )
                    for index in years
                    if index < 5
                ],
                current_term=5 in years,
                current_premiums=(
                    premiums["property"][5] if "property" in lines else None,
                    premiums["general_liability"][5] if "general_liability" in lines else None,
                ),
                claims=[claim for index, claim in claims if owner_of_year[index] == carrier_index],
                valued_as_of=max(effective - timedelta(days=85), application_date - timedelta(days=rng.randint(3, 40))),
            )
        )
    return runs


def draw_application_date(rng: random.Random) -> date:
    """A date in the application window."""
    return random_day(rng, *APPLICATION_WINDOW)
