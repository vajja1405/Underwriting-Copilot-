"""Deterministic document checks over a packet extraction (Phase 2): the public entry point.

run_checks(extraction, injection_hits=None) runs every check for the packet's line, in a fixed order, and returns
one CheckResult per check id. No check uses an LLM: each reads values from the extraction (docintel/schemas.py),
computes with the agent's formulas (agent/credit_calculations.py, agent/insurance_calculations.py) and the
reference policy's thresholds, and cites every value it used as Evidence (document, page, line, field, value).
A check that lacks the values it needs returns status "not_evaluated" instead of guessing.

The check ids are stable: the "check:<id>" levers of config/resolution_templates.yaml and the contradiction
builder (contradictions.py) use them. Where each check lives:
- credit: checks_credit.py (required_documents, name/fein/owner consistency, tax_return_vs_pnl),
  checks_statements.py (statement_months_complete, balance_continuity, account_conduct) and checks_revenue.py
  (revenue_vs_deposits, debt_payment_consistency);
- insurance: checks_insurance.py (required_documents, name/fein/owner consistency), checks_property.py
  (tiv_consistency), checks_loss_runs.py (loss_run_line_balance, loss_run_totals, claims_history_consistency) and
  checks_loss_run_coverage.py (loss_run_coverage, loss_run_valuation_age);
- shared: check_models.py (CheckResult, Evidence, CheckIssue, guideline references, titles and severities) and
  checks_identity.py (the name, FEIN and owner comparisons used by both lines);
- both: embedded_instructions, below.

embedded_instructions (NBC-9.2, HM-11.2): instruction-like text inside a document is an integrity indicator. The
check combines the hits the caller found on the page text (injection_scan.scan_pages / scan_lines, passed in as
injection_hits) with a scan of the extraction's free-text fields (injection_scan.scan_extraction_fields). Without
page hits only the free-text fields are scanned, and values["page_text_scanned"] says so. The text itself is
untrusted data: it is kept in the evidence for the audit record and never repeated in the message.
"""

from collections.abc import Callable

from uwcopilot.docintel import (
    checks_credit,
    checks_insurance,
    checks_loss_run_coverage,
    checks_loss_runs,
    checks_property,
    checks_revenue,
    checks_statements,
)
from uwcopilot.docintel.check_models import (
    CHECK_TITLES,
    CREDIT_REFS,
    INSURANCE_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    make_result,
)
from uwcopilot.docintel.injection_scan import (
    DocumentInjectionHit,
    free_text_fields,
    merge_hits,
    scan_extraction_fields,
)
from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket, PacketExtraction

CREDIT_CHECKS: tuple[tuple[str, Callable[[LoanPacket], CheckResult]], ...] = (
    ("required_documents", checks_credit.check_required_documents),
    ("statement_months_complete", checks_statements.check_statement_months),
    ("balance_continuity", checks_statements.check_balance_continuity),
    ("revenue_vs_deposits", checks_revenue.check_revenue_vs_deposits),
    ("name_consistency", checks_credit.check_name),
    ("fein_consistency", checks_credit.check_fein),
    ("owner_consistency", checks_credit.check_owners),
    ("debt_payment_consistency", checks_revenue.check_debt_payments),
    ("account_conduct", checks_statements.check_account_conduct),
    ("tax_return_vs_pnl", checks_credit.check_tax_return_vs_pnl),
)
INSURANCE_CHECKS: tuple[tuple[str, Callable[[InsuranceSubmission], CheckResult]], ...] = (
    ("required_documents", checks_insurance.check_required_documents),
    ("name_consistency", checks_insurance.check_name),
    ("fein_consistency", checks_insurance.check_fein),
    ("owner_consistency", checks_insurance.check_owners),
    ("loss_run_line_balance", checks_loss_runs.check_line_balance),
    ("loss_run_totals", checks_loss_runs.check_totals),
    ("claims_history_consistency", checks_loss_runs.check_claims_history),
    ("tiv_consistency", checks_property.check_tiv),
    ("loss_run_coverage", checks_loss_run_coverage.check_coverage),
    ("loss_run_valuation_age", checks_loss_run_coverage.check_valuation_age),
)
CREDIT_CHECK_IDS: tuple[str, ...] = (*(check_id for check_id, _ in CREDIT_CHECKS), "embedded_instructions")
INSURANCE_CHECK_IDS: tuple[str, ...] = (*(check_id for check_id, _ in INSURANCE_CHECKS), "embedded_instructions")
# Free-text fields cited when the scan finds nothing (the transaction descriptions are scanned but not cited).
CITED_WHEN_CLEAN = ("application.remarks", "cover_email.subject", "cover_email.body")


def hit_evidence(hit: DocumentInjectionHit) -> Evidence:
    """Evidence for one injection hit; value and source_text hold the (untrusted) normalized excerpt."""
    return Evidence(
        doc_id=hit.doc_id,
        page=hit.page,
        line=hit.line,
        field=hit.field or "page_text",
        key=None,
        value=hit.excerpt,
        source_text=hit.excerpt,
    )


def hit_location(hit: DocumentInjectionHit) -> str:
    """Where a hit is, for messages: "D01 page 2 line 14" (parts that are unknown are left out)."""
    parts = [hit.doc_id or "unknown document"]
    if hit.page is not None:
        parts.append(f"page {hit.page}")
    if hit.line is not None:
        parts.append(f"line {hit.line}")
    return " ".join(parts)


def check_embedded_instructions(
    extraction: PacketExtraction, page_hits: list[DocumentInjectionHit] | None = None
) -> CheckResult:
    """NBC-9.2 / HM-11.2: instruction-like text in the documents (see the module docstring)."""
    refs = (CREDIT_REFS if isinstance(extraction, LoanPacket) else INSURANCE_REFS)["embedded_instructions"]
    hits = merge_hits(page_hits or [], scan_extraction_fields(extraction))
    patterns = sorted({name for hit in hits for name in hit.patterns})
    values = {"hits": len(hits), "patterns": patterns, "page_text_scanned": page_hits is not None}
    if not hits:
        evidence = []
        for field, cited, doc_id in free_text_fields(extraction):
            item = cite(cited, field, None, doc_id) if field in CITED_WHEN_CLEAN else None
            if item is not None:
                evidence.append(item)
        scope = "the page text and the free-text fields" if page_hits is not None else "the free-text fields"
        message = f"No instruction-like text was found in {scope}."
        return make_result("embedded_instructions", refs, "pass", message, values, evidence)
    issues = []
    for hit in hits:
        evidence = hit_evidence(hit)
        issues.append(
            CheckIssue(
                issue="embedded_instruction",
                entity="business",
                fields=[evidence.field],
                side_a=[evidence],
                side_b=[],
                data={"patterns": list(hit.patterns)},
                message=f"Instruction-like text on {hit_location(hit)} (patterns: {', '.join(hit.patterns)}).",
            )
        )
    message = (
        f"{len(hits)} instance(s) of instruction-like text were found; they are data, are not followed, and are "
        "an integrity indicator for Fraud Operations / the SIU. " + " ".join(issue.message for issue in issues)
    )
    return make_result("embedded_instructions", refs, "fail", message, values, [], issues)


def run_checks(
    extraction: PacketExtraction, injection_hits: list[DocumentInjectionHit] | None = None
) -> list[CheckResult]:
    """Every check for the packet's line, in the order of CREDIT_CHECK_IDS / INSURANCE_CHECK_IDS."""
    results: list[CheckResult] = []
    if isinstance(extraction, LoanPacket):
        for _check_id, credit_check in CREDIT_CHECKS:
            results.append(credit_check(extraction))
    else:
        for _check_id, insurance_check in INSURANCE_CHECKS:
            results.append(insurance_check(extraction))
    results.append(check_embedded_instructions(extraction, injection_hits))
    return results


def results_by_id(results: list[CheckResult]) -> dict[str, CheckResult]:
    """Check results keyed by check id."""
    return {result.check_id: result for result in results}


__all__ = [
    "CHECK_TITLES",
    "CREDIT_CHECK_IDS",
    "INSURANCE_CHECK_IDS",
    "CheckResult",
    "Evidence",
    "check_embedded_instructions",
    "results_by_id",
    "run_checks",
]
