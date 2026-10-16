"""InjectedProblem records of a SYNTHETIC insurance submission, built once the document ids are known.

doc_ids maps document keys to packet doc ids: "insurance_application", "property_schedule", "cover_email",
"ownership_statement", "loss_run:<carrier>", "inspection_report:<location id>".

Every problem that changes the claims of the latent world (a hidden claim, a missing loss run, a corrected claim
line, a claims-history correction) also sets the application's stated claim count and total incurred to the TRUE
loss history (true_history), so application_latent never carries a reconciliation difference no problem explains.

Latent outcomes do not follow the template priors (see the same note in credit_records.py): the latent option is the
one the cause implies, or the nearest one. For loss_run_total_mismatch the integrity variant is always rows_correct
and the hidden-claim variant always summary_correct_missing_claim; claims_history_mismatch is always
loss_runs_correct; a missing ownership statement is always provided_consistent.
"""

from datetime import date, timedelta

from uwcopilot.docintel.levers import SCHEDULE_TIV_LEVER, apply_substitutions, location_total
from uwcopilot.docintel.synthetic.common import money
from uwcopilot.docintel.synthetic.insurance_application import (
    INSPECTION_ABOVE_TIV,
    claim_dict,
    next_year,
    year_dicts,
)
from uwcopilot.docintel.synthetic.insurance_case import CarrierRun, ClaimData, InsuranceCase
from uwcopilot.docintel.synthetic.problems import ProblemPlan, ProblemRecord, nearest_option

# --------------------------------------------------------------------------------------------------------------


def true_history(case: InsuranceCase) -> dict:
    """The true claim count and total incurred over every carrier (hidden claims and missing loss runs included)."""
    claims = case.all_claims()
    return {
        "stated_claim_count": len(claims),
        "stated_total_incurred": round(sum(claim.incurred for claim in claims), 2),
    }


def run_by_carrier(case: InsuranceCase, carrier: str) -> CarrierRun:
    """The run of a carrier."""
    return next(run for run in case.runs if run.carrier == carrier)


def claim_path(app: dict, claim: ClaimData) -> str:
    """The dotted path of a printed claim inside application_as_documented."""
    target = claim_dict(claim)
    for year_index, year in enumerate(app["loss_runs"]["years"]):
        for claim_index, item in enumerate(year["claims"]):
            if item == target:
                return f"loss_runs.years[{year_index}].claims[{claim_index}]"
    for claim_index, item in enumerate(app["loss_runs"]["current_term_claims"]):
        if item == target:
            return f"loss_runs.current_term_claims[{claim_index}]"
    raise ValueError(f"claim {claim.claim_number} is not in the application")


def year_index(app: dict, claim: ClaimData) -> int:
    """Index of the application's loss-run year that contains the claim's date of loss."""
    for index, year in enumerate(app["loss_runs"]["years"]):
        start = date.fromisoformat(year["policy_year_start"])
        if start <= claim.date_of_loss < next_year(start):
            return index
    raise ValueError(f"no policy year for claim {claim.claim_number}")


def loss_run_total(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """The loss-run totals block disagrees with its claim rows (HM-3.3, HM-11.1)."""
    run = run_by_carrier(case, plan.details["carrier"])
    doc_id = doc_ids[f"loss_run:{run.carrier}"]
    count, paid, _, incurred = run.summary()
    if plan.cause == "integrity_arithmetic":
        extra = plan.details["extra"]
        anchor = run.years[-1].start
        line = "property" if run.years[-1].property_premium is not None else "general_liability"
        when = (anchor + timedelta(days=180)).isoformat()
        missing = {"date_of_loss": when, "line": line, "paid": extra, "reserve": 0.0, "incurred": extra}
        starts = [year["policy_year_start"] for year in app["loss_runs"]["years"]]
        index = starts.index(anchor.isoformat())
        latent = {"check:loss_run_totals": "resolved"}
    else:
        hidden = next(claim for claim in run.claims if claim.claim_number == plan.details["claim_number"])
        missing = claim_dict(hidden)
        index = year_index(app, hidden)
        latent = {
            "check:loss_run_totals": "resolved",
            f"+loss_runs.years[{index}].claims": missing,
            **true_history(case),
        }
    return ProblemRecord(
        doc_ids=[doc_id],
        fields=["loss_run.summary.total_incurred", "loss_run.claims.incurred"],
        values_by_doc={doc_id: incurred},
        description=f"The totals block of {run.carrier} ({doc_id}) shows {count} claims and {money(incurred)} "
        "incurred, "
        "which the claim rows do not add up to.",
        resolving_request="corrected_loss_run",
        resolving_request_params={"carrier": run.carrier},
        params={"year_index": index, "missing_claim": missing},
        latent_option_id=plan.details["latent"],
        latent_substitutions=latent,
    )


def find_claim(case: InsuranceCase, number: str) -> ClaimData:
    """A claim by its number."""
    return next(claim for claim in case.all_claims() if claim.claim_number == number)


def carrier_of(case: InsuranceCase, claim: ClaimData) -> CarrierRun:
    """The run that holds a claim."""
    return next(run for run in case.runs if claim in run.claims)


def paid_reserve(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """One claim line where paid + reserve differs from incurred by more than $1.00."""
    claim = find_claim(case, plan.details["claim_number"])
    path = claim_path(app, claim)
    doc_id = doc_ids[f"loss_run:{carrier_of(case, claim).carrier}"]
    if plan.details["latent"] == "incurred_correct":
        latent = {f"{path}.reserve": claim.reserve}
    else:
        latent = {f"{path}.incurred": claim.incurred, **true_history(case)}
    return ProblemRecord(
        doc_ids=[doc_id],
        fields=["loss_run.claims.paid", "loss_run.claims.reserve", "loss_run.claims.incurred"],
        values_by_doc={doc_id: claim.shown_incurred},
        description=f"Claim {claim.claim_number} on {doc_id}: paid {money(claim.paid)} plus reserve "
        f"{money(claim.shown_reserve)} is not incurred {money(claim.shown_incurred)}.",
        resolving_request="corrected_loss_run",
        resolving_request_params={"carrier": carrier_of(case, claim).carrier},
        params={
            "claim_path": path,
            "reserve_if_incurred_correct": round(claim.shown_incurred - claim.paid, 2),
            "paid_plus_reserve": round(claim.paid + claim.shown_reserve, 2),
        },
        latent_option_id=plan.details["latent"],
        latent_substitutions=latent,
    )


def newly_inspected(app: dict, target_tiv: float) -> bool:
    """True when scaling the schedule to target_tiv (the "@schedule_tiv" lever) puts a location above $5,000,000
    and the as-documented checklist does not list inspection_report yet. The packet holds the report of every such
    location (insurance_generate.inspected_locations).
    """
    if "inspection_report" in app["documents_provided"]:
        return False
    scaled = apply_substitutions(app, {SCHEDULE_TIV_LEVER: target_tiv})
    return any(location_total(location) > INSPECTION_ABOVE_TIV for location in scaled["locations"])


def tiv(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Application TIV vs the property schedule total (HM-5.2).

    When the latent schedule is scaled up so far that a location passes $5,000,000, the latent world also lists
    the inspection_report checklist code (the report is in the packet). Every option may list it too: it has no
    effect on the policy when no location needs an inspection.
    """
    schedule = case.schedule_tiv()
    stated = case.stated_tiv
    midpoint = round((schedule + stated) / 2, 2)
    latent = {
        "schedule_correct": {"stated_tiv": schedule},
        "application_correct": {"@schedule_tiv": stated},
        "midpoint": {"stated_tiv": midpoint, "@schedule_tiv": midpoint},
    }[plan.details["latent"]]
    extra = {}
    if newly_inspected(app, latent.get(SCHEDULE_TIV_LEVER, schedule)):
        latent = {**latent, "documents_provided+": "inspection_report"}
    if newly_inspected(app, max(stated, schedule)):
        extra = {"documents_provided+": "inspection_report"}
    values = {doc_ids["insurance_application"]: stated, doc_ids["property_schedule"]: schedule}
    return ProblemRecord(
        doc_ids=list(values),
        fields=["application.stated_tiv", "property_schedule.total_tiv"],
        values_by_doc=values,
        description=f"Application TIV {money(stated)} versus schedule TIV {money(schedule)}; true cause {plan.cause}.",
        resolving_request="updated_property_schedule",
        params={"schedule_tiv": schedule, "stated_tiv": stated, "midpoint": midpoint},
        latent_option_id=plan.details["latent"],
        latent_substitutions=latent,
        extra_option_substitutions=extra,
    )


def claims_history(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Application claim count / incurred vs the loss runs (HM-3.3)."""
    runs = app["loss_runs"]
    claims = [claim for year in runs["years"] for claim in year["claims"]] + runs["current_term_claims"]
    values: dict = {doc_ids["insurance_application"]: case.stated_claim_count}
    for run in case.present_runs():
        values[doc_ids[f"loss_run:{run.carrier}"]] = run.summary()[0]
    return ProblemRecord(
        doc_ids=list(values),
        fields=["application.stated_claim_count", "application.stated_total_incurred", "loss_run.summary.claim_count"],
        values_by_doc=values,
        description=f"The application states {case.stated_claim_count} claims and {money(case.stated_total_incurred)} "
        f"incurred; the loss runs show {len(claims)} ({plan.details['variant']}).",
        resolving_request="claims_history_reconciliation",
        params={
            "loss_run_count": len(claims),
            "loss_run_incurred": round(sum(claim["incurred"] for claim in claims), 2),
            "claims_missing_from_loss_runs": [],
        },
        latent_option_id="loss_runs_correct",
        latent_substitutions=true_history(case),
    )


def observed_rates(app: dict) -> dict[str, float]:
    """Incurred / earned premium per line over the loss-run years in the packet (0 when no premium)."""
    rates = {}
    for line, premium_key in (("property", "property_earned_premium"), ("general_liability", "gl_earned_premium")):
        premium = sum(year[premium_key] or 0.0 for year in app["loss_runs"]["years"])
        losses = sum(
            claim["incurred"] for year in app["loss_runs"]["years"] for claim in year["claims"] if claim["line"] == line
        )
        rates[line] = losses / premium if premium > 0 else 0.0
    return rates


def years_at_rate(run: CarrierRun, rates: dict[str, float], factor: float) -> list[dict]:
    """The missing run's years with one claim per written line at factor x the observed loss ratio."""
    years = year_dicts(run, [])
    for year in years:
        for line, key in (("property", "property_earned_premium"), ("general_liability", "gl_earned_premium")):
            incurred = round((year[key] or 0.0) * rates[line] * factor, 2)
            if incurred > 0:
                when = (date.fromisoformat(year["policy_year_start"]) + timedelta(days=180)).isoformat()
                year["claims"].append(
                    {"date_of_loss": when, "line": line, "paid": incurred, "reserve": 0.0, "incurred": incurred}
                )
    return years


def missing_document(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """A missing carrier loss run (missing_loss_run) or ownership statement (missing_document)."""
    if plan.details["kind"] == "ownership_statement":
        return ProblemRecord(
            doc_ids=[],
            fields=["documents_provided"],
            values_by_doc={},
            description="The submission has no beneficial ownership statement.",
            resolving_request="missing_document",
            resolving_request_params={"document": "beneficial_ownership_statement"},
            params={"document": "beneficial_ownership_statement"},
            latent_option_id="provided_consistent",
            latent_substitutions={"documents_provided+": "beneficial_ownership_statement"},
        )
    run = run_by_carrier(case, plan.details["carrier"])
    rates = observed_rates(app)
    true_years = year_dicts(run, run.claims, printed=False)
    true_total = sum(claim["incurred"] for year in true_years for claim in year["claims"])
    options = {
        "clean_history": 0.0,
        "typical_losses": sum(c["incurred"] for y in years_at_rate(run, rates, 1.0) for c in y["claims"]),
        "adverse_losses": sum(c["incurred"] for y in years_at_rate(run, rates, 2.0) for c in y["claims"]),
    }
    application = doc_ids["insurance_application"]
    return ProblemRecord(
        doc_ids=[application],
        fields=["application.prior_carriers"],
        values_by_doc={application: run.carrier},
        description=f"The loss run of prior carrier {run.carrier} ({len(run.years)} policy years) is missing.",
        resolving_request="missing_loss_run",
        resolving_request_params={"carrier": run.carrier},
        params={
            "years_without_claims": years_at_rate(run, rates, 0.0),
            "years_at_observed_rate": years_at_rate(run, rates, 1.0),
            "years_at_twice_observed_rate": years_at_rate(run, rates, 2.0),
        },
        latent_option_id=nearest_option(true_total, options),
        latent_substitutions={
            "+loss_runs.years": true_years,
            "documents_provided+": "loss_runs",
            **true_history(case),
        },
        extra_option_substitutions={"documents_provided+": "loss_runs"},
    )


NAME_FIELDS = {
    "property_schedule": "property_schedule.insured_name",
    "ownership_statement": "ownership_statement.entity_name",
}


def name_mismatch(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """A different insured name on one document."""
    key = plan.details["doc_key"]
    odd = doc_ids[key]
    benign = plan.cause == "benign_typo"
    application = doc_ids["insurance_application"]
    return ProblemRecord(
        doc_ids=[application, odd],
        fields=["application.named_insured", NAME_FIELDS.get(key, "loss_run.insured_name")],
        values_by_doc={application: case.named_insured, odd: plan.details["value"]},
        description=f"The insured name on {odd} differs from the application; true cause {plan.cause}.",
        resolving_request="corrected_application" if benign else "identity_reverification",
        params={},
        latent_option_id="typo_confirmed" if benign else "identity_issue",
        latent_substitutions={"check:name_consistency": "resolved" if benign else "confirmed"},
    )


def owner_mismatch(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Ownership statement differs from the application's owners."""
    owner_id = plan.details["owner_id"]
    application_owner = next(owner for owner in case.owners if owner.owner_id == owner_id)
    member = next((item for item in case.statement_members if item.owner_id == owner_id), None)
    by_name = {owner["name"]: owner for owner in app["owners"]}
    other = [{**by_name[item.name], "ownership_pct": item.ownership_pct} for item in case.statement_members]
    latent = plan.details["latent"]
    substitutions = {
        "application_correct": {"check:owner_consistency": "resolved"},
        "other_document_correct": {"check:owner_consistency": "resolved", "owners": other},
        "unresolved": {"check:owner_consistency": "confirmed"},
    }[latent]
    application = doc_ids["insurance_application"]
    statement = doc_ids["ownership_statement"]
    return ProblemRecord(
        doc_ids=[application, statement],
        fields=["application.owners.ownership_pct", "ownership_statement.owners.ownership_pct"],
        values_by_doc={
            application: application_owner.ownership_pct,
            statement: None if member is None else member.ownership_pct,
        },
        description=f"Owner {owner_id} ownership differs between the application and the ownership statement "
        f"({plan.details['variant']}); true cause {plan.cause}.",
        resolving_request="ownership_reconciliation",
        params={"owners_per_other_document": other},
        latent_option_id=latent,
        latent_substitutions=substitutions,
    )


def prompt_injection(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Instruction-like text in the cover email or the application remarks (HM-11.2): routed to the SIU."""
    key = "cover_email" if plan.details["location"] == "cover_email" else "insurance_application"
    doc_id = doc_ids[key]
    return ProblemRecord(
        doc_ids=[doc_id],
        fields=["cover_email.body" if key == "cover_email" else "application.remarks"],
        values_by_doc={doc_id: plan.details["text"]},
        description=f"Embedded instruction ({plan.details['technique']}) in the {plan.details['location']} "
        f"of {doc_id}.",
        resolving_request="fraud_operations_review",
        params={},
        latent_option_id="confirmed",
        latent_substitutions={"check:embedded_instructions": "confirmed"},
    )


BUILDERS = {
    "loss_run_total_mismatch": loss_run_total,
    "paid_reserve_mismatch": paid_reserve,
    "tiv_mismatch": tiv,
    "claims_history_mismatch": claims_history,
    "missing_document": missing_document,
    "name_mismatch": name_mismatch,
    "owner_mismatch": owner_mismatch,
    "prompt_injection": prompt_injection,
}


def insurance_record(case: InsuranceCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """The ProblemRecord of one applied insurance problem."""
    return BUILDERS[plan.problem_type](case, plan, app, doc_ids)
