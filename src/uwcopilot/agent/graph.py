"""The underwriting agent as a LangGraph state graph with a human-approval checkpoint.

    intake -> injection_screen -> plan_tools -> dispatch_tools -> run_tool (one task per call, in parallel)
           -> after_tools -> evaluate_policy -> draft_memo -> check_memo -> human_review (interrupt)
           -> record_decision

- intake validates the application and drops the ground-truth label (the agent never sees it). The graph's input
  schema only has "application", so a caller cannot smuggle a decision or any other state in with it.
- injection_screen flags instruction-like free text (NBC-9.2 / HM-11.2). Flagged text never chooses anything; it
  reaches the memo prompt only escaped and quoted as data.
- plan_tools builds the tool plan from structured fields only (tool_plan.py).
- dispatch_tools is the tool gate: allow-list, schema validation, loop guard, call budget (tool_runner.py).
  Approved calls fan out with Send and run in parallel; each result is checkpointed as it completes.
- evaluate_policy runs the deterministic reference policy (policy.py).
- draft_memo asks the LLM for a structured memo. The LLM may ask for more guideline searches (read-only, at most
  max_llm_tool_rounds rounds, through the same gate); it cannot call any other tool.
- check_memo runs the deterministic claim checker. A failing memo is regenerated once with the checker's findings;
  if it fails again it goes to the human with its problems listed. It is never silently dropped or fixed.
- human_review always interrupts. Only a human's resume value can continue the run, and validate_decision()
  refuses decisions the guidelines do not allow (then the checkpoint asks again). Nothing auto-approves and
  nothing auto-declines.
- record_decision appends one chained, idempotent decision record.

State is plain JSON data so the SQLite checkpointer can persist it; a run can be killed at the interrupt and resumed
later from the same file by a new process (runner.py).
"""

import hashlib
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from uwcopilot.agent.applications import (
    AnyApplication,
    application_inputs,
    canonical_json,
    inputs_sha256,
    parse_application,
)
from uwcopilot.agent.claim_checker import ClaimIssue, MemoCheck, check_memo
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.evidence import EvidenceItem, build_evidence
from uwcopilot.agent.hitl import build_review_request, final_status, is_override, validate_decision
from uwcopilot.agent.injection import InjectionHit, screen_application
from uwcopilot.agent.memo import (
    MEMO_MAX_TOKENS,
    PROMPT_VERSION,
    MemoDraft,
    MemoParseError,
    build_memo_prompt,
    parse_memo,
)
from uwcopilot.agent.policy import (
    InjectionFlag,
    PolicyInputs,
    PolicyResult,
    RiskSignal,
    ScreeningOutcome,
    evaluate_policy,
)
from uwcopilot.agent.tool_plan import ToolRequest, plan_tool_calls
from uwcopilot.agent.tool_runner import MAX_TOOL_CALLS, ApprovedCall, ToolResult, execute_call, vet_requests
from uwcopilot.agent.vocab import FAKE_LLM_STAMP, SYNTHETIC_LABEL
from uwcopilot.llm.base import IncompleteLLMResponse, LLMClient
from uwcopilot.observability.redaction import PiiRedactor, redactor_for_application
from uwcopilot.observability.tracing import AgentTracer, record_error, record_llm_response, set_output
from uwcopilot.tools.registry import ToolHandler

GRAPH_VERSION = "underwriting-graph-v1"
MAX_MEMO_ATTEMPTS = 2
MAX_LLM_TOOL_ROUNDS = 2
NODES: tuple[str, ...] = (
    "intake",
    "injection_screen",
    "plan_tools",
    "dispatch_tools",
    "run_tool",
    "after_tools",
    "evaluate_policy",
    "draft_memo",
    "check_memo",
    "human_review",
    "record_decision",
)


class AgentInput(TypedDict):
    """The only thing a caller can put into a new run."""

    application: dict[str, Any]


class AgentState(TypedDict, total=False):
    """Everything the graph keeps between nodes. Lists with operator.add are appended to, never replaced."""

    application: dict[str, Any]
    application_id: str
    inputs_sha256: str
    injection_hits: list[dict[str, Any]]
    pending_requests: list[dict[str, Any]]
    approved_calls: list[dict[str, Any]]
    tool_results: Annotated[list[dict[str, Any]], operator.add]
    tool_rejections: Annotated[list[dict[str, Any]], operator.add]
    llm_tool_rounds: int
    policy_result: dict[str, Any] | None
    memo: dict[str, Any] | None
    memo_raw: str | None
    memo_attempts: int
    memo_check: dict[str, Any] | None
    memo_status: str
    memo_parse_error: str | None
    memo_history: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: Annotated[list[dict[str, Any]], operator.add]
    human_decision: dict[str, Any] | None
    rejected_decisions: Annotated[list[dict[str, Any]], operator.add]
    decision_record: dict[str, Any] | None
    status: str


def utc_now() -> datetime:
    """The current time in UTC (tests pass a fixed clock instead)."""
    return datetime.now(UTC)


@dataclass
class AgentDeps:
    """What the nodes need from outside: the LLM, tool handlers, the decision log, tracing and a clock."""

    llm: LLMClient
    tool_handlers: Mapping[str, ToolHandler]
    decision_log: DecisionLog
    tracer: AgentTracer = field(default_factory=AgentTracer)
    clock: Callable[[], datetime] = utc_now
    max_memo_attempts: int = MAX_MEMO_ATTEMPTS
    max_llm_tool_rounds: int = MAX_LLM_TOOL_ROUNDS
    max_tool_calls: int = MAX_TOOL_CALLS
    memo_max_tokens: int = MEMO_MAX_TOKENS


# --------------------------------------------------------------------------------------------------------------
# Helpers that turn checkpointed JSON back into models
# --------------------------------------------------------------------------------------------------------------


def application_of(state: Mapping[str, Any]) -> AnyApplication:
    """The validated application from state."""
    return parse_application(state["application"])


def tool_results_of(state: Mapping[str, Any]) -> list[ToolResult]:
    """Executed tool results in result-id order (TR-1, TR-2, ...)."""
    results = [ToolResult.model_validate(item) for item in state.get("tool_results") or []]
    return sorted(results, key=lambda result: int(result.result_id.split("-")[1]))


def injection_hits_of(state: Mapping[str, Any]) -> list[InjectionHit]:
    """The injection screen's hits from state."""
    return [InjectionHit.model_validate(item) for item in state.get("injection_hits") or []]


def policy_of(state: Mapping[str, Any]) -> PolicyResult:
    """The reference policy result from state."""
    return PolicyResult.model_validate(state["policy_result"])


def evidence_of(state: Mapping[str, Any]) -> dict[str, EvidenceItem]:
    """The evidence registry for the current state (rebuilt deterministically when needed)."""
    return build_evidence(application_of(state), policy_of(state), tool_results_of(state), injection_hits_of(state))


def policy_inputs_from(results: list[ToolResult], hits: list[InjectionHit]) -> PolicyInputs:
    """Turn tool results and injection hits into the policy's inputs. Failed screenings count as not completed."""
    screening: dict[str, ScreeningOutcome] = {}
    risk: RiskSignal | None = None
    risk_reason: str | None = "the score_risk tool was not called"
    for result in results:
        if result.tool == "screen_entity" and result.party_id:
            if result.status == "ok" and result.output is not None:
                candidates = result.output.get("candidates", [])
                screening[result.party_id] = ScreeningOutcome(
                    party_id=result.party_id,
                    status=result.output["status"],
                    result_id=result.result_id,
                    top_score=max((candidate["score"] for candidate in candidates), default=None),
                    candidate_count=len(candidates),
                )
            else:
                screening[result.party_id] = ScreeningOutcome(
                    party_id=result.party_id, status="not_completed", result_id=result.result_id
                )
        if result.tool == "score_risk":
            if result.status == "ok" and result.output is not None:
                risk = RiskSignal(
                    result_id=result.result_id,
                    product=result.output["product"],
                    prediction=result.output["prediction"],
                    risk_band=result.output["risk_band"],
                    model_version=result.output["model_version"],
                    unknown_features=result.output.get("unknown_features", []),
                )
                risk_reason = None
            else:
                risk_reason = f"the score_risk tool was {result.status} ({result.error})"
    return PolicyInputs(
        screening=list(screening.values()),
        risk=risk,
        risk_unavailable_reason=risk_reason,
        injection_flags=[InjectionFlag(field=hit.field, patterns=hit.patterns) for hit in hits],
    )


def thread_id_of(config: RunnableConfig | None) -> str:
    """The LangGraph thread id from the run config ("" when absent)."""
    return str(((config or {}).get("configurable") or {}).get("thread_id", ""))


# --------------------------------------------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------------------------------------------


NodeBody = Callable[[dict[str, Any], str, PiiRedactor], dict[str, Any]]


def build_graph(deps: AgentDeps) -> StateGraph:
    """The uncompiled graph. Compile it with a checkpointer (see runner.py)."""

    def traced(name: str, body: NodeBody) -> Callable[[dict[str, Any], RunnableConfig], dict[str, Any]]:
        """Wrap a node body in a CHAIN span with redacted input and output. Interrupts are not errors."""

        def node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
            """Run the node body inside its span."""
            thread_id = thread_id_of(config)
            redactor = redactor_for_application(state.get("application") or {})
            with deps.tracer.node_span(name, thread_id, redactor, state) as span:
                try:
                    update = body(state, thread_id, redactor)
                except GraphInterrupt:
                    span.set_attribute("uwc.interrupted", True)
                    raise
                except Exception as error:
                    record_error(span, error)
                    raise
                set_output(span, redactor, update)
                return update

        return node

    def intake(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Validate the application and keep only its inputs (never the ground-truth label)."""
        application = parse_application(state["application"])
        return {
            "application": application_inputs(application),
            "application_id": application.application_id,
            "inputs_sha256": inputs_sha256(application),
            "policy_result": None,
            "memo": None,
            "memo_raw": None,
            "memo_check": None,
            "memo_attempts": 0,
            "llm_tool_rounds": 0,
            "human_decision": None,
            "decision_record": None,
            "status": "in_progress",
        }

    def injection_screen(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Flag instruction-like text in every free-text field."""
        hits = screen_application(application_of(state))
        return {"injection_hits": [hit.model_dump() for hit in hits]}

    def plan_tools(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """The deterministic tool plan (structured fields only)."""
        requests = plan_tool_calls(application_of(state))
        return {"pending_requests": [request.model_dump(mode="json") for request in requests]}

    def dispatch_tools(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """The tool gate: approve or reject every pending request."""
        requests = [ToolRequest.model_validate(item) for item in state.get("pending_requests") or []]
        executed = state.get("tool_results") or []
        approved, rejected = vet_requests(
            requests, {item["signature"] for item in executed}, len(executed), deps.max_tool_calls
        )
        return {
            "pending_requests": [],
            "approved_calls": [call.model_dump(mode="json") for call in approved],
            "tool_rejections": [rejection.model_dump() for rejection in rejected],
        }

    def run_tool(payload: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Execute one approved call inside a TOOL span."""
        call = ApprovedCall.model_validate(payload["call"])
        with deps.tracer.tool_span(call.request.tool, call.result_id, thread_id, redactor, call.arguments) as span:
            result = execute_call(call, deps.tool_handlers)
            span.set_attribute("uwc.tool.status", result.status)
            span.set_attribute("uwc.tool.origin", result.origin)
            span.set_attribute("uwc.latency_ms", result.latency_ms)
            set_output(span, redactor, result.output if result.output is not None else {"error": result.error})
        return {"tool_results": [result.model_dump(mode="json")]}

    def after_tools(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Join point after a batch of tool calls."""
        return {"approved_calls": []}

    def evaluate(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Run the reference policy on the application and the tool results."""
        inputs = policy_inputs_from(tool_results_of(state), injection_hits_of(state))
        result = evaluate_policy(application_of(state), inputs)
        return {"policy_result": result.model_dump(mode="json")}

    def draft_memo(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Ask the LLM for a memo (or for more guideline searches)."""
        application = application_of(state)
        policy = policy_of(state)
        evidence = evidence_of(state)
        attempts = state.get("memo_attempts") or 0
        previous_check = state.get("memo_check")
        retrying = previous_check is not None and not previous_check["passed"] and state.get("memo_raw") is not None
        feedback = MemoCheck.model_validate(previous_check).feedback() if retrying else None
        system, messages = build_memo_prompt(
            application,
            policy,
            evidence,
            injection_hits_of(state),
            previous_output=state.get("memo_raw") if retrying else None,
            feedback=feedback,
        )
        prompt_record = {"system": system, "messages": [message.model_dump() for message in messages]}
        with deps.tracer.llm_span(
            deps.llm.provider, deps.llm.model, PROMPT_VERSION, thread_id, redactor, prompt_record
        ) as span:
            try:
                response = deps.llm.complete(system, messages, max_tokens=deps.memo_max_tokens)
            except Exception as error:  # noqa: BLE001 - budget refusal or provider error: the human still gets the file
                record_error(span, error)
                failed_call: dict[str, Any] = {"error": type(error).__name__, "detail": str(error)[:300], "fake": False}
                if isinstance(error, IncompleteLLMResponse):
                    # Cut off (max_tokens) or refused: the partial text is not used, but the call was paid for.
                    incomplete = error.response
                    failed_call |= {
                        "stop_reason": incomplete.stop_reason,
                        "input_tokens": incomplete.input_tokens,
                        "output_tokens": incomplete.output_tokens,
                        "cost_usd": incomplete.cost_usd,
                        "latency_s": incomplete.latency_s,
                        "cached": incomplete.cached,
                    }
                return {
                    "memo": None,
                    "memo_raw": None,
                    "memo_attempts": attempts + 1,
                    "memo_status": "llm_unavailable",
                    "pending_requests": [],
                    "llm_calls": [failed_call],
                }
            record_llm_response(span, redactor, response)
        llm_call = {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "latency_s": response.latency_s,
            "cached": response.cached,
            "fake": response.is_fake,
            "prompt_version": PROMPT_VERSION,
        }
        try:
            memo = parse_memo(response.text)
        except MemoParseError as error:
            return {
                "memo": None,
                "memo_raw": response.text,
                "memo_attempts": attempts + 1,
                "memo_status": "invalid_memo",
                "memo_parse_error": str(error),
                "pending_requests": [],
                "llm_calls": [llm_call],
            }
        rounds = state.get("llm_tool_rounds") or 0
        if memo.tool_requests and rounds < deps.max_llm_tool_rounds:
            requests = [
                ToolRequest(
                    request_id=f"llm-{rounds + 1}-{index + 1}",
                    tool=request.tool,
                    arguments=request.arguments,
                    origin="llm",
                    purpose="guideline search requested by the memo writer",
                ).model_dump(mode="json")
                for index, request in enumerate(memo.tool_requests)
            ]
            return {
                "pending_requests": requests,
                "llm_tool_rounds": rounds + 1,
                "memo_status": "tool_requested",
                "llm_calls": [llm_call],
            }
        return {
            "memo": memo.model_dump(mode="json"),
            "memo_raw": response.text,
            "memo_attempts": attempts + 1,
            "memo_status": "drafted",
            "pending_requests": [],
            "llm_calls": [llm_call],
        }

    def check(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Run the claim checker and decide whether to regenerate."""
        attempts = state.get("memo_attempts") or 0
        if state.get("memo") is None:
            detail = (
                "no memo could be drafted"
                if state.get("memo_status") == "llm_unavailable"
                else ("the answer was not a valid memo JSON object")
            )
            result = MemoCheck(passed=False, issues=[ClaimIssue(kind="invalid_memo", detail=detail)])
        else:
            result = check_memo(MemoDraft.model_validate(state["memo"]), evidence_of(state), policy_of(state))
        if result.passed:
            status = "passed"
        elif state.get("memo_status") == "llm_unavailable":
            status = "llm_unavailable"
        elif attempts >= deps.max_memo_attempts:
            status = "failed_check"
        else:
            status = "retrying"
        check_data = result.model_dump()
        return {
            "memo_check": check_data,
            "memo_status": status,
            "memo_history": [{"attempt": attempts, "memo": state.get("memo"), "check": check_data}],
        }

    def human_review(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Stop for a human. Only a valid human decision continues the run."""
        application = application_of(state)
        policy = policy_of(state)
        raw = interrupt(build_review_request(state, policy, application))
        decision, errors = validate_decision(raw, policy, application)
        if decision is None:
            submitted = raw if isinstance(raw, dict) else {"value": str(raw)[:500]}
            return {
                "human_decision": None,
                "status": "awaiting_review",
                "rejected_decisions": [{"submitted": submitted, "errors": errors, "at": deps.clock().isoformat()}],
            }
        return {
            "human_decision": {
                **decision.model_dump(),
                "is_override": is_override(decision.action, policy.recommendation),
                "reference_recommendation": policy.recommendation,
                "decided_at": deps.clock().isoformat(),
            },
            "status": "decided",
        }

    def record(state: dict[str, Any], thread_id: str, redactor: PiiRedactor) -> dict[str, Any]:
        """Append the decision record (idempotent) and finish."""
        decision = state["human_decision"]
        record_id = (
            "DR-"
            + hashlib.sha256(f"{thread_id}|{state['inputs_sha256']}|{canonical_json(decision)}".encode()).hexdigest()[
                :24
            ]
        )
        llm_calls = state.get("llm_calls") or []
        fake = any(call.get("fake") for call in llm_calls)
        policy = policy_of(state)
        stored = deps.decision_log.append(
            {
                "record_id": record_id,
                "recorded_at": deps.clock().isoformat(),
                "thread_id": thread_id,
                "application_id": state["application_id"],
                "data_label": SYNTHETIC_LABEL,
                "inputs_sha256": state["inputs_sha256"],
                "injection_screen": state.get("injection_hits") or [],
                "tool_calls": [
                    {key: value for key, value in result.items() if key != "latency_ms"}
                    for result in state.get("tool_results") or []
                ],
                "tool_rejections": state.get("tool_rejections") or [],
                "policy_result": state["policy_result"],
                "memo": state.get("memo"),
                "memo_status": state.get("memo_status"),
                "memo_check": state.get("memo_check"),
                "memo_attempts": state.get("memo_attempts"),
                "llm_calls": llm_calls,
                "fake_llm": fake,
                "stamp": FAKE_LLM_STAMP if fake else None,
                "versions": {
                    "graph_version": GRAPH_VERSION,
                    "policy_version": policy.policy_version,
                    "prompt_version": PROMPT_VERSION,
                    "llm_provider": deps.llm.provider,
                    "llm_model": deps.llm.model,
                },
                "reviewer": decision,
                "rejected_reviewer_attempts": state.get("rejected_decisions") or [],
                "reference_recommendation": policy.recommendation,
                "final_status": final_status(decision["action"]),
            }
        )
        return {
            "decision_record": {
                "record_id": stored["record_id"],
                "record_sha256": stored["record_sha256"],
                "path": str(deps.decision_log.path),
            },
            "status": final_status(decision["action"]),
        }

    def route_after_dispatch(state: dict[str, Any]) -> list[Send] | str:
        """Fan out one run_tool task per approved call, or go straight on when there are none."""
        calls = state.get("approved_calls") or []
        if not calls:
            return "after_tools"
        return [Send("run_tool", {"call": call, "application": state["application"]}) for call in calls]

    def route_after_tools(state: dict[str, Any]) -> str:
        """First batch -> policy; LLM-requested batches -> back to the memo writer."""
        return "evaluate_policy" if state.get("policy_result") is None else "draft_memo"

    def route_after_draft(state: dict[str, Any]) -> str:
        """More guideline searches requested -> the tool gate; otherwise check the memo."""
        return "dispatch_tools" if state.get("pending_requests") else "check_memo"

    def route_after_check(state: dict[str, Any]) -> str:
        """Regenerate once on failure; then always the human."""
        return "draft_memo" if state.get("memo_status") == "retrying" else "human_review"

    def route_after_review(state: dict[str, Any]) -> str:
        """Ask again after a refused decision; record a valid one."""
        return "record_decision" if state.get("human_decision") else "human_review"

    graph = StateGraph(AgentState, input_schema=AgentInput)
    bodies: dict[str, NodeBody] = {
        "intake": intake,
        "injection_screen": injection_screen,
        "plan_tools": plan_tools,
        "dispatch_tools": dispatch_tools,
        "run_tool": run_tool,
        "after_tools": after_tools,
        "evaluate_policy": evaluate,
        "draft_memo": draft_memo,
        "check_memo": check,
        "human_review": human_review,
        "record_decision": record,
    }
    for name, body in bodies.items():
        graph.add_node(name, traced(name, body))
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "injection_screen")
    graph.add_edge("injection_screen", "plan_tools")
    graph.add_edge("plan_tools", "dispatch_tools")
    graph.add_conditional_edges("dispatch_tools", route_after_dispatch, ["run_tool", "after_tools"])
    graph.add_edge("run_tool", "after_tools")
    graph.add_conditional_edges("after_tools", route_after_tools, ["evaluate_policy", "draft_memo"])
    graph.add_edge("evaluate_policy", "draft_memo")
    graph.add_conditional_edges("draft_memo", route_after_draft, ["dispatch_tools", "check_memo"])
    graph.add_conditional_edges("check_memo", route_after_check, ["draft_memo", "human_review"])
    graph.add_conditional_edges("human_review", route_after_review, ["human_review", "record_decision"])
    graph.add_edge("record_decision", END)
    return graph
