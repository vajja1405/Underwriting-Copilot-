"""Claims the package adds about the business, the owners, the loan schedule and the simulation's settings.

All go into the founder ledger through build_founder_ledger's extra_claims (ids start with "PKG."):
- the business name and every owner's name and ownership share: what the founder typed (USER_STATED; names are
  text claims printed as data, never used as codes);
- the loan's amortization schedule: the monthly payment and, for every loan year, interest, principal and the
  balance at the end of the year. They are arithmetic on the loan amount, the term and the note rate, so they follow
  the founder ledger's derived-number rule (narrative/derive.py): never more certain than the least certain input.
  When the note rate is an ASSUMPTION (prime plus the spread range), each figure carries the range it takes over the
  rate's range (evaluated on RATE_GRID_POINTS evenly spaced rates, ends and the note rate included);
- the simulation's horizon and its cash-check window in months: settings of the simulator run (MODEL_ESTIMATE of
  that run, exact), so labels such as "within 36 months" trace to a claim.
"""

from uwcopilot.agent.credit_calculations import amortizing_monthly_payment
from uwcopilot.evidence.ledger_models import Claim, Provenance
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import derived_claim, model_claim
from uwcopilot.studio.narrative.simulation_claims import SIMULATOR_MODEL
from uwcopilot.studio.package.project import FounderProject

MONTHS_PER_YEAR = 12
CASH_WINDOW_MONTHS = 24  # FeasibilityResult.p_cash_negative_within_24_months (studio/feasibility/result.py)
RUN_SETTING = "a setting of the simulation run, exact"
RATE_GRID_POINTS = 9


def text_claim(claim_id: str, label: str, value: str, field: str) -> Claim:
    """A text the founder typed: USER_STATED, printed as data."""
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit="TEXT",
        display="text",
        status="USER_STATED",
        provenance=Provenance(kind="user_input", ref=field),
        inputs={"field": field, "value": value},
    )


def owner_claims(project: FounderProject) -> list[Claim]:
    """Business name, and every owner's name and ownership share (USER_STATED)."""
    claims = [text_claim("PKG.business_name", "Business name", project.business_name, "business_name")]
    for index, owner in enumerate(project.owners):
        field = f"owners[{index}]"
        claims.append(
            text_claim(
                f"PKG.owner.{owner.owner_id}.name", f"Owner [{owner.owner_id}]: name", owner.name, f"{field}.name"
            )
        )
        claims.append(
            Claim(
                id=f"PKG.owner.{owner.owner_id}.ownership_pct",
                label=f"Owner [{owner.owner_id}]: ownership share",
                value=float(owner.ownership_pct),
                unit="PERCENT",
                display="pct1",
                status="USER_STATED",
                provenance=Provenance(kind="user_input", ref=f"{field}.ownership_pct"),
                inputs={"field": f"{field}.ownership_pct", "value": float(owner.ownership_pct)},
            )
        )
    return claims


def amortization(amount: float, rate_pct: float, term_months: int) -> tuple[float, list[tuple[float, float, float]]]:
    """(monthly payment, [(interest, principal, end balance) per loan year]) of a fully amortizing loan.

    Interest is the balance times the monthly rate each month; the last payment clears the balance exactly, so the
    final balance is zero. Amounts are rounded to the cent per year.
    """
    payment = amortizing_monthly_payment(amount, rate_pct, term_months)
    monthly_rate = rate_pct / 100.0 / MONTHS_PER_YEAR
    balance = amount
    years: list[tuple[float, float, float]] = []
    interest_sum = principal_sum = 0.0
    for month in range(1, term_months + 1):
        interest = balance * monthly_rate
        principal = balance if month == term_months else payment - interest
        balance -= principal
        interest_sum += interest
        principal_sum += principal
        if month % MONTHS_PER_YEAR == 0 or month == term_months:
            years.append((round(interest_sum, 2), round(principal_sum, 2), round(max(balance, 0.0), 2)))
            interest_sum = principal_sum = 0.0
    return round(payment, 2), years


def rate_bounds(rate: Claim) -> tuple[float, float]:
    """(low, high) of the note rate's range (its value twice when it has none)."""
    if rate.value_range is not None:
        return rate.value_range.low, rate.value_range.high
    return float(rate.value), float(rate.value)


def rate_grid(low: float, note: float, high: float) -> list[float]:
    """RATE_GRID_POINTS evenly spaced rates from low to high, plus the note rate itself."""
    if high <= low:
        return [note]
    step = (high - low) / (RATE_GRID_POINTS - 1)
    return sorted({*(low + step * index for index in range(RATE_GRID_POINTS)), note})


def schedule_ranges(
    amount: float, months: int, rates: list[float]
) -> tuple[tuple[float, float], list[list[tuple[float, float]]]]:
    """(payment range, per loan year [(low, high) of interest, principal, end balance]) over the rates."""
    runs = [amortization(amount, rate, months) for rate in rates]
    payments = [payment for payment, _ in runs]
    years = []
    for index in range(len(runs[0][1])):
        values = [run[1][index] for run in runs]
        years.append([(min(item[pos] for item in values), max(item[pos] for item in values)) for pos in range(3)])
    return (min(payments), max(payments)), years


def loan_schedule_claims(claims: dict[str, Claim]) -> list[Claim]:
    """Monthly payment and the yearly interest / principal / end balance of the requested loan (module docstring)."""
    loan, term, rate = claims[ids.FOUNDER_LOAN], claims[ids.FOUNDER_TERM], claims[ids.RATE_NOTE]
    amount, months, note = float(loan.value), int(term.value), float(rate.value)
    low_rate, high_rate = rate_bounds(rate)
    payment, years = amortization(amount, note, months)
    payment_range, year_ranges = schedule_ranges(amount, months, rate_grid(low_rate, note, high_rate))
    inputs = [loan, term, rate]
    formula = "fully amortizing monthly payment at the note rate (NBC-4.1 term loan formula)"
    schedule = [
        derived_claim(
            "PKG.loan.monthly_payment",
            "Loan monthly payment",
            payment,
            "USD",
            "usd2",
            formula,
            inputs,
            value_range=payment_range,
        )
    ]
    fields = (("interest", "interest paid"), ("principal", "principal repaid"), ("end_balance", "balance at year end"))
    for index, values in enumerate(years):
        year = index + 1
        for position, (field, words) in enumerate(fields):
            schedule.append(
                derived_claim(
                    f"PKG.loan.y{year}.{field}",
                    f"Loan year {year}: {words}",
                    values[position],
                    "USD",
                    "usd0",
                    f"amortization schedule, loan year {year}, {words}",
                    inputs,
                    value_range=year_ranges[index][position],
                )
            )
    return schedule


def two_decimal_percent(source: Claim, claim_id: str, label: str) -> Claim:
    """A percent claim printed with two decimals ("9.25", unit PERCENT, display ratio2): pct1 would print 9.25 as
    "9.2%". The value, status and range are the source claim's (derived_claim with that one input)."""
    value_range = None
    if source.value_range is not None:
        value_range = (source.value_range.low, source.value_range.high)
    return derived_claim(
        claim_id,
        label,
        float(source.value),
        "PERCENT",
        "ratio2",
        f"{source.id} printed with two decimals (percent per year)",
        [source],
        value_range=value_range,
    )


def rate_claims(claims: dict[str, Claim]) -> list[Claim]:
    """The note rate, the metro's prime rate and the program's rate stress with two decimals (when the ledger has a
    value for them; the rate stress exists for a variable rate, and S3's stress label prints it as "2.00 points")."""
    made = []
    for source_id, claim_id, label in (
        (ids.RATE_NOTE, "PKG.loan.note_rate_pct", "Note rate, percent per year"),
        ("SITE.metro.prime_rate", "PKG.site.metro.prime_rate_pct", "Prime rate for the metro, percent per year"),
        (
            ids.PROGRAM_RATE_STRESS_ADD,
            "PKG.program.rate_stress_points",
            "Rate stress of the program, percentage points",
        ),
    ):
        source = claims.get(source_id)
        if source is not None and source.value is not None and source.status != "UNKNOWN":
            made.append(two_decimal_percent(source, claim_id, label))
    return made


def run_setting_claims(result: FeasibilityResult, run: dict) -> list[Claim]:
    """The simulated horizon and the cash-check window, in months (settings of the run)."""
    return [
        model_claim(
            "PKG.sim.horizon_months",
            "Simulation horizon",
            result.months,
            "MONTHS",
            "months",
            SIMULATOR_MODEL,
            result.simulator_version,
            RUN_SETTING,
            run,
            "months",
        ),
        model_claim(
            "PKG.sim.cash_window_months",
            "Months the simulator's cash check covers",
            CASH_WINDOW_MONTHS,
            "MONTHS",
            "months",
            SIMULATOR_MODEL,
            result.simulator_version,
            RUN_SETTING,
            run,
            "p_cash_negative_within_24_months",
        ),
    ]
