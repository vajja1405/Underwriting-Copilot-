"""Applying the planned problems to a SYNTHETIC insurance submission (Phase 2 spec section 2.3, insurance).

Called by insurance_generate.py in this order:
1. on the carriers' loss runs: choose_missing_document (a carrier's loss run or the ownership statement),
   apply_loss_run_total (summary block vs rows), apply_paid_reserve (one claim line out of balance),
   prepare_claims_history (make sure the claims the application will misstate exist);
2. stated_history: the application's claim count and total incurred (what the printed loss runs show, then the
   claims_history_mismatch understatement), and stated_tiv_for (the application TIV, with a tiv_mismatch);
3. apply_document_problems on the finished case (a different insured name on one document, different ownership
   on the ownership statement, embedded instructions in the cover email or the remarks).
Records are built in insurance_records.py.
"""

import random
from datetime import date, timedelta

from uwcopilot.docintel.synthetic.common import cents, round_to
from uwcopilot.docintel.synthetic.credit_case import InjectionPlan
from uwcopilot.docintel.synthetic.injections import choose_injection
from uwcopilot.docintel.synthetic.insurance_case import CarrierRun, ClaimData, InsuranceCase
from uwcopilot.docintel.synthetic.problems import ProblemPlan, find_plan
from uwcopilot.docintel.synthetic.problems_credit import alter_members, different_name, typo

OUTDATED_SCHEDULE_NOTE = (
    "The statement of values was updated this year to current replacement costs, so the schedule total is newer "
    "than the figure on the application."
)


def unique_number(runs: list[CarrierRun], prefix: str, year: int, rng: random.Random) -> str:
    """A claim number not used by any claim yet."""
    used = {claim.claim_number for run in runs for claim in run.claims}
    while True:
        number = f"{prefix}{year % 100:02d}-{rng.randint(10000, 99999)}"
        if number not in used:
            return number


def add_claim(rng: random.Random, runs: list[CarrierRun], run: CarrierRun, start: date, line: str) -> ClaimData:
    """Add a small closed claim to a run's policy year (used when a problem needs a claim that does not exist)."""
    incurred = cents(rng.uniform(2_000, 20_000))
    claim = ClaimData(
        claim_number=unique_number(runs, run.policy_number[:3], start.year, rng),
        date_of_loss=start + timedelta(days=rng.randint(20, 330)),
        line=line,
        paid=incurred,
        reserve=0.0,
        incurred=incurred,
    )
    run.claims.append(claim)
    return claim


def written_lines(run: CarrierRun) -> list[str]:
    """Lines the carrier wrote (from its premiums)."""
    property_premium, gl_premium = run.current_premiums
    lines = []
    if property_premium is not None:
        lines.append("property")
    if gl_premium is not None:
        lines.append("general_liability")
    return lines


def choose_missing_document(rng: random.Random, plans: list[ProblemPlan], runs: list[CarrierRun]) -> None:
    """A missing carrier loss run (60%; never the current-term carrier) or the ownership statement (40%)."""
    plan = find_plan(plans, "missing_document")
    if plan is None:
        return
    candidates = [run for run in runs if not run.current_term and run.years]
    wants_run = rng.random() < 0.6 or find_plan(plans, "owner_mismatch") is not None
    if wants_run and candidates:
        plan.details.update(kind="loss_run", carrier=rng.choice(candidates).carrier)
    else:
        plan.details.update(kind="ownership_statement")


def present(runs: list[CarrierRun], plans: list[ProblemPlan]) -> list[CarrierRun]:
    """Runs that will be in the packet."""
    plan = find_plan(plans, "missing_document")
    missing = plan.details.get("carrier") if plan is not None else None
    return [run for run in runs if run.carrier != missing]


def apply_loss_run_total(rng: random.Random, plans: list[ProblemPlan], runs: list[CarrierRun]) -> None:
    """The totals block disagrees with the claim rows (integrity) or counts a claim missing from the rows."""
    plan = find_plan(plans, "loss_run_total_mismatch")
    if plan is None:
        return
    run = rng.choice([item for item in present(runs, plans) if item.years])
    plan.details["carrier"] = run.carrier
    if plan.cause == "integrity_arithmetic":
        extra = cents(rng.uniform(1_500, 25_000))
        run.summary_extra_paid = extra
        run.summary_extra_incurred = extra
        plan.details.update(extra=extra, latent="rows_correct")
        return
    starts = [year.start for year in run.years]
    claims = [claim for claim in run.claims if claim.date_of_loss < starts[-1] + timedelta(days=365)]
    if not claims:
        claims = [add_claim(rng, runs, run, rng.choice(starts), rng.choice(written_lines(run)))]
    hidden = rng.choice(claims)
    hidden.hidden = True
    plan.details.update(claim_number=hidden.claim_number, latent="summary_correct_missing_claim")


def apply_paid_reserve(rng: random.Random, plans: list[ProblemPlan], runs: list[CarrierRun]) -> None:
    """One printed claim line where paid + reserve differs from incurred by more than $1.00 (HM-3.3, HM-11.1)."""
    plan = find_plan(plans, "paid_reserve_mismatch")
    if plan is None:
        return
    claims = [
        claim for run in present(runs, plans) for claim in run.claims if not claim.hidden and claim.incurred >= 2_000
    ]
    if not claims:
        run = rng.choice([item for item in present(runs, plans) if item.years])
        claims = [add_claim(rng, runs, run, run.years[-1].start, written_lines(run)[0])]
    claim = rng.choice(claims)
    change = cents(max(50.0, claim.incurred * rng.uniform(0.05, 0.15)))
    if rng.random() < 0.5:
        claim.printed_reserve = cents(claim.reserve + change)
        latent = "incurred_correct"
    else:
        claim.printed_incurred = cents(claim.incurred + change)
        latent = "paid_reserve_correct"
    plan.details.update(claim_number=claim.claim_number, latent=latent)


def prepare_claims_history(
    rng: random.Random, plans: list[ProblemPlan], runs: list[CarrierRun], current_start: date
) -> None:
    """Make sure the claims the application will leave out exist (current-term claims, or prior claims)."""
    plan = find_plan(plans, "claims_history_mismatch")
    if plan is None:
        return
    kept = present(runs, plans)
    printed = [claim for run in kept for claim in run.claims if not claim.hidden]
    current = [claim for claim in printed if claim.date_of_loss >= current_start]
    prior = [claim for claim in printed if claim.date_of_loss < current_start]
    if plan.cause == "benign_timing" and not current:
        run = next(item for item in kept if item.current_term)
        add_claim(rng, runs, run, current_start, rng.choice(written_lines(run)))
    if plan.cause == "unsupported_unexplained" and not prior:
        run = rng.choice([item for item in kept if item.years])
        add_claim(rng, runs, run, rng.choice(run.years).start, rng.choice(written_lines(run)))


def stated_history(
    rng: random.Random, plans: list[ProblemPlan], runs: list[CarrierRun], current_start: date
) -> tuple[int, float]:
    """The application's claim count and total incurred (see the module docstring).

    Without a claims_history_mismatch the application copies the printed loss runs. benign_timing leaves out the
    current-term claims; unsupported_unexplained leaves out prior claims (never as many as there are current-term
    claims, which would look like the benign timing pattern) or understates the incurred total by 30-60%.
    """
    printed = [claim for run in present(runs, plans) for claim in run.printed_claims()]
    count = len(printed)
    incurred = round(sum(claim.shown_incurred for claim in printed), 2)
    plan = find_plan(plans, "claims_history_mismatch")
    if plan is None:
        return count, incurred
    current = [claim for claim in printed if claim.date_of_loss >= current_start]
    prior = [claim for claim in printed if claim.date_of_loss < current_start]
    if plan.cause == "benign_timing":
        plan.details["variant"] = "current_term_omitted"
        return count - len(current), round(incurred - sum(claim.shown_incurred for claim in current), 2)
    choices = [number for number in range(1, min(2, len(prior)) + 1) if number != len(current)]
    if choices:
        omitted = rng.sample(prior, rng.choice(choices))
        plan.details["variant"] = "prior_claims_omitted"
        return count - len(omitted), round(incurred - sum(claim.shown_incurred for claim in omitted), 2)
    plan.details["variant"] = "incurred_understated"
    return count, round(incurred * rng.uniform(0.40, 0.70), 2)


def stated_tiv_for(rng: random.Random, plans: list[ProblemPlan], schedule: float) -> float:
    """The application TIV: within 3% of the schedule, or 6%-40% off for a tiv_mismatch (rounded to $1,000)."""
    plan = find_plan(plans, "tiv_mismatch")
    if plan is None:
        return round_to(schedule * (1.0 + rng.uniform(-0.03, 0.03)), 1_000)
    difference = rng.uniform(0.06, 0.40)
    if plan.cause == "benign_outdated_application":
        plan.details["latent"] = "schedule_correct"
        return round_to(schedule * (1.0 - difference), 1_000)
    plan.details["latent"] = "application_correct" if rng.random() < 0.5 else "midpoint"
    sign = 1.0 if rng.random() < 0.5 else -1.0
    return round_to(schedule * (1.0 + sign * difference), 1_000)


def apply_document_problems(rng: random.Random, case: InsuranceCase, plans: list[ProblemPlan]) -> None:
    """Missing ownership statement, a different insured name, different ownership, embedded instructions."""
    case.statement_members = [type(owner)(**vars(owner)) for owner in case.owners]
    missing = find_plan(plans, "missing_document")
    if missing is not None and missing.details["kind"] == "ownership_statement":
        case.missing_ownership_statement = True
    name = find_plan(plans, "name_mismatch")
    if name is not None:
        documents = ["property_schedule"] + [f"loss_run:{run.carrier}" for run in case.present_runs()]
        if not case.missing_ownership_statement:
            documents.append("ownership_statement")
        document = rng.choice(documents)
        value = (
            typo(rng, case.named_insured) if name.cause == "benign_typo" else different_name(rng, case.named_insured)
        )
        case.doc_names[document] = value
        name.details.update(doc_key=document, value=value)
    owners = find_plan(plans, "owner_mismatch")
    if owners is not None:
        case.statement_members = alter_members(rng, owners, case.owners)
    injection = find_plan(plans, "prompt_injection")
    if injection is not None:
        technique, text = choose_injection(rng, "insurance")
        location = "cover_email" if rng.random() < 0.6 else "remarks"
        injection.details.update(technique=technique, text=text, location=location)
        case.injection = InjectionPlan(location, technique, text)
        if location == "remarks":
            case.remarks = f"{case.remarks} {text}"
        else:
            case.email_paragraphs.insert(len(case.email_paragraphs) - 1, text)
