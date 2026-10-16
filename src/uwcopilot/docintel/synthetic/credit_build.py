"""Seeded draws for a SYNTHETIC loan case: the business, the loan request, the owners and the existing debts.

All distributions are the Phase 2 spec's targets (section 2.1): application date 2026-08-17..2026-09-30; product
mix term 50% / equipment 25% / line 25%; amounts log-uniform $50k-$900k (lines up to $500k; about 5% of requests
are deliberately outside the NBC-1.1 limits); terms 24-84 months; rates 7.5-14.0%; variable rate for lines and
about 30% of term loans; industries about 80% allowed, 15% restricted, 5% prohibited; 1-3 person owners plus
sometimes an organization owner; credit scores 600-820, mostly 660 or more; rare bankruptcy.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta

from faker import Faker

from uwcopilot.agent.applications import CREDIT_INDUSTRIES
from uwcopilot.agent.credit_calculations import amortizing_monthly_payment
from uwcopilot.agent.policy_credit import PROHIBITED_INDUSTRIES, RESTRICTED_INDUSTRIES
from uwcopilot.docintel.synthetic.common import (
    BANKS,
    CARD_PROCESSORS,
    LENDERS,
    MCA_FUNDERS,
    US_STATES,
    add_months,
    business_name,
    cents,
    log_uniform,
    person_name,
    random_day,
    round_to,
    street_address,
    synthetic_fein,
    weighted_choice,
)
from uwcopilot.docintel.synthetic.credit_case import ORGANIZATION, PERSON, Debt, Party

APPLICATION_WINDOW = (date(2026, 8, 17), date(2026, 9, 30))
PRODUCT_MIX = {"term_loan": 0.50, "equipment_loan": 0.25, "line_of_credit": 0.25}
TERMS = {"term_loan": (24, 36, 48, 60, 72, 84), "equipment_loan": (36, 48, 60, 72, 84), "line_of_credit": (12,)}
OUTSIDE_LIMITS_SHARE = 0.05
ALLOWED_INDUSTRIES = tuple(
    code for code in CREDIT_INDUSTRIES if code not in PROHIBITED_INDUSTRIES and code not in RESTRICTED_INDUSTRIES
)
INDUSTRY_GROUPS = {
    "allowed": ALLOWED_INDUSTRIES,
    "restricted": tuple(sorted(RESTRICTED_INDUSTRIES)),
    "prohibited": tuple(sorted(PROHIBITED_INDUSTRIES)),
}
INDUSTRY_GROUP_MIX = {"allowed": 0.80, "restricted": 0.15, "prohibited": 0.05}
# Industries whose customers mostly pay by card (card settlements, some cash).
CARD_INDUSTRIES = {
    "restaurant", "bakery", "hardware_retail", "dental_practice", "medical_clinic", "veterinary_clinic",
    "auto_repair", "cannabis_retail", "used_vehicle_dealer", "adult_entertainment", "casino_gambling",
}  # fmt: skip
CASH_HEAVY_INDUSTRIES = {"restaurant", "bakery", "hardware_retail", "cannabis_retail", "auto_repair"}
INDUSTRY_WORDS = {
    "dental_practice": "Dental", "professional_services": "Advisory", "machine_shop": "Machine Works",
    "software_development": "Software", "restaurant": "Kitchen", "landscaping": "Landscaping",
    "veterinary_clinic": "Veterinary", "hardware_retail": "Hardware", "medical_clinic": "Medical",
    "commercial_printing": "Printing", "cannabis_retail": "Botanicals", "long_haul_trucking": "Freight Lines",
    "bakery": "Bakery", "auto_repair": "Auto Care", "freight_broker": "Logistics",
    "construction_contractor": "Builders",
    "used_vehicle_dealer": "Motors", "money_services_business": "Money Services",
}  # fmt: skip
STRUCTURE_MIX = {"llc": 0.60, "corporation": 0.30, "partnership": 0.10}
TITLES = {
    "llc": ("Managing Member", "Member"),
    "corporation": ("President", "Vice President"),
    "partnership": ("General Partner", "Limited Partner"),
}
DEBT_TYPES = ("Term loan", "Equipment loan", "Vehicle loan", "Commercial mortgage")
BENIGN_REMARKS: tuple[str, ...] = (
    "Owner plans to open a second location next spring.",
    "Revenue is seasonal; the strongest months are November and December.",
    "Equipment vendor quote is attached; delivery is expected within six weeks.",
    "Most customers are local commercial accounts on net-30 terms.",
    "The owner's brother joined as operations manager in January.",
    "Prior lender relationship ended when that lender exited small business lending.",
    "Two delivery vans were sold this year; the proceeds are visible in the statements.",
    "The business renewed its lease for five years in the spring.",
)


@dataclass
class LoanRequest:
    """The loan request as first drawn (the amount may be reduced later so the target DSCR is plausible)."""

    product: str
    amount: float
    term_months: int
    rate_pct: float
    rate_type: str
    purpose: str


def draw_application_date(rng: random.Random) -> date:
    """A date in the application window."""
    return random_day(rng, *APPLICATION_WINDOW)


def draw_industry(rng: random.Random) -> str:
    """An industry code: mostly allowed, some restricted, few prohibited (NBC-2.2, NBC-2.3)."""
    group = weighted_choice(rng, INDUSTRY_GROUP_MIX)
    return rng.choice(INDUSTRY_GROUPS[group])


def months_in_business(rng: random.Random) -> int:
    """Months since formation: mostly 30-300, a few near-misses of the 24-month minimum (NBC-2.1)."""
    draw = rng.random()
    if draw < 0.05:
        return rng.randint(18, 23)
    if draw < 0.08:
        return rng.randint(24, 29)
    return rng.randint(30, 300)


def formation_date(application_date: date, months: int) -> date:
    """The formation date `months` before the application date (day kept at 28 or less)."""
    first = add_months(application_date, -months)
    return first.replace(day=min(application_date.day, 28))


def draw_loan_request(rng: random.Random) -> LoanRequest:
    """Product, amount, term, rate and purpose (about 5% outside the NBC-1.1 product limits)."""
    product = weighted_choice(rng, PRODUCT_MIX)
    term = rng.choice(TERMS[product])
    if product == "line_of_credit":
        amount = log_uniform(rng, 50_000, 500_000)
    else:
        amount = log_uniform(rng, 50_000, 900_000)
    if rng.random() < OUTSIDE_LIMITS_SHARE:
        if product == "line_of_credit":
            amount = rng.uniform(510_000, 650_000)
        else:
            term = rng.choice((96, 120))
    rate = round(rng.uniform(7.5, 14.0) * 4) / 4
    if product == "line_of_credit":
        rate_type = "variable"
    elif product == "term_loan" and rng.random() < 0.30:
        rate_type = "variable"
    else:
        rate_type = "fixed"
    purposes = {"term_loan": "expansion", "equipment_loan": "equipment_purchase", "line_of_credit": "working_capital"}
    purpose = purposes[product]
    if product == "term_loan" and rng.random() < 0.4:
        purpose = "working_capital"
    return LoanRequest(product, round_to(amount, 1_000), term, rate, rate_type, purpose)


def ownership_shares(rng: random.Random, count: int, total: int) -> list[float]:
    """Whole-number shares that add up to `total`, largest first, each at least 5."""
    while True:
        weights = [rng.uniform(1.0, 3.0) for _ in range(count)]
        shares = [round(total * weight / sum(weights)) for weight in weights]
        shares[0] += total - sum(shares)
        shares.sort(reverse=True)
        if min(shares) >= 5:
            return [float(share) for share in shares]


def credit_score(rng: random.Random) -> int:
    """600-820: about 90% at 660 or more, 7% near miss (640-659), 3% below 640."""
    draw = rng.random()
    if draw < 0.90:
        return rng.randint(660, 820)
    if draw < 0.97:
        return rng.randint(640, 659)
    return rng.randint(600, 639)


def draw_person(rng: random.Random, fake: Faker, owner_id: str, state: str, as_of: date) -> Party:
    """A made-up person owner with specimen ID details and a credit score (title and share set by the caller)."""
    birth = as_of - timedelta(days=rng.randint(28 * 365, 70 * 365))
    return Party(
        owner_id=owner_id,
        name=person_name(fake),
        entity_type=PERSON,
        title="",
        ownership_pct=0.0,
        is_control_person=False,
        date_of_birth=birth,
        id_number=f"SPC{rng.randrange(10**8, 10**9)}",
        id_expiry=as_of + timedelta(days=rng.randint(200, 2500)),
        address=street_address(fake, state),
        credit_score=credit_score(rng),
        bankruptcy_within_7_years=rng.random() < 0.03,
    )


def draw_owners(
    rng: random.Random, fake: Faker, structure: str, state: str, as_of: date, min_owners: int
) -> list[Party]:
    """1-3 person owners (O1 is the control person), plus an organization owner about 10% of the time."""
    people = max(min_owners, int(weighted_choice(rng, {"1": 0.35, "2": 0.40, "3": 0.25})))
    has_organization = rng.random() < 0.10
    organization_pct = rng.randint(10, 30) if has_organization else 0
    shares = ownership_shares(rng, people, 100 - organization_pct) if people > 1 else [100.0 - organization_pct]
    control_title, other_title = TITLES[structure]
    owners = []
    for index, share in enumerate(shares, start=1):
        person = draw_person(rng, fake, f"O{index}", state, as_of)
        person.ownership_pct = share
        person.is_control_person = index == 1
        person.title = control_title if index == 1 else other_title
        owners.append(person)
    if has_organization:
        owners.append(
            Party(
                owner_id=f"O{len(owners) + 1}",
                name=f"{fake.last_name()} Family Holdings LLC",
                entity_type=ORGANIZATION,
                title="Member" if structure == "llc" else "Shareholder",
                ownership_pct=float(organization_pct),
                is_control_person=False,
            )
        )
    return owners


def draw_loans(rng: random.Random, revenue: float, months: list[date], as_of: date, min_loans: int) -> list[Debt]:
    """0-3 existing loans sized to revenue; about 15% of cases funded one of them inside the statement window."""
    count = max(min_loans, int(weighted_choice(rng, {"0": 0.30, "1": 0.40, "2": 0.20, "3": 0.10})))
    lenders = rng.sample(LENDERS, count)
    loans = []
    for lender in lenders:
        balance = cents(revenue * rng.uniform(0.03, 0.30))
        remaining = rng.randint(18, 96)
        payment = cents(amortizing_monthly_payment(balance, rng.uniform(6.0, 12.0), remaining))
        loans.append(
            Debt(
                lender=lender,
                debt_type=rng.choice(DEBT_TYPES),
                kind="loan",
                balance=balance,
                scheduled_payment=payment,
                observed_payment=payment,
                maturity=add_months(as_of, remaining),
                payment_day=rng.randint(3, 25),
            )
        )
    if loans and rng.random() < 0.15:
        newest = loans[-1]
        funded_index = rng.randint(2, 10)
        start = months[funded_index]
        newest.funded_on = random_day(rng, start.replace(day=2), start.replace(day=20))
        newest.funded_amount = round_to(newest.balance * rng.uniform(1.0, 1.1), 1_000)
        newest.first_payment_index = funded_index + 1
    return loans


def draw_mca(rng: random.Random, revenue: float, months: list[date]) -> Debt | None:
    """A merchant cash advance in about 8% of cases: funded inside the window, remitted twice a month after it."""
    if rng.random() >= 0.08:
        return None
    funded_index = rng.randint(3, 9)
    start = months[funded_index]
    funded = round_to(revenue * rng.uniform(0.03, 0.10), 500)
    monthly = cents(funded * rng.uniform(1.25, 1.45) / rng.randint(6, 10))
    half = cents(monthly / 2)
    return Debt(
        lender=rng.choice(MCA_FUNDERS),
        debt_type="Merchant cash advance",
        kind="mca",
        balance=funded,
        scheduled_payment=cents(2 * half),
        observed_payment=cents(2 * half),
        maturity=add_months(start, 10),
        payment_day=1,
        first_payment_index=funded_index + 1,
        funded_on=random_day(rng, start.replace(day=2), start.replace(day=20)),
        funded_amount=funded,
    )


@dataclass
class Identity:
    """Who the applicant is."""

    legal_name: str
    fein: str
    structure: str
    state: str
    industry: str
    formation_date: date
    address: str
    bank: str
    bank_layout: int
    account_number: str
    card_processor: str


def draw_identity(rng: random.Random, fake: Faker, application_date: date) -> Identity:
    """Business name, FEIN, structure, state, industry, formation date, address and the bank account."""
    structure = weighted_choice(rng, STRUCTURE_MIX)
    industry = draw_industry(rng)
    state = rng.choice(US_STATES)
    bank_layout = rng.randrange(len(BANKS))
    digits = rng.randint(8, 10)
    return Identity(
        legal_name=business_name(rng, fake, INDUSTRY_WORDS.get(industry, "Services"), structure),
        fein=synthetic_fein(rng),
        structure=structure,
        state=state,
        industry=industry,
        formation_date=formation_date(application_date, months_in_business(rng)),
        address=street_address(fake, state),
        bank=BANKS[bank_layout],
        bank_layout=bank_layout,
        account_number="99" + "".join(str(rng.randrange(10)) for _ in range(digits)),
        card_processor=rng.choice(CARD_PROCESSORS),
    )


def largest_customer_pct(rng: random.Random, industry: str) -> float:
    """Largest customer share of revenue: small for card businesses; sometimes above 30% for others (NBC-2.3)."""
    if industry in CARD_INDUSTRIES:
        return round(rng.uniform(1.0, 10.0), 1)
    if rng.random() < 0.15:
        return round(rng.uniform(31.0, 55.0), 1)
    return round(rng.uniform(3.0, 28.0), 1)


def cash_share(rng: random.Random, industry: str) -> float:
    """Cash share of revenue deposits (kept under the 20% NBC-3.3 cap so the cap never binds)."""
    if industry in CASH_HEAVY_INDUSTRIES:
        return rng.uniform(0.04, 0.16)
    return rng.uniform(0.0, 0.04)
