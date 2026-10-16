"""The Evidence-to-Action Engine for one case (Phase 4): ledger, calculations, scenarios, stability, next-best
evidence and the decision record, all deterministic.

run_engine(source, ...) accepts:
- a docintel PacketResult (the Phase 2 pipeline output for one packet PDF): extraction, checks, contradictions and
  page-text injection hits are all used;
- a PacketExtraction alone: the checks and the contradiction builder are run on it (no page-text injection hits);
- a CaseState (for example evidence.state.from_application for an application typed by an applicant, or a
  committed SYNTHETIC application), with an optional PacketContext.

Steps: evaluate the reference policy (agent/policy.py, the clean screening assumption unless outcomes are given) ->
build the evidence ledger -> run the stress scenarios and counterfactuals -> decision stability -> detect the open
items -> rank the evidence requests by value of information -> the supplementary calculations (display-only views,
evidence/supplementary.py) -> build the decision record (appended to a DecisionLog only when one is passed). The
engine prepares and explains; it never approves or declines. The recommendation is the reference policy's, and a
person decides.

A case whose application does not validate (the extraction could not read required values) is NOT guessed: the
result says it is not evaluable, lists the reason, and the record says so too.
"""

import hashlib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from uwcopilot.agent.applications import canonical_json, inputs_sha256
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.contradictions import build_contradictions
from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket, PacketExtraction
from uwcopilot.evidence.ledger import Ledger, ModelEstimator, build_ledger
from uwcopilot.evidence.open_item_models import OpenItem, PacketContext, packet_context
from uwcopilot.evidence.open_items import detect_open_items
from uwcopilot.evidence.record import ENGINE_VERSION, append_record, build_decision_record, utc_now
from uwcopilot.evidence.requests import catalog_version, templates_version
from uwcopilot.evidence.scenarios import DecisionStability, ScenarioResult, decision_stability, run_scenarios
from uwcopilot.evidence.state import (
    MODEL_NOT_EVALUATED_REASON,
    CaseState,
    IncompleteApplicationError,
    application_of,
    evaluate,
    from_packet,
    from_packet_result,
)
from uwcopilot.evidence.supplementary import supplementary_calculations
from uwcopilot.evidence.voi import RankedRequest, rank_requests

if TYPE_CHECKING:  # the pipeline imports the PDF and OCR stack; only its result type is needed here
    from uwcopilot.docintel.pipeline import PacketResult

NO_PROMPTS = "none: no LLM runs in the Evidence-to-Action Engine"


@dataclass
class EngineResult:
    """Everything the engine produced for one case (see the module docstring)."""

    case_id: str
    line: str | None
    state: CaseState
    evaluable: bool
    not_evaluable_reason: str | None = None
    policy_result: PolicyResult | None = None
    ledger: Ledger | None = None
    scenarios: list[ScenarioResult] = field(default_factory=list)
    stability: DecisionStability | None = None
    open_items: list[OpenItem] = field(default_factory=list)
    next_best_evidence: list[RankedRequest] = field(default_factory=list)
    supplementary: dict[str, Any] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def recommendation(self) -> str | None:
        """The reference policy's recommendation (None when the case is not evaluable)."""
        return None if self.policy_result is None else self.policy_result.recommendation

    @property
    def calculations(self) -> list[Any]:
        """The policy's calculations (CALC-*)."""
        return [] if self.policy_result is None else list(self.policy_result.calculations)


def versions_block(policy_result: PolicyResult | None) -> dict[str, Any]:
    """Versions written into every record."""
    return {
        "engine": ENGINE_VERSION,
        "policy": None if policy_result is None else policy_result.policy_version,
        "evidence_request_catalog": catalog_version(),
        "resolution_templates": templates_version(),
        "risk_model": "not evaluated",
        "risk_model_reason": MODEL_NOT_EVALUATED_REASON,
        "prompts": NO_PROMPTS,
    }


def sha256_text(text: str) -> str:
    """SHA-256 of a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def packet_hashes(packet_sha256: str | None, context: PacketContext) -> dict[str, str | None]:
    """The packet PDF hash when known, and the hash of the canonical extraction JSON."""
    extraction = None
    if context.extraction is not None:
        extraction = sha256_text(canonical_json(context.extraction.model_dump(mode="json")))
    return {"pdf": packet_sha256, "extraction": extraction}


def resolve_source(
    source: "PacketResult | PacketExtraction | CaseState", context: PacketContext | None
) -> tuple[CaseState, PacketContext, dict[str, Any] | None]:
    """(case state, packet context, contradiction graph JSON) for any accepted source."""
    if isinstance(source, CaseState):
        return source, context or PacketContext(), None
    if isinstance(source, LoanPacket | InsuranceSubmission):
        checks = run_checks(source)
        report = build_contradictions(source, checks)
        state = from_packet(source, checks, report)
        return state, packet_context(source, checks, report), report.graph_json
    state = from_packet_result(source)
    found = packet_context(source.extraction, source.checks, source.contradictions)
    return state, found, source.contradictions.graph_json


def inputs_hash(state: CaseState) -> str:
    """agent.applications.inputs_sha256 of the validated application (the hash the agent's records use); for an
    application that does not validate, SHA-256 of its canonical JSON as read."""
    try:
        return inputs_sha256(application_of(state))
    except IncompleteApplicationError:
        inputs = {key: value for key, value in state.application.items() if key != "ground_truth"}
        return sha256_text(canonical_json(inputs))


def record_for(
    result: EngineResult,
    context: PacketContext,
    graph: dict[str, Any] | None,
    packet_sha256: str | None,
    clock: Callable[[], datetime],
    supersedes_record_id: str | None,
    correction_reason: str | None,
) -> dict[str, Any]:
    """The decision record of an engine result."""
    return build_decision_record(
        case_id=result.case_id,
        line=result.line,
        data_label=result.state.data_label,
        packet_sha256=packet_hashes(packet_sha256, context),
        inputs_sha256=inputs_hash(result.state),
        evidence_ledger=None if result.ledger is None else result.ledger.model_dump(mode="json"),
        contradictions=[item.model_dump(mode="json") for item in context.contradictions],
        contradiction_graph=graph,
        policy_result=None if result.policy_result is None else result.policy_result.model_dump(mode="json"),
        scenarios=[item.model_dump(mode="json") for item in result.scenarios],
        decision_stability=None if result.stability is None else result.stability.model_dump(mode="json"),
        open_items=[item.model_dump(mode="json") for item in result.open_items],
        next_best_evidence=[item.model_dump(mode="json") for item in result.next_best_evidence],
        supplementary_calculations=result.supplementary,
        versions=versions_block(result.policy_result),
        clock=clock,
        not_evaluable_reason=result.not_evaluable_reason,
        supersedes_record_id=supersedes_record_id,
        correction_reason=correction_reason,
    )


def run_engine(
    source: "PacketResult | PacketExtraction | CaseState",
    *,
    context: PacketContext | None = None,
    packet_sha256: str | None = None,
    estimator: ModelEstimator | None = None,
    answered: Collection[str] = (),
    clock: Callable[[], datetime] = utc_now,
    record_log: DecisionLog | None = None,
    supersedes_record_id: str | None = None,
    correction_reason: str | None = None,
) -> EngineResult:
    """Run the whole engine on one case (see the module docstring).

    A correction (supersedes_record_id) needs a correction_reason and a record_log that holds the superseded record
    (ValueError otherwise, raised before any work is done).
    """
    if supersedes_record_id is not None:
        if not (correction_reason or "").strip():
            raise ValueError("a record that supersedes another needs a correction reason")
        if record_log is None or record_log.find(supersedes_record_id) is None:
            raise ValueError(f"record {supersedes_record_id} is not in the record log, so it cannot be superseded")
    state, found, graph = resolve_source(source, context)
    line = state.application.get("line")
    case_id = state.case_id or str(state.application.get("application_id", ""))
    try:
        application = application_of(state)
    except IncompleteApplicationError as error:
        result = EngineResult(case_id=case_id, line=line, state=state, evaluable=False, not_evaluable_reason=str(error))
    else:
        policy = evaluate(state)
        ledger = build_ledger(
            state, policy, found.extraction, contradictions=list(found.contradictions), estimator=estimator
        )
        scenarios = run_scenarios(state, policy)
        items = detect_open_items(state, found, policy, answered)
        result = EngineResult(
            case_id=case_id,
            line=line,
            state=state,
            evaluable=True,
            policy_result=policy,
            ledger=ledger,
            scenarios=scenarios,
            stability=decision_stability(scenarios, policy.recommendation),
            open_items=items,
            next_best_evidence=rank_requests(state, items, policy),
            supplementary=supplementary_calculations(application, policy, ledger),
        )
    record = record_for(result, found, graph, packet_sha256, clock, supersedes_record_id, correction_reason)
    result.record = append_record(record, record_log) if record_log is not None else record
    return result
