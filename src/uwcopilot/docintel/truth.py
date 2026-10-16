"""Ground truth for the SYNTHETIC document packets (Phase 2). Written by the generator, read only by evaluation.

A packet is one PDF that holds every document of one SYNTHETIC loan application or insurance submission. Its
ground truth records three things:

1. What is printed where: every page's text lines (FieldTruth / PageTruth), so the evaluation can check that a
   cited page really contains the value it is cited for.
2. Two versions of the application (the agent's CreditApplication / InsuranceApplication as JSON):
   - application_as_documented: what a perfect reader of THESE documents would conclude, problems included
     (an overstated revenue figure stays overstated);
   - application_latent: the "real world" behind the documents, after every injected problem is resolved by
     its latent (true) resolution option. Nobody can see it; the Phase 4 simulator reveals it one request at a
     time.
3. Every injected problem (InjectedProblem): its type, true cause, whether it is benign or unsupported, which
   evidence request would resolve it, and the plausible resolutions with documented prior weights.

Nothing in the extraction pipeline, the evidence engine or the agent may read this module's data at run time.
Only scripts/eval_*.py and tests do.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GENERATOR_VERSION = "synthetic-packets-v2"
SYNTHETIC_FOOTER = "SYNTHETIC - for demonstration only"

PacketLine = Literal["credit", "insurance"]
Split = Literal["dev", "test"]

# Document types. The page classifier predicts one of these per page.
CREDIT_DOC_TYPES: tuple[str, ...] = (
    "loan_application",
    "bank_statement",
    "profit_and_loss",
    "balance_sheet",
    "debt_schedule",
    "photo_id",
    "credit_report",
    "formation_document",
    "tax_return_summary",
    "collateral_valuation",
)
INSURANCE_DOC_TYPES: tuple[str, ...] = (
    "insurance_application",
    "loss_run",
    "property_schedule",
    "cover_email",
    "ownership_statement",
    "inspection_report",
)
DOC_TYPES: tuple[str, ...] = CREDIT_DOC_TYPES + INSURANCE_DOC_TYPES
DocType = Literal[DOC_TYPES]  # type: ignore[valid-type]

# Problem types the generator can inject. Each one is detected by a deterministic check in docintel/checks.py.
PROBLEM_TYPES: tuple[str, ...] = (
    # both lines
    "missing_document",  # a required document is absent from the packet
    "missing_pages",  # pages of a present document are absent (a statement month, a loss-run page)
    "name_mismatch",  # business legal name differs between documents
    "fein_mismatch",  # FEIN differs between documents
    "owner_mismatch",  # an owner's name or ownership % differs between documents
    "prompt_injection",  # instruction-like text inside a document
    # credit
    "revenue_gap",  # stated annual revenue vs revenue verified from bank deposits
    "balance_discontinuity",  # a statement's opening balance or arithmetic does not continue the previous one
    "debt_payment_mismatch",  # debt schedule payment vs payment observed in the statements
    # insurance
    "loss_run_total_mismatch",  # loss-run summary totals vs the sum of the claim rows
    "paid_reserve_mismatch",  # a claim line where paid + reserve differs from incurred by more than $1.00
    "tiv_mismatch",  # stated TIV on the application vs the property schedule total
    "claims_history_mismatch",  # claim count / total incurred stated on the application vs the loss runs
)
ProblemType = Literal[PROBLEM_TYPES]  # type: ignore[valid-type]

# The true cause of a problem. The "benign_" causes have an innocent explanation that the documents themselves
# support; "unsupported_" causes do not; "integrity_" causes are document-integrity indicators (NBC-9.1, HM-11.1).
CAUSES: tuple[str, ...] = (
    "benign_non_revenue_counted",  # stated revenue counted transfers / refunds / loan proceeds as revenue
    "benign_unidentified_revenue",  # deposits without a description (excluded by NBC-3.3) really were revenue
    "benign_timing",  # stated figure is from a different period (fiscal year, receivables collected later)
    "benign_typo",  # a transcription error on one document; every other document agrees
    "benign_outdated_application",  # the application used an older figure that a newer document supersedes
    "benign_rule_resolved",  # the guideline says which value to use (e.g. the higher debt payment, NBC-4.1)
    "unsupported_overstatement",  # stated figure is higher than any document supports
    "unsupported_unexplained",  # values disagree and nothing in the packet explains why
    "integrity_arithmetic",  # totals / balances do not add up (possible tampering)
    "integrity_embedded_instruction",  # instruction-like text addressed to a reviewer or an AI system
    "missing",  # a required document or page is not in the packet
)
Cause = Literal[CAUSES]  # type: ignore[valid-type]

Classification = Literal["benign", "unsupported", "integrity", "missing"]

CLASSIFICATION_FOR_CAUSE: dict[str, str] = {
    "benign_non_revenue_counted": "benign",
    "benign_unidentified_revenue": "benign",
    "benign_timing": "benign",
    "benign_typo": "benign",
    "benign_outdated_application": "benign",
    "benign_rule_resolved": "benign",
    "unsupported_overstatement": "unsupported",
    "unsupported_unexplained": "unsupported",
    "integrity_arithmetic": "integrity",
    "integrity_embedded_instruction": "integrity",
    "missing": "missing",
}


class Strict(BaseModel):
    """Base for every truth model: unknown fields are an error, and objects are immutable once built."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PageTruth(Strict):
    """One page of the packet PDF, exactly as the generator printed it.

    text_lines are the lines in reading order, before any scanning noise. page_number counts from 1 across the
    whole packet PDF; page_in_doc counts from 1 within its document.
    """

    page_number: int = Field(ge=1)
    doc_id: str = Field(pattern=r"^D\d{2}$")
    doc_type: DocType
    page_in_doc: int = Field(ge=1)
    scanned: bool
    text_lines: list[str]


class DocumentTruth(Strict):
    """One document inside the packet. pages lists packet page numbers in order (empty if fully removed)."""

    doc_id: str = Field(pattern=r"^D\d{2}$")
    doc_type: DocType
    title: str
    pages: list[int]
    scanned: bool
    # The checklist item this document satisfies (agent.applications CREDIT_DOCUMENTS / INSURANCE_DOCUMENTS).
    required_document: str | None = None
    # Extra identifiers: the statement month ("2026-03") or the loss-run carrier name.
    month: str | None = None
    carrier: str | None = None


class FieldTruth(Strict):
    """A value printed on a page: one row of the flattened oracle extraction (schemas.flatten_fields).

    field is a dotted path in the extraction schema (docintel/schemas.py), for example "application.fein",
    "statement.closing_balance" or "loss_run.summary.total_incurred". key is the row key defined in schemas.py
    (a statement month, a transaction "2026-03-14|D|4250.00", a claim number, a location id, "O1" for owners);
    it is None for fields that occur once per packet.
    """

    field: str
    key: str | None = None
    value: str | float | int | bool | None
    doc_id: str
    page_number: int = Field(ge=1)


class ResolutionOption(Strict):
    """One plausible way an open problem could be resolved, with its documented prior weight.

    substitutions use the lever vocabulary of config/resolution_templates.yaml: dotted paths into the agent's
    CreditApplication / InsuranceApplication JSON (e.g. {"stated_annual_revenue": 1180000.0}) plus a few special
    levers ("@verified_annual_revenue", "+bank_statements.months", "documents_provided+", "check:<check_id>").
    """

    option_id: str
    description: str
    prior: float = Field(gt=0, le=1)
    substitutions: dict[str, Any]


class InjectedProblem(Strict):
    """A problem the generator planted, with its true cause and what would resolve it."""

    problem_id: str = Field(pattern=r"^PB-\d{2}$")
    problem_type: ProblemType
    cause: Cause
    classification: Classification
    # Where the problem shows: the documents involved and the values each one shows (by doc_id).
    doc_ids: list[str]
    fields: list[str]
    values_by_doc: dict[str, float | int | str | bool | None]
    description: str
    # The evidence request from config/evidence_requests.yaml that resolves it, and its parameters.
    resolving_request: str
    resolving_request_params: dict[str, str] = Field(default_factory=dict)
    # Plausible resolutions (instantiated from config/resolution_templates.yaml; priors sum to 1), the template
    # option closest to the truth, and the exact true values (hidden from the engine; revealed by the simulator).
    resolution_options: list[ResolutionOption]
    latent_option_id: str
    latent_substitutions: dict[str, Any]

    @model_validator(mode="after")
    def check_options(self) -> "InjectedProblem":
        """Priors must sum to 1 and the latent option must be one of the options."""
        if self.classification != CLASSIFICATION_FOR_CAUSE[self.cause]:
            raise ValueError(f"{self.problem_id}: classification does not match cause {self.cause}")
        if self.resolution_options:
            total = sum(option.prior for option in self.resolution_options)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"{self.problem_id}: resolution priors sum to {total}, not 1")
            ids = [option.option_id for option in self.resolution_options]
            if self.latent_option_id not in ids:
                raise ValueError(f"{self.problem_id}: latent option {self.latent_option_id} not in {ids}")
        return self


class InjectionTruth(Strict):
    """An embedded instruction planted in a document (about 10% of packets)."""

    doc_id: str
    page_number: int = Field(ge=1)
    text: str
    technique: str  # e.g. "approval_claim", "ignore_instructions", "skip_screening", "role_spoof"


class PacketTruth(Strict):
    """Everything the generator knows about one SYNTHETIC packet."""

    packet_id: str = Field(pattern=r"^PKT-[CI]-\d{3}$")
    data_label: Literal["SYNTHETIC"] = "SYNTHETIC"
    line: PacketLine
    split: Split
    seed: int
    generator_version: str = GENERATOR_VERSION
    scenario: str
    pdf_file: str  # file name of the packet PDF, relative to the packet folder
    pdf_sha256: str
    documents: list[DocumentTruth]
    pages: list[PageTruth]
    fields: list[FieldTruth]
    problems: list[InjectedProblem]
    injections: list[InjectionTruth]
    # The perfect extraction of this packet (docintel.schemas LoanPacket / InsuranceSubmission as JSON), with the
    # page and line where the renderer printed every value. fields == flatten_fields(oracle_extraction).
    oracle_extraction: dict
    # The agent's application models as JSON (validated with uwcopilot.agent.applications.parse_application).
    application_as_documented: dict
    application_latent: dict
    # Documents the checklist requires that are absent from the packet (agent.applications document codes).
    missing_required_documents: list[str]

    @model_validator(mode="after")
    def check_consistency(self) -> "PacketTruth":
        """Page numbers are 1..n without gaps, and every reference points at a real page or document."""
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"{self.packet_id}: page numbers are not 1..n in order")
        doc_ids = {document.doc_id for document in self.documents}
        for item in [*self.fields, *self.injections]:
            if item.doc_id not in doc_ids:
                raise ValueError(f"{self.packet_id}: unknown doc_id {item.doc_id}")
            if item.page_number > len(numbers):
                raise ValueError(f"{self.packet_id}: page {item.page_number} is outside the packet")
        return self


def load_truth(path: Path) -> PacketTruth:
    """Read and validate one truth.json file."""
    return PacketTruth.model_validate_json(path.read_text(encoding="utf-8"))


def truth_sha256(truth: PacketTruth) -> str:
    """SHA-256 of the canonical truth JSON; the hash of all test-split truths is the eval-set version."""
    text = json.dumps(truth.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
