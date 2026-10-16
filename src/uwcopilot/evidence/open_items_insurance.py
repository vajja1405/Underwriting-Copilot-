"""Insurance open items (Harborline): loss-run integrity, TIV, claims history and missing prior-carrier loss runs.

See open_items.py for the item model and the request rules. Detection, per problem type:
- loss_run_total_mismatch: one item per open loss_run_totals contradiction (one loss run), while the confirmed
  loss_run_totals flag is open; request corrected_loss_run {carrier};
- paid_reserve_mismatch: one item per claim line of the CURRENT application whose paid + reserve differs from
  incurred by more than $1.00, linked to its loss_run_line_balance contradiction when the packet has one (matched
  on the three amounts); request corrected_loss_run {carrier};
- tiv_mismatch: the current application TIV is more than 5.0% from the schedule TIV (HM-5.2 correction band);
- claims_history_mismatch: the policy's HM-3.3 reconciliation finding is present, and (for a packet) the
  claims_history_consistency check found the difference;
- missing_loss_run: a prior carrier on the application without a loss run (loss_run_coverage "missing_carrier"),
  while the loss_runs checklist code is still missing.
"""

from typing import Any

from uwcopilot.agent.applications import Claim as LossClaim
from uwcopilot.agent.insurance_calculations import claim_out_of_balance
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.policy_insurance import TIV_CORRECTION_PCT
from uwcopilot.docintel.contradictions import Contradiction
from uwcopilot.evidence import open_item_params as params
from uwcopilot.evidence.open_item_models import (
    LOSS_RUNS_CODE,
    OpenItem,
    PacketContext,
    calc_value,
    contradiction_ids,
    doc_ids_of,
    flag_open,
    make_item,
    open_contradictions,
)
from uwcopilot.evidence.state import CaseState

AMOUNT_TOLERANCE = 0.005
RECONCILIATION_RULE = "loss_run_reconciliation"


def carrier_params(context: PacketContext, doc_id: str | None) -> dict[str, str]:
    """{"carrier": <name printed on the loss run>} when the extraction has it, else {}."""
    carrier = params.carrier_of(context.extraction, doc_id)
    return {"carrier": carrier} if carrier else {}


def loss_run_total_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Loss runs whose printed totals do not equal their claim lines (HM-11.1)."""
    if not flag_open(state, "loss_run_totals"):
        return []
    items = []
    for contradiction in open_contradictions(context, state, "loss_run_totals"):
        doc_id = contradiction.doc_ids[0] if contradiction.doc_ids else None
        items.append(
            make_item(
                "loss_run_total_mismatch",
                "CONFLICTING",
                doc_id or contradiction.contradiction_id,
                template_key="loss_run_total_mismatch",
                request_id="corrected_loss_run",
                request_params=carrier_params(context, doc_id),
                placeholders=params.missing_claim_values(state.application, context.extraction, contradiction),
                claim_ids=("CHK-loss_run_totals",),
                evidence_ids=(contradiction.contradiction_id,),
                doc_ids=tuple(contradiction.doc_ids),
                explanation_code=contradiction.explanation_code,
                detail=f"loss run {doc_id or 'unknown'}: printed totals differ from the claim lines",
            )
        )
    if not items:
        items.append(
            make_item(
                "loss_run_total_mismatch",
                "CONFLICTING",
                template_key="loss_run_total_mismatch",
                request_id="corrected_loss_run",
                placeholders={"year_index": None, "missing_claim": None},
                claim_ids=("CHK-loss_run_totals",),
                detail="a confirmed loss_run_totals flag with no contradiction on file",
            )
        )
    return items


def unbalanced_claims(application: dict) -> list[tuple[str, dict]]:
    """(application path, claim) of every claim line out of balance by more than $1.00."""
    runs = application["loss_runs"]
    found = []
    for year_index, year in enumerate(runs["years"]):
        for claim_index, claim in enumerate(year.get("claims", [])):
            found.append((f"loss_runs.years[{year_index}].claims[{claim_index}]", claim))
    for claim_index, claim in enumerate(runs.get("current_term_claims", [])):
        found.append((f"loss_runs.current_term_claims[{claim_index}]", claim))
    return [(path, claim) for path, claim in found if claim_out_of_balance(LossClaim.model_validate(claim))]


def same_amounts(claim: dict, contradiction: Contradiction) -> bool:
    """True when the contradiction's paid, reserve and incurred equal the claim's (to the cent)."""
    values = contradiction.values
    return all(
        values.get(name) is not None and abs(float(values[name]) - float(claim[name])) <= AMOUNT_TOLERANCE
        for name in ("paid", "reserve", "incurred")
    )


def paid_reserve_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Claim lines of the current application where paid + reserve differs from incurred (HM-3.3, HM-11.1)."""
    related = open_contradictions(context, state, "loss_run_line_balance")
    items = []
    used: set[str] = set()
    for path, claim in unbalanced_claims(state.application):
        match = next(
            (item for item in related if item.contradiction_id not in used and same_amounts(claim, item)), None
        )
        if match is not None:
            used.add(match.contradiction_id)
        doc_id = match.doc_ids[0] if match is not None and match.doc_ids else None
        paid, reserve, incurred = float(claim["paid"]), float(claim["reserve"]), float(claim["incurred"])
        items.append(
            make_item(
                "paid_reserve_mismatch",
                "CONFLICTING",
                match.contradiction_id if match is not None else path,
                template_key="paid_reserve_mismatch",
                request_id="corrected_loss_run",
                request_params=carrier_params(context, doc_id),
                placeholders={
                    "claim_path": path,
                    "reserve_if_incurred_correct": round(incurred - paid, 2),
                    "paid_plus_reserve": round(paid + reserve, 2),
                },
                claim_ids=("CHK-loss_run_line_balance",),
                evidence_ids=(match.contradiction_id,) if match is not None else (),
                doc_ids=(doc_id,) if doc_id else (),
                explanation_code=match.explanation_code if match is not None else None,
                detail=f"claim line {path}: paid plus reserve differs from incurred by more than 1.00 USD",
            )
        )
    return items


def tiv_items(state: CaseState, context: PacketContext, result: PolicyResult) -> list[OpenItem]:
    """Application TIV more than 5.0% from the schedule TIV (HM-5.2)."""
    difference = calc_value(result, "CALC-tiv_difference_pct")
    schedule = calc_value(result, "CALC-schedule_tiv")
    if difference is None or schedule is None or difference <= TIV_CORRECTION_PCT:
        return []
    stated = float(state.application["stated_tiv"])
    related = open_contradictions(context, state, "tiv_consistency")
    extra = params.inspection_extra(state.application, context.extraction, max(stated, schedule))
    return [
        make_item(
            "tiv_mismatch",
            "CONFLICTING",
            template_key="tiv_mismatch",
            request_id="updated_property_schedule",
            placeholders={
                "schedule_tiv": schedule,
                "stated_tiv": stated,
                "midpoint": round((stated + schedule) / 2, 2),
            },
            extra_substitutions=extra,
            claim_ids=("APP-stated_tiv", "CALC-schedule_tiv", "CHK-tiv_consistency"),
            evidence_ids=contradiction_ids(related),
            doc_ids=doc_ids_of(related),
            explanation_code=related[0].explanation_code if related else None,
            detail=f"TIV difference {difference:.1f}% (above {TIV_CORRECTION_PCT:.1f}%)",
        )
    ]


def claims_history_items(state: CaseState, context: PacketContext, result: PolicyResult) -> list[OpenItem]:
    """The application's claim count or incurred total does not reconcile to the loss runs (HM-3.3)."""
    if not any(finding.rule == RECONCILIATION_RULE for finding in result.findings):
        return []
    related = open_contradictions(context, state, "claims_history_consistency")
    if context.has_packet and not related:
        return []
    runs = state.application["loss_runs"]
    claims = [claim for year in runs["years"] for claim in year.get("claims", [])] + list(
        runs.get("current_term_claims", [])
    )
    return [
        make_item(
            "claims_history_mismatch",
            "CONFLICTING",
            template_key="claims_history_mismatch",
            request_id="claims_history_reconciliation",
            placeholders={
                "loss_run_count": len(claims),
                "loss_run_incurred": round(sum(float(claim["incurred"]) for claim in claims), 2),
                "claims_missing_from_loss_runs": [],
            },
            claim_ids=("APP-stated_claim_count", "APP-stated_total_incurred", "CHK-claims_history_consistency"),
            evidence_ids=contradiction_ids(related),
            doc_ids=doc_ids_of(related),
            explanation_code=related[0].explanation_code if related else None,
            detail="the application's loss history does not reconcile to the loss runs",
        )
    ]


def missing_carrier_issues(context: PacketContext) -> list[dict[str, Any]]:
    """The "missing_carrier" issue data of the loss_run_coverage check (carrier name, policy years)."""
    for result in context.checks:
        if result.check_id == "loss_run_coverage" and result.status == "fail":
            return [issue.data for issue in result.issues if issue.issue == "missing_carrier"]
    return []


def missing_loss_run_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Prior carriers on the application without a loss run, while the loss_runs code is still missing."""
    if LOSS_RUNS_CODE in state.application.get("documents_provided", []):
        return []
    issues = missing_carrier_issues(context)
    extra = {"documents_provided+": LOSS_RUNS_CODE} if len(issues) == 1 else {}
    items = []
    for number, issue in enumerate(issues, start=1):
        carrier = str(issue.get("carrier") or "")
        items.append(
            make_item(
                "missing_loss_run",
                "UNKNOWN",
                str(number),
                template_key="missing_loss_run",
                request_id="missing_loss_run",
                request_params={"carrier": carrier} if carrier else {},
                placeholders=params.missing_loss_run_values(state.application, context.extraction, carrier),
                extra_substitutions=extra,
                claim_ids=("DOC-loss_runs", "CHK-loss_run_coverage"),
                detail=f"prior carrier number {number} on the application has no loss run "
                f"({issue.get('policy_years', 0)} policy year(s))",
            )
        )
    return items


def insurance_items(state: CaseState, context: PacketContext, result: PolicyResult) -> list[OpenItem]:
    """Every insurance-only open item (the shared ones are added by open_items.detect_open_items)."""
    items = loss_run_total_items(state, context)
    items += paid_reserve_items(state, context)
    items += tiv_items(state, context, result)
    items += claims_history_items(state, context, result)
    items += missing_loss_run_items(state, context)
    return items
