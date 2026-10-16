"""handoff: a founder package -> the lender copilot, stopped at the human checkpoint, with the founder ledger.

    result = handoff(package)                  # tool handlers, LLM and checkpointer can be passed in
    result.reached_checkpoint                  # True: the agent stopped for a human
    result.review_request.evidence_requests    # next-best evidence for the founder ledger's material claims

Steps:
1. to_application(package): the StartupCreditApplication (application.py);
2. the EXISTING LangGraph agent (agent.graph through agent.runner.start_run) runs on it: injection screen, tool plan
   (sanctions screening of the applicant and owners only, for a startup file), the startup program policy, the
   deterministic TEMPLATE memo (the fake LLM's template writer: "FAKE LLM - test mode", no model is called) and the
   claim checker; the graph always interrupts at human_review. handoff never resumes it, so nothing is approved or
   declined without a human; the run can be resumed later from the checkpointer by a person;
3. the evidence engine on the same file (screening outcomes and injection flags from the agent's own run): the
   startup open items (evidence/open_items_startup.py, plus missing checklist documents) and their requests ranked
   by value of information (evidence/startup_requests.py);
4. the lender review request: the agent's review payload, the founder ledger with every claim's status (material
   claims marked), the open items and the ranked requests, each tied to the founder-ledger claims it would firm up
   (the UNKNOWN, ASSUMPTION and USER_STATED material claims).
"""

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps, injection_hits_of, policy_inputs_from, tool_results_of, utc_now
from uwcopilot.agent.runner import compile_agent, start_run
from uwcopilot.agent.settings import load_agent_settings
from uwcopilot.agent.tools import screening_handler
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.open_items import detect_open_items
from uwcopilot.evidence.startup_requests import rank_startup_requests, startup_catalog_version
from uwcopilot.evidence.state import evaluate, from_application
from uwcopilot.evidence.voi_models import RankedRequest
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package.application import to_application
from uwcopilot.studio.package.constants import FULL_DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.package.models import Package
from uwcopilot.studio.package.text import source_text, value_text
from uwcopilot.tools.registry import ToolHandler

HANDOFF_VERSION = "studio-handoff-1.0"
UNSUPPORTED_NUMBER = "unsupported_number"


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LedgerClaimView(Frozen):
    """One founder-ledger claim as the lender sees it: value as printed, status, source and whether it is material."""

    claim_id: str
    label: str
    value: str
    status: str
    source: str
    material: bool


class RequestView(Frozen):
    """A ranked evidence request with the founder-ledger claims it would firm up (and their statuses now)."""

    rank: int
    request_id: str
    title: str
    params: dict[str, str]
    item_ids: list[str]
    voi: float
    p_change: float
    cost_weight: float
    reason: str
    founder_claims: dict[str, str] = Field(description="{founder ledger claim id: its status now}")


class LenderReviewRequest(Frozen):
    """What the lender reviews at the checkpoint: the agent's payload plus the founder ledger and next-best evidence."""

    package_id: str
    application_id: str
    data_label: str
    disclaimer: str = FULL_DISCLAIMER
    simulation_label: str = SIMULATION_LABEL
    agent: dict[str, Any]
    founder_ledger: list[LedgerClaimView]
    status_counts: dict[str, int]
    open_items: list[dict[str, Any]]
    evidence_requests: list[RequestView]
    evidence_catalog_version: str


class HandoffResult(Frozen):
    """Where the handoff stands: the agent stopped at the human checkpoint (never decided) and what it found."""

    handoff_version: str = HANDOFF_VERSION
    package_id: str
    application_id: str
    thread_id: str
    status: Literal["awaiting_review", "completed"]
    reached_checkpoint: bool
    decision_recorded_without_human: bool
    reference_recommendation: str
    evidence_engine_recommendation: str
    memo_status: str | None
    memo_check_passed: bool | None
    unsupported_numbers: int
    review_request: LenderReviewRequest


def material_claim_ids(package: Package) -> set[str]:
    """Claims a program check reads, plus the inputs of the top drivers (NSP-6.1): the claims a decision rests on."""
    material = {claim_id for assertion in package.ledger.assertions for claim_id in assertion.evidence_ids}
    material |= {item.claim_id for item in package.material_assumptions if item.tornado_rank in (1, 2, 3)}
    return material


def ledger_view(package: Package, material: set[str]) -> list[LedgerClaimView]:
    """Every founder-ledger claim with its printed value, status and source."""
    return [
        LedgerClaimView(
            claim_id=item.id,
            label=item.label,
            value=value_text(item),
            status=item.status,
            source=source_text(item, package.runs),
            material=item.id in material,
        )
        for item in package.ledger.claims
    ]


def driver_claim(package: Package, rank: int) -> str | None:
    """The founder-ledger claim behind the top driver of this rank."""
    return next((item.claim_id for item in package.material_assumptions if item.tornado_rank == rank), None)


def founder_claims_of(package: Package, item: OpenItem) -> list[str]:
    """The founder-ledger claims an open item is about."""
    claims = {claim.id for claim in package.ledger.claims}
    if item.problem_type == "missing_equity_verification":
        wanted = [ids.FOUNDER_EQUITY, ids.CALC_EQUITY_PCT]
    elif item.problem_type == "missing_experience_evidence":
        wanted = [ids.FOUNDER_EXPERIENCE]
    elif item.problem_type == "unsupported_material_assumption":
        rank = int(item.item_id.rsplit("-r", 1)[-1])
        wanted = [ids.tornado_id(rank, "input"), driver_claim(package, rank) or ""]
    elif item.problem_type == "missing_quotes":
        wanted = [ids.FOUNDER_CAPEX, ids.ASSUME_CAPEX]
    else:
        wanted = []
    return [claim_id for claim_id in wanted if claim_id in claims]


def request_views(package: Package, ranked: list[RankedRequest], items: list[OpenItem]) -> list[RequestView]:
    """Ranked requests with the founder claims (and statuses) each one would firm up."""
    by_id = {item.item_id: item for item in items}
    statuses = {claim.id: claim.status for claim in package.ledger.claims}
    views = []
    for request in ranked:
        claim_ids: list[str] = []
        for item_id in request.item_ids:
            claim_ids += [claim for claim in founder_claims_of(package, by_id[item_id]) if claim not in claim_ids]
        views.append(
            RequestView(
                rank=request.rank,
                request_id=request.request_id,
                title=request.title,
                params=dict(request.params),
                item_ids=list(request.item_ids),
                voi=request.voi,
                p_change=request.p_change,
                cost_weight=request.cost_weight,
                reason=request.reason,
                founder_claims={claim_id: statuses[claim_id] for claim_id in claim_ids},
            )
        )
    return views


def default_handlers() -> dict[str, ToolHandler]:
    """The agent's real screening handler (the downloaded OFAC / OpenSanctions lists, loaded on first call). A
    startup file's tool plan only screens (no guideline search, no risk score)."""
    return {"screen_entity": screening_handler()}


def thread_for(package: Package) -> str:
    """A stable thread id per package."""
    return "handoff-" + hashlib.sha256(package.package_id.encode()).hexdigest()[:16]


def unsupported_count(memo_check: Mapping[str, Any] | None) -> int:
    """How many claim-checker issues say a memo number is not in the cited evidence."""
    if not memo_check:
        return 0
    return sum(1 for issue in memo_check.get("issues", []) if issue.get("kind") == UNSUPPORTED_NUMBER)


def handoff(
    package: Package,
    *,
    tool_handlers: Mapping[str, ToolHandler] | None = None,
    llm: LLMClient | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    decision_log: DecisionLog | None = None,
    thread_id: str | None = None,
    clock: Callable[[], datetime] = utc_now,
    log_dir: Path | None = None,
) -> HandoffResult:
    """Run the lender copilot on the package up to the human checkpoint (module docstring)."""
    application = to_application(package)
    log = decision_log or DecisionLog(
        log_dir / "decision_records.jsonl" if log_dir else load_agent_settings().decision_log
    )
    deps = AgentDeps(
        llm=llm or FakeLLM(template_memo_responder),
        tool_handlers=dict(tool_handlers) if tool_handlers is not None else default_handlers(),
        decision_log=log,
        clock=clock,
    )
    agent = compile_agent(deps, checkpointer or InMemorySaver())
    thread = thread_id or thread_for(package)
    outcome = start_run(agent, application.model_dump(mode="json"), thread)
    state = outcome.state
    inputs = policy_inputs_from(tool_results_of(state), injection_hits_of(state))
    case = from_application(
        application.model_dump(mode="json"), injection_flags=inputs.injection_flags, screening=inputs.screening
    )
    current = evaluate(case)
    items = detect_open_items(case, policy_result=current)
    ranked = rank_startup_requests(case, items, current)
    agent_request = dict(outcome.review_request or {})
    material = material_claim_ids(package)
    review = LenderReviewRequest(
        package_id=package.package_id,
        application_id=application.application_id,
        data_label=package.data_label,
        agent=agent_request,
        founder_ledger=ledger_view(package, material),
        status_counts=package.ledger.status_counts(),
        open_items=[item.model_dump() for item in items],
        evidence_requests=request_views(package, ranked, items),
        evidence_catalog_version=startup_catalog_version(),
    )
    policy = state.get("policy_result") or {}
    memo_check = state.get("memo_check")
    return HandoffResult(
        package_id=package.package_id,
        application_id=application.application_id,
        thread_id=thread,
        status=outcome.status,
        reached_checkpoint=outcome.status == "awaiting_review" and outcome.review_request is not None,
        decision_recorded_without_human=state.get("decision_record") is not None,
        reference_recommendation=str(policy.get("recommendation", "")),
        evidence_engine_recommendation=current.recommendation,
        memo_status=state.get("memo_status"),
        memo_check_passed=memo_check.get("passed") if memo_check else None,
        unsupported_numbers=unsupported_count(memo_check),
        review_request=review,
    )
