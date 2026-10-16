"""Shared shapes of the open items (open_items.py, open_items_insurance.py): the OpenItem model, the packet
context and small helpers that read the case state, the policy result and the contradictions.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.contradictions import Contradiction, ContradictionReport
from uwcopilot.docintel.schemas import PacketExtraction
from uwcopilot.evidence.state import CaseState

ItemStatus = Literal["CONFLICTING", "UNKNOWN"]
STATEMENTS_CODE = "bank_statements_12_months"
LOSS_RUNS_CODE = "loss_runs"
PROBLEM_TITLES: dict[str, str] = {
    "revenue_gap": "Stated revenue differs from verified revenue by more than 10%",
    "missing_pages": "A bank statement month is missing",
    "missing_document": "Required documents are missing",
    "name_mismatch": "Business name differs between documents",
    "fein_mismatch": "FEIN differs between documents",
    "owner_mismatch": "Ownership differs between documents",
    "balance_discontinuity": "Statement balances do not continue or add up",
    "tax_return_vs_pnl": "Tax return receipts differ from the P&L revenue",
    "debt_payment_mismatch": "Debt schedule payment differs from the statements",
    "prompt_injection": "Instruction-like text in a document",
    "loss_run_total_mismatch": "Loss run totals do not equal the claim lines",
    "paid_reserve_mismatch": "A claim line's paid plus reserve differs from incurred",
    "tiv_mismatch": "Application TIV differs from the property schedule",
    "claims_history_mismatch": "Application loss history differs from the loss runs",
    "missing_loss_run": "A prior carrier's loss run is missing",
}


class OpenItem(BaseModel):
    """One open problem, the request that could resolve it and the case values for its resolution template."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    problem_type: str
    status: ItemStatus
    title: str
    template_key: str | None
    request_id: str | None
    request_params: dict[str, str] = {}
    placeholders: dict[str, Any] = {}
    extra_substitutions: dict[str, Any] = {}
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    doc_ids: tuple[str, ...] = ()
    explanation_code: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PacketContext:
    """What the documents showed besides the application: extraction, check results and contradictions.

    All parts are optional; an application typed by an applicant (no packet) has an empty context.
    """

    extraction: PacketExtraction | None = None
    checks: tuple[CheckResult, ...] = ()
    contradictions: tuple[Contradiction, ...] = field(default_factory=tuple)

    @property
    def has_packet(self) -> bool:
        """True when the case came from a document packet (checks were run)."""
        return bool(self.checks) or self.extraction is not None


def packet_context(
    extraction: PacketExtraction | None = None,
    checks: list[CheckResult] | None = None,
    contradictions: ContradictionReport | list[Contradiction] | None = None,
) -> PacketContext:
    """A PacketContext from the Phase 2 outputs (a report or a list of contradictions)."""
    found = contradictions.contradictions if isinstance(contradictions, ContradictionReport) else contradictions
    return PacketContext(extraction=extraction, checks=tuple(checks or ()), contradictions=tuple(found or ()))


def calc_value(result: PolicyResult, calc_id: str) -> float | None:
    """The value of one policy calculation (None when it was not computed)."""
    return next((item.value for item in result.calculations if item.calc_id == calc_id), None)


def open_contradictions(context: PacketContext, state: CaseState, check_id: str) -> list[Contradiction]:
    """The contradictions of one check, unless a resolution cleared that check."""
    if check_id in state.resolved_checks:
        return []
    return [item for item in context.contradictions if item.check_id == check_id]


def flag_open(state: CaseState, check_id: str) -> bool:
    """True when a confirmed document-check flag is on file and no resolution cleared the check."""
    if check_id in state.resolved_checks:
        return False
    return any(flag.check_id == check_id and flag.status == "confirmed" for flag in state.document_checks)


def contradiction_ids(items: list[Contradiction]) -> tuple[str, ...]:
    """The contradiction ids."""
    return tuple(item.contradiction_id for item in items)


def doc_ids_of(items: list[Contradiction]) -> tuple[str, ...]:
    """Distinct document ids of the contradictions, in first-seen order."""
    found: list[str] = []
    for item in items:
        found.extend(doc_id for doc_id in item.doc_ids if doc_id not in found)
    return tuple(found)


def make_item(problem_type: str, status: ItemStatus, qualifier: str = "", **fields: Any) -> OpenItem:
    """An OpenItem with id "OI-<problem_type>[-<qualifier>]" and the problem type's title."""
    item_id = f"OI-{problem_type}" + (f"-{qualifier}" if qualifier else "")
    return OpenItem(
        item_id=item_id,
        problem_type=problem_type,
        status=status,
        title=PROBLEM_TITLES[problem_type],
        **fields,
    )
