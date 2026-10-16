"""Open items: the open CONFLICTING or UNKNOWN problems of a case, each tied to an evidence request (Phase 4 D).

detect_open_items(state, context, policy_result, answered) finds every open problem of the CURRENT state, so an item
closes when a resolution removes its cause (a revenue gap that is gone, a document that arrived, a check that was
resolved). Problems detected from the documents only (a name that differs, embedded instructions) stay open until
their request is answered; the caller passes the answered item ids (the simulator does, after each reveal).

Item -> request (config/evidence_requests.yaml) and template (config/resolution_templates.yaml):
- revenue_gap (gap above 10.0%, NBC-3.4): the request follows the revenue_vs_deposits contradiction's explanation
  code: non_revenue_deposits_counted or unidentified_deposits -> revenue_breakdown_by_account; fiscal_year_timing ->
  fiscal_year_revenue_reconciliation; anything else (or no packet) -> revenue_source_documentation;
- missing_pages (a statement month missing from the 12-month window) -> missing_statement_month {month};
- missing_document (a checklist code not provided) -> missing_document {document}. All missing documents are asked
  for in ONE request (one checklist letter), so their request params list every missing code and the value of
  information values them together. A formation document supplies two codes (formation_documents and
  beneficial_ownership_certification, as in docintel checks_credit.documents_provided): they form one item. A
  missing statement month or a missing prior-carrier loss run covers its own checklist code;
- name_mismatch / fein_mismatch: transcription_error on every contradiction -> corrected_application, otherwise
  identity_reverification; owner_mismatch -> ownership_reconciliation;
- balance_discontinuity -> bank_verified_statement; debt_payment_mismatch -> debt_schedule_update;
  prompt_injection -> fraud_operations_review;
- insurance: loss_run_total_mismatch and paid_reserve_mismatch -> corrected_loss_run {carrier}; tiv_mismatch (above
  the HM-5.2 5.0% correction band) -> updated_property_schedule; claims_history_mismatch ->
  claims_history_reconciliation; a prior carrier without a loss run -> missing_loss_run {carrier} with the
  missing_loss_run template.
- tax_return_vs_pnl (NBC-9.1 integrity indicator) is listed as an open item with NO request: no catalog request or
  template resolves it, so it stays with Fraud Operations.
- startup credit (Founder Studio S6, open_items_startup.py): missing_equity_verification (NSP-3.1),
  missing_experience_evidence (NSP-2.1), unsupported_material_assumption (NSP-6.1, one per top driver) and
  missing_quotes, with the requests and templates of config/studio/startup_evidence_requests.yaml
  (startup_requests.py); a missing NSP-1.3 checklist document uses the shared missing_document item.

An item's detail text is built from ids, codes and numbers only, never from document text. Request params may hold
a carrier name read from a document: it is untrusted data, used only to say which loss run is asked for, and every
rendered view escapes it.
"""

from collections.abc import Collection

from uwcopilot.agent.applications import parse_application
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.policy_credit import BENIGN_GAP_PCT
from uwcopilot.evidence import open_item_params as params
from uwcopilot.evidence.ledger import required_documents_for
from uwcopilot.evidence.open_item_models import (
    LOSS_RUNS_CODE,
    PROBLEM_TITLES,
    STATEMENTS_CODE,
    ItemStatus,
    OpenItem,
    PacketContext,
    calc_value,
    contradiction_ids,
    doc_ids_of,
    flag_open,
    make_item,
    open_contradictions,
    packet_context,
)
from uwcopilot.evidence.open_items_insurance import insurance_items
from uwcopilot.evidence.open_items_startup import startup_items
from uwcopilot.evidence.state import EMBEDDED_INSTRUCTIONS_CHECK, CaseState, evaluate

REVENUE_GAP_REQUESTS: dict[str, str] = {
    "non_revenue_deposits_counted": "revenue_breakdown_by_account",
    "unidentified_deposits": "revenue_breakdown_by_account",
    "fiscal_year_timing": "fiscal_year_revenue_reconciliation",
}
DEFAULT_REVENUE_GAP_REQUEST = "revenue_source_documentation"
# Checklist codes one document supplies together (docintel checks_credit.documents_provided).
DOCUMENTS_DELIVERED_TOGETHER: dict[str, tuple[str, ...]] = {
    "formation_documents": ("formation_documents", "beneficial_ownership_certification"),
}
IDENTITY_CHECKS: dict[str, str] = {
    "name_consistency": "name_mismatch",
    "fein_consistency": "fein_mismatch",
    "owner_consistency": "owner_mismatch",
}
TRANSCRIPTION_ERROR = "transcription_error"
# A fixed order of problem types (integrity and identity first, then the money problems, then missing items).
PROBLEM_ORDER: tuple[str, ...] = (
    "prompt_injection",
    "balance_discontinuity",
    "tax_return_vs_pnl",
    "loss_run_total_mismatch",
    "paid_reserve_mismatch",
    "fein_mismatch",
    "name_mismatch",
    "owner_mismatch",
    "revenue_gap",
    "tiv_mismatch",
    "claims_history_mismatch",
    "debt_payment_mismatch",
    "missing_equity_verification",
    "missing_experience_evidence",
    "unsupported_material_assumption",
    "missing_quotes",
    "missing_pages",
    "missing_loss_run",
    "missing_document",
)


# --------------------------------------------------------------------------------------------------------------
# Credit
# --------------------------------------------------------------------------------------------------------------


def revenue_gap_items(state: CaseState, context: PacketContext, result: PolicyResult) -> list[OpenItem]:
    """A revenue gap above 10.0% in the current state (NBC-3.4)."""
    gap = calc_value(result, "CALC-revenue_gap_pct")
    verified = calc_value(result, "CALC-verified_annual_revenue")
    if gap is None or verified is None or gap <= BENIGN_GAP_PCT:
        return []
    related = open_contradictions(context, state, "revenue_vs_deposits")
    code = related[0].explanation_code if related else None
    stated = float(state.application["stated_annual_revenue"])
    return [
        make_item(
            "revenue_gap",
            "CONFLICTING",
            template_key="revenue_gap",
            request_id=REVENUE_GAP_REQUESTS.get(code or "", DEFAULT_REVENUE_GAP_REQUEST),
            placeholders={"verified": verified, "stated": stated, "midpoint": round((stated + verified) / 2, 2)},
            claim_ids=("APP-stated_annual_revenue", "CALC-verified_annual_revenue", "CHK-revenue_vs_deposits"),
            evidence_ids=contradiction_ids(related),
            doc_ids=doc_ids_of(related),
            explanation_code=code,
            detail=f"revenue gap {gap:.1f}% (above {BENIGN_GAP_PCT:.1f}%)",
        )
    ]


def missing_month_items(state: CaseState) -> list[OpenItem]:
    """Each month missing from the 12-month statement window (NBC-3.2 cannot verify revenue without it)."""
    months = params.missing_months(state.application)
    extra = {"documents_provided+": STATEMENTS_CODE} if len(months) == 1 else {}
    return [
        make_item(
            "missing_pages",
            "UNKNOWN",
            month,
            template_key="missing_pages",
            request_id="missing_statement_month",
            request_params={"month": month},
            placeholders=params.missing_month_values(state.application, month),
            extra_substitutions=extra,
            claim_ids=("APP-bank_statements.months_provided", "CHK-statement_months_complete"),
            detail=f"statement month {month} is not in the file",
        )
        for month in months
    ]


def document_groups(missing: list[str]) -> list[tuple[str, ...]]:
    """Missing codes grouped by the document that supplies them (one group per document), in checklist order."""
    groups: list[tuple[str, ...]] = []
    placed: set[str] = set()
    for code in missing:
        if code in placed:
            continue
        together = DOCUMENTS_DELIVERED_TOGETHER.get(code, (code,))
        group = tuple(item for item in together if item in missing) or (code,)
        groups.append(group)
        placed.update(group)
    return groups


def missing_document_items(state: CaseState, covered: set[str], answered: Collection[str] = ()) -> list[OpenItem]:
    """Required checklist documents not provided, as one request (codes another item asks for, and answered items,
    are left out)."""
    application = parse_application(state.application)
    provided = set(application.documents_provided)
    missing = [code for code in required_documents_for(application) if code not in provided and code not in covered]
    groups = [group for group in document_groups(missing) if f"OI-missing_document-{group[0]}" not in answered]
    joined = ", ".join(group[0] for group in groups)
    items = []
    for group in groups:
        extra = {"documents_provided+": list(group)} if len(group) > 1 else {}
        items.append(
            make_item(
                "missing_document",
                "UNKNOWN",
                group[0],
                template_key="missing_document",
                request_id="missing_document",
                request_params={"document": joined},
                placeholders={"document": group[0]},
                extra_substitutions=extra,
                claim_ids=tuple(f"DOC-{code}" for code in group),
                detail=f"required document(s) not in the file: {', '.join(group)}",
            )
        )
    return items


def identity_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Name, FEIN and ownership differences between documents (confirmed document-check flags)."""
    items = []
    for check_id, problem_type in IDENTITY_CHECKS.items():
        if not flag_open(state, check_id):
            continue
        related = open_contradictions(context, state, check_id)
        codes = [item.explanation_code for item in related]
        if problem_type == "owner_mismatch":
            request = "ownership_reconciliation"
            placeholders = {
                "owners_per_other_document": params.owners_per_other_document(state.application, context.extraction)
            }
        else:
            benign = bool(codes) and all(code == TRANSCRIPTION_ERROR for code in codes)
            request = "corrected_application" if benign else "identity_reverification"
            placeholders = {}
        items.append(
            make_item(
                problem_type,
                "CONFLICTING",
                template_key=problem_type,
                request_id=request,
                placeholders=placeholders,
                claim_ids=(f"CHK-{check_id}",),
                evidence_ids=contradiction_ids(related),
                doc_ids=doc_ids_of(related),
                explanation_code=codes[0] if len(set(codes)) == 1 else None,
                detail=f"check {check_id} failed ({len(related)} contradiction(s))",
            )
        )
    return items


def integrity_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Statement balances that do not continue (with a request) and tax return vs P&L (no catalog request)."""
    items = []
    if flag_open(state, "balance_continuity"):
        related = open_contradictions(context, state, "balance_continuity")
        items.append(
            make_item(
                "balance_discontinuity",
                "CONFLICTING",
                template_key="balance_discontinuity",
                request_id="bank_verified_statement",
                claim_ids=("CHK-balance_continuity",),
                evidence_ids=contradiction_ids(related),
                doc_ids=doc_ids_of(related),
                explanation_code="arithmetic",
                detail=f"check balance_continuity failed ({len(related)} contradiction(s))",
            )
        )
    if flag_open(state, "tax_return_vs_pnl"):
        related = open_contradictions(context, state, "tax_return_vs_pnl")
        items.append(
            make_item(
                "tax_return_vs_pnl",
                "CONFLICTING",
                template_key=None,
                request_id=None,
                claim_ids=("CHK-tax_return_vs_pnl",),
                evidence_ids=contradiction_ids(related),
                doc_ids=doc_ids_of(related),
                detail="no catalog request resolves this integrity indicator; it stays with Fraud Operations",
            )
        )
    return items


def debt_payment_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """A debt schedule payment that differs from the statements (NBC-4.1 uses the higher of the two)."""
    related = open_contradictions(context, state, "debt_payment_consistency")
    if not related:
        return []
    debt = state.application["debt"]
    return [
        make_item(
            "debt_payment_mismatch",
            "CONFLICTING",
            template_key="debt_payment_mismatch",
            request_id="debt_schedule_update",
            placeholders={
                "observed_payment": float(debt["existing_monthly_payment_observed"]),
                "scheduled_payment": float(debt["existing_monthly_payment_schedule"]),
            },
            claim_ids=(
                "APP-debt.existing_monthly_payment_schedule",
                "APP-debt.existing_monthly_payment_observed",
                "CHK-debt_payment_consistency",
            ),
            evidence_ids=contradiction_ids(related),
            doc_ids=doc_ids_of(related),
            explanation_code=related[0].explanation_code,
            detail=f"{len(related)} debt payment contradiction(s)",
        )
    ]


def injection_items(state: CaseState, context: PacketContext) -> list[OpenItem]:
    """Instruction-like text in a document (document injection flags), until Fraud Operations / the SIU review it."""
    if EMBEDDED_INSTRUCTIONS_CHECK in state.resolved_checks:
        return []
    fields = [flag.field for flag in state.injection_flags if flag.field.startswith("doc:")]
    if not fields:
        return []
    related = open_contradictions(context, state, EMBEDDED_INSTRUCTIONS_CHECK)
    doc_ids = tuple(dict.fromkeys(field.split(":")[1] for field in fields))
    return [
        make_item(
            "prompt_injection",
            "CONFLICTING",
            template_key="prompt_injection",
            request_id="fraud_operations_review",
            claim_ids=("CHK-embedded_instructions",),
            evidence_ids=contradiction_ids(related),
            doc_ids=doc_ids,
            explanation_code="embedded_instruction",
            detail=f"instruction-like text at {len(fields)} document location(s); never followed",
        )
    ]


# --------------------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------------------


def credit_items(state: CaseState, context: PacketContext, result: PolicyResult) -> list[OpenItem]:
    """The credit-only items (the shared ones are added by detect_open_items)."""
    items = revenue_gap_items(state, context, result)
    items += missing_month_items(state)
    items += integrity_items(state, context)
    items += debt_payment_items(state, context)
    return items


def detect_open_items(
    state: CaseState,
    context: PacketContext | None = None,
    policy_result: PolicyResult | None = None,
    answered: Collection[str] = (),
) -> list[OpenItem]:
    """Every open item of the current state, in PROBLEM_ORDER (answered item ids left out)."""
    packet = context or PacketContext()
    result = policy_result if policy_result is not None else evaluate(state)
    line = state.application.get("line")
    items: list[OpenItem] = []
    if line == "credit":
        items += credit_items(state, packet, result)
    elif line == "insurance":
        items += insurance_items(state, packet, result)
    elif line == "startup_credit":
        items += startup_items(state, result)
    items += identity_items(state, packet)
    items += injection_items(state, packet)
    covered = {STATEMENTS_CODE} if any(item.problem_type == "missing_pages" for item in items) else set()
    if any(item.problem_type == "missing_loss_run" for item in items):
        covered.add(LOSS_RUNS_CODE)
    items += missing_document_items(state, covered, answered)
    order = {problem_type: index for index, problem_type in enumerate(PROBLEM_ORDER)}
    items.sort(key=lambda item: order[item.problem_type])
    return [item for item in items if item.item_id not in answered]


__all__ = [
    "PROBLEM_ORDER",
    "PROBLEM_TITLES",
    "ItemStatus",
    "OpenItem",
    "PacketContext",
    "detect_open_items",
    "packet_context",
]
