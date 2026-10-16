"""Request and response models of the Lender Desk demo API (/api/lender/*). The router docstring shows the JSON.

v0.1 shows only SYNTHETIC structured applications (eval/applications and eval/startup_applications) and labels
every response "SYNTHETIC DEMO". The agent's own models are reused where they exist (the policy's findings,
calculations and passed checks; screening list snapshots), so the API shows exactly what the agent produced.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.policy import Calculation, CheckPassed, Finding, NotEvaluated
from uwcopilot.agent.vocab import ReviewerAction
from uwcopilot.tools.schemas import ListSnapshot

DEMO_LABEL = "SYNTHETIC DEMO"
NEVER_DECIDES = "The copilot never decides — a person does."
LineName = Literal["credit", "insurance", "startup_credit"]


class QueueItem(BaseModel):
    """One demo file in the queue."""

    id: str
    line: LineName
    line_label: str
    applicant: str = Field(description="SYNTHETIC business name")
    requested_amount: float = Field(description="The amount approval authority is measured against (see amount_label)")
    amount_label: str
    label: str = DEMO_LABEL


class LineCount(BaseModel):
    """How many demo files a line has."""

    id: LineName
    label: str
    count: int


class QueueResponse(BaseModel):
    """GET /api/lender/queue."""

    label: str = DEMO_LABEL
    notice: str = NEVER_DECIDES
    files: list[QueueItem]
    total: int
    lines: list[LineCount]


class ScreeningCandidateOut(BaseModel):
    """A watchlist entry that scored at or above the screening threshold (a potential match, never a finding)."""

    list_entry_id: str
    list_name: str
    source: str
    matched_name: str
    score: float


class ScreeningOut(BaseModel):
    """The screening result for one party (APPLICANT, O1, ...)."""

    party_id: str | None
    result_id: str
    status: str = Field(description="clear | potential_match (a person reviews it) | error | unavailable")
    top_score: float | None
    candidates: list[ScreeningCandidateOut]
    error: str | None


class ScreeningPanel(BaseModel):
    """Which lists were screened (with their download dates) and the result for each party."""

    configured_lists: list[str] = Field(description="UWC_SCREENING_LISTS as this server reads it")
    lists_used: list[ListSnapshot] = Field(description="The list files the results name, with snapshot dates")
    results: list[ScreeningOut]
    note: str


class ToolPanel(BaseModel):
    """The status of one tool across the run (guideline search, risk model)."""

    status: Literal["ok", "unavailable", "error", "not_called"]
    detail: str


class ToolStatusOut(BaseModel):
    """One executed tool call."""

    result_id: str
    tool: str
    purpose: str
    party_id: str | None
    status: str
    error: str | None


class InjectionHitOut(BaseModel):
    """A free-text field the injection screen flagged. excerpt is untrusted text: show it as text only."""

    field: str
    patterns: list[str]
    excerpt: str


class MemoReasonOut(BaseModel):
    """One reason of the memo with its citations (section ids, CALC-*, TR-*, APP-*, CHK-*)."""

    text: str
    citations: list[str]


class MemoOut(BaseModel):
    """The memo (v0.1: the deterministic template memo, no language model)."""

    recommendation: str
    summary: str
    reasons: list[MemoReasonOut]


class MemoCheckIssue(BaseModel):
    """One problem the claim checker found."""

    kind: str
    detail: str


class MemoCheckOut(BaseModel):
    """The deterministic claim checker's verdict on the memo."""

    passed: bool
    issues: list[MemoCheckIssue]


class EvidenceOut(BaseModel):
    """A citable item that is not a guideline section (those come from GET /api/lender/sections/{id})."""

    evidence_id: str
    kind: str
    title: str
    text: str
    flags: list[str]


class ActionCheck(BaseModel):
    """Whether the checkpoint would accept an action from you, and why not (from agent.hitl.validate_decision)."""

    action: ReviewerAction
    allowed: bool
    reasons: list[str]
    needs_override_reason: bool


class DecisionSummary(BaseModel):
    """The essentials of a recorded DEMO decision."""

    record_id: str
    record_sha256: str
    action: str
    final_status: str
    reviewer_role: str
    is_override: bool
    decided_at: str | None
    label: str = "DEMO"


class LenderFileResponse(BaseModel):
    """GET /api/lender/files/{id}: the agent's run up to the human checkpoint, shaped for the review screen."""

    id: str
    line: LineName
    line_label: str
    applicant: str
    requested_amount: float
    amount_label: str
    label: str = DEMO_LABEL
    notice: str = NEVER_DECIDES
    reference_recommendation: str
    recommendation_text: str
    required_reviewer_role: str
    your_reviewer_role: str
    review_queue: str
    blocking_open: bool
    referral_targets: list[str]
    authority_exposure: float
    approval_limits: dict[str, float]
    allowed_actions: list[str]
    action_checks: list[ActionCheck]
    override_reasons: list[str]
    findings: list[Finding]
    checks_passed: list[CheckPassed]
    calculations: list[Calculation]
    not_evaluated: list[NotEvaluated]
    screening: ScreeningPanel
    guideline_search: ToolPanel
    risk_model: ToolPanel
    tool_results: list[ToolStatusOut]
    tool_rejections: list[dict[str, Any]]
    injection_hits: list[InjectionHitOut]
    memo: MemoOut | None
    memo_status: str | None
    memo_writer: Literal["template"] = "template"
    memo_note: str
    memo_check: MemoCheckOut | None
    stamp: str | None
    evidence: dict[str, EvidenceOut]
    cited_sections: list[str]
    policy_version: str
    graph_version: str
    decision: DecisionSummary | None


class SectionResponse(BaseModel):
    """GET /api/lender/sections/{section_id}: one section of a SYNTHETIC guideline or lending program."""

    section_id: str
    title: str
    text: str
    document: str
    data_label: Literal["SYNTHETIC"] = "SYNTHETIC"
    flags: list[str] = Field(description="Instruction-like phrases found in the text (it is data, never orders)")


class DecisionRequest(BaseModel):
    """POST /api/lender/files/{id}/decision. Who decides and in which role comes from the session, not the body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    action: ReviewerAction
    rationale: str = Field(min_length=10, max_length=2000)
    override_reason: str | None = Field(default=None, max_length=64)


class DecisionResponse(BaseModel):
    """The recorded DEMO decision."""

    file_id: str
    status: Literal["recorded"] = "recorded"
    label: str = "DEMO"
    decision: DecisionSummary
    markdown: str


class RecordResponse(BaseModel):
    """GET /api/lender/files/{id}/record: the user's DEMO decision record."""

    file_id: str
    label: str = "DEMO"
    decision: DecisionSummary
    markdown: str
    record: dict[str, Any]


class RecordDeleted(BaseModel):
    """DELETE /api/lender/files/{id}/record."""

    file_id: str
    deleted: bool
    pending: bool
    message: str
