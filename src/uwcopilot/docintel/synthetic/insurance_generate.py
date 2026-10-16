"""Build one SYNTHETIC insurance submission end to end (before rendering).

Fixed order so a seed always gives the same case: insured and request; locations; carriers, premiums and claims;
owners; loss-run problems; the application's stated loss history and TIV; the broker's cover email; then the
document-level problems.
"""

import random
from datetime import timedelta

from faker import Faker

from uwcopilot.docintel.synthetic.common import (
    BROKERAGES,
    INSPECTORS,
    US_STATES,
    business_name,
    log_uniform,
    person_name,
    round_to,
    synthetic_fein,
    weighted_choice,
)
from uwcopilot.docintel.synthetic.credit_build import draw_owners
from uwcopilot.docintel.synthetic.insurance_build import (
    BENIGN_REMARKS,
    CLASS_WORDS,
    CONTRACTOR_CLASSES,
    draw_application_date,
    draw_class,
    draw_locations,
    draw_runs,
    effective_date,
)
from uwcopilot.docintel.synthetic.insurance_case import InsuranceCase, LocationData
from uwcopilot.docintel.synthetic.problems import ProblemPlan, find_plan
from uwcopilot.docintel.synthetic.problems_insurance import (
    OUTDATED_SCHEDULE_NOTE,
    apply_document_problems,
    apply_loss_run_total,
    apply_paid_reserve,
    choose_missing_document,
    prepare_claims_history,
    stated_history,
    stated_tiv_for,
)

COVERAGE_MIX = {"package": 0.70, "property": 0.20, "general_liability": 0.10}
GL_LIMIT_MIX = {"standard": 0.85, "higher": 0.12, "above_capacity": 0.03}
GL_LIMITS = {
    "standard": (1_000_000.0, 2_000_000.0),
    "higher": (2_000_000.0, 4_000_000.0),
    "above_capacity": (3_000_000.0, 6_000_000.0),
}
INSPECTION_ABOVE_TIV = 5_000_000


def lines_for(coverage: str) -> list[str]:
    """Lines written under a coverage."""
    return ["property", "general_liability"] if coverage == "package" else [coverage]


def email_paragraphs(broker: str, brokerage: str, insured: str, effective: str, outdated: bool) -> list[str]:
    """The cover email body; the last paragraph is the sign-off (instructions are inserted before it)."""
    paragraphs = [
        "Hello Harborline new business team,",
        f"Please find attached the commercial submission for {insured}, with the application, the prior carrier "
        f"loss runs, the property schedule and the ownership statement, for an effective date of {effective}.",
    ]
    if outdated:
        paragraphs.append(OUTDATED_SCHEDULE_NOTE)
    paragraphs.append("Let me know if anything else is needed to quote.")
    paragraphs.append(f"Best regards, {broker}, {brokerage} (fictional)")
    return paragraphs


def largest_schedule_scale(stated_tiv: float, schedule: float, plans: list[ProblemPlan]) -> float:
    """The largest factor a tiv_mismatch resolution could scale the schedule values by (1.0 if none scales up).

    The application_correct option scales the schedule to the application TIV and the midpoint option half way, so
    the largest factor is application TIV / schedule TIV when the application is the higher figure.
    """
    if find_plan(plans, "tiv_mismatch") is None:
        return 1.0
    return max(1.0, stated_tiv / schedule)


def inspected_locations(locations: list[LocationData], scale: float) -> list[LocationData]:
    """Locations with an inspection report: TIV above $5,000,000 on the schedule or after scaling it by `scale`.

    A location that crosses $5,000,000 only when the application's higher TIV is right also has an inspection
    report, so resolving the TIV difference never creates a requirement (HM-3.1) that no document can meet.
    """
    return [location for location in locations if location.tiv * scale > INSPECTION_ABOVE_TIV - 1.0]


def generate_insurance_case(rng: random.Random, fake: Faker, number: int, plans: list[ProblemPlan]) -> InsuranceCase:
    """One SYNTHETIC insurance case with its planned problems applied."""
    application_date = draw_application_date(rng)
    effective = effective_date(application_date)
    class_code = draw_class(rng)
    structure = "llc" if rng.random() < 0.65 else "corporation"
    state = rng.choice(US_STATES)
    coverage = weighted_choice(rng, COVERAGE_MIX)
    lines = lines_for(coverage)
    gl_limits = GL_LIMITS[weighted_choice(rng, GL_LIMIT_MIX)] if "general_liability" in lines else None
    revenue = round_to(log_uniform(rng, 400_000, 20_000_000), 1_000)
    locations = draw_locations(rng, fake, state, class_code, effective.year)
    schedule = round(sum(location.tiv for location in locations), 2)
    runs = draw_runs(rng, effective, application_date, lines, schedule, revenue)
    min_owners = 2 if find_plan(plans, "owner_mismatch") else 1
    owners = draw_owners(rng, fake, structure, state, application_date, min_owners)
    current_start = effective.replace(year=effective.year - 1)

    choose_missing_document(rng, plans, runs)
    apply_loss_run_total(rng, plans, runs)
    apply_paid_reserve(rng, plans, runs)
    prepare_claims_history(rng, plans, runs, current_start)
    count, incurred = stated_history(rng, plans, runs, current_start)
    stated_tiv = stated_tiv_for(rng, plans, schedule)
    tiv_plan = find_plan(plans, "tiv_mismatch")
    outdated = tiv_plan is not None and tiv_plan.cause == "benign_outdated_application"
    missing = find_plan(plans, "missing_document")
    insured = business_name(rng, fake, CLASS_WORDS.get(class_code, "Holdings"), structure)
    broker = person_name(fake)
    brokerage = rng.choice(BROKERAGES)
    contractor = class_code in CONTRACTOR_CLASSES
    case = InsuranceCase(
        number=number,
        packet_id=f"PKT-I-{number:03d}",
        application_date=application_date,
        effective_date=effective,
        named_insured=insured,
        fein=synthetic_fein(rng),
        years_in_business=rng.randint(0, 2) if rng.random() < 0.05 else rng.randint(3, 40),
        class_code=class_code,
        coverage=coverage,
        gl_limits=gl_limits,
        stated_tiv=stated_tiv,
        annual_revenue=revenue,
        annual_payroll=round_to(revenue * rng.uniform(0.20, 0.40), 1_000),
        subcontracted_pct=round(
            rng.uniform(10.0, 60.0) if contractor else rng.choice((0.0, 0.0, rng.uniform(1.0, 15.0))), 1
        ),
        coi_tracking=rng.random() < 0.6,
        foreign_products=rng.random() < 0.05,
        stated_claim_count=count,
        stated_total_incurred=incurred,
        owners=owners,
        locations=locations,
        runs=runs,
        remarks=rng.choice(BENIGN_REMARKS),
        broker_name=broker,
        brokerage=brokerage,
        signer=next(owner.name for owner in owners if owner.is_control_person),
        schedule_signed=application_date - timedelta(days=rng.randint(5, 120)),
        inspections={
            location.location_id: effective - timedelta(days=rng.randint(30, 300))
            for location in inspected_locations(locations, largest_schedule_scale(stated_tiv, schedule, plans))
        },
        inspector=rng.choice(INSPECTORS),
        email_paragraphs=email_paragraphs(broker, brokerage, insured, effective.isoformat(), outdated),
        missing_carrier=missing.details.get("carrier") if missing is not None else None,
    )
    apply_document_problems(rng, case, plans)
    return case
