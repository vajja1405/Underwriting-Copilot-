"""The Lender Desk demo: the EXISTING underwriting agent on SYNTHETIC structured applications.

Nothing here decides. The agent (uwcopilot.agent.graph) runs to its human checkpoint; a person's decision goes
through the graph's own human_review node, i.e. agent.hitl.validate_decision and agent.runner.resume_run. There is
no other way to record a decision.

- Files: every APP-*.json in eval/applications (credit, insurance) and eval/startup_applications (startup credit),
  all SYNTHETIC. The ground-truth label in each file is never sent to the agent or to the browser.
- The memo writer is the deterministic TEMPLATE memo (agent.fake_memo): it restates the reference policy's result;
  no language model is called, nothing is paid for. Every memo is stamped "FAKE LLM — test mode"; the UI says
  "template memo".
- Tools: the agent's own handlers. Guideline search needs the Qdrant index; when it is not built the tool reports
  "unavailable" (never a crash). Screening uses the lists named by UWC_SCREENING_LISTS (the Space: OFAC only).
  The risk model needs UWC_RISK_API_URL; without it the tool reports "unavailable". Tool outputs are deterministic,
  so they are remembered per arguments (CachedTool): the second run of a file does not search or screen again.
  An unexpected tool failure is logged in full on the server and reaches the agent (and so the page and the DEMO
  decision record) only as a plain sentence (SafeTool): exception text can hold server paths or other internals.
- One run per file per process (cached by the compute runner): GET /api/lender/files/{id} shows it.
- A DEMO decision: each user acts in a private sandbox. The file is run again (fast: cached tool outputs) on the
  user's own thread in a fresh in-memory checkpointer, resumed with the decision, and the decision record the
  graph writes (to a temporary decision log) is stored in that user's encrypted Store namespace, labelled DEMO.
  A refused decision is not stored; its reasons are returned (422).
- Roles: the web role maps to the agent's reviewer role: lender_senior -> senior_underwriter; lender_reviewer,
  founder and admin -> underwriter (demo files only; there are no real files in v0.1, and admin is not a credit
  role).
"""

import atexit
import logging
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from uwcopilot.agent.applications import (
    AnyApplication,
    applicant_name,
    application_inputs,
    canonical_json,
    load_application,
)
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.evidence import EvidenceItem, build_evidence, guideline_sections
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import GRAPH_VERSION, AgentDeps, injection_hits_of, tool_results_of
from uwcopilot.agent.hitl import APPROVED_OVERRIDE_REASONS, authority_exposure, is_override, validate_decision
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.runner import compile_agent, resume_run, start_run
from uwcopilot.agent.settings import load_agent_settings
from uwcopilot.agent.tools import guideline_search_handler, screening_handler
from uwcopilot.agent.vocab import REVIEWER_ACTIONS, SYNTHETIC_LABEL
from uwcopilot.kyc.screening import ScreeningListsError, configured_screening_lists
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.rag.prompt import detect_instruction_like_text
from uwcopilot.risk.client import RiskApiClient
from uwcopilot.tools.registry import ToolHandler, ToolUnavailable
from uwcopilot.tools.schemas import ListSnapshot
from uwcopilot.web.auth import Principal
from uwcopilot.web.schemas_lender import (
    ActionCheck,
    DecisionRequest,
    DecisionSummary,
    EvidenceOut,
    InjectionHitOut,
    LenderFileResponse,
    LineCount,
    MemoCheckOut,
    MemoOut,
    QueueItem,
    QueueResponse,
    ScreeningCandidateOut,
    ScreeningOut,
    ScreeningPanel,
    SectionResponse,
    ToolPanel,
    ToolStatusOut,
)
from uwcopilot.web.services.errors import Conflict, NotFound, ServiceError
from uwcopilot.web.services.text_checks import refuse_identifiers
from uwcopilot.web.store import owner_digest

logger = logging.getLogger(__name__)

DECISIONS_NAMESPACE = "lender_demo_decisions"
LINE_LABELS: dict[str, str] = {
    "credit": "Business credit",
    "insurance": "Commercial insurance",
    "startup_credit": "Startup credit",
}
AMOUNT_LABELS: dict[str, str] = {
    "credit": "requested loan amount (USD)",
    "insurance": "total insured value of the schedule (USD)",
    "startup_credit": "requested startup loan amount (USD)",
}
RECOMMENDATION_WORDS: dict[str, str] = {
    "approve": "approve",
    "refer": "refer to a higher authority",
    "decline_for_review": "decline, for a senior reviewer to confirm",
    "request_more_information": "request more information",
}
SECTION_DOCUMENTS: dict[str, str] = {
    "NBC": "Northwind Business Credit guideline (SYNTHETIC)",
    "HM": "Harborline Mutual commercial insurance guideline (SYNTHETIC)",
    "NSP": "Northwind startup lending program (SYNTHETIC)",
}
MEMO_NOTE = (
    "Template memo: it restates the reference policy's result with its citations. No language model wrote it and "
    "it costs nothing; it is stamped 'FAKE LLM — test mode'."
)
SCREENING_NOTE = (
    "Screening finds possible matches only. A potential match always goes to a person; nothing here can clear or "
    "confirm one. The threshold was chosen on the full lists (OFAC + OpenSanctions); with fewer lists its accuracy "
    "is not measured."
)
PROBE_RATIONALE = "availability check only, not a decision"
PROBE_REVIEWER = "availability_probe"


# ---------------------------------------------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------------------------------------------


class ToolFailed(RuntimeError):
    """A tool failed on the server; the message is safe to show (the details are only in the server log)."""


class SafeTool:
    """Wraps a tool handler: ToolUnavailable passes through (its message is written to be shown); any other failure
    is logged with its traceback and replaced by ToolFailed with a plain message (no exception text, no paths)."""

    def __init__(self, name: str, handler: ToolHandler) -> None:
        """name: the tool's name, used in the plain message."""
        self.name = name
        self.handler = handler

    def __call__(self, arguments: Any) -> Any:
        """The handler's output, or ToolFailed."""
        try:
            return self.handler(arguments)
        except ToolUnavailable:
            raise
        except Exception as error:
            logger.exception("tool %s failed", self.name)
            raise ToolFailed(f"{self.name} failed on the server; the details are in the server log") from error

    def close(self) -> None:
        """Close the wrapped handler when it holds resources."""
        close = getattr(self.handler, "close", None)
        if callable(close):
            close()


class CachedTool:
    """A deterministic tool handler that remembers its outputs by its validated arguments (failures are not kept)."""

    def __init__(self, handler: ToolHandler) -> None:
        """Wrap handler."""
        self.handler = handler
        self._lock = threading.Lock()
        self._outputs: dict[str, Any] = {}

    def __call__(self, arguments: Any) -> Any:
        """The cached output for these arguments, or the handler's output (then cached)."""
        data = arguments.model_dump(mode="json") if isinstance(arguments, BaseModel) else arguments
        key = canonical_json(data)
        with self._lock:
            if key in self._outputs:
                return self._outputs[key]
        output = self.handler(arguments)
        with self._lock:
            self._outputs[key] = output
        return output

    def close(self) -> None:
        """Close the wrapped handler when it holds resources (the guideline retriever)."""
        close = getattr(self.handler, "close", None)
        if callable(close):
            close()


class GuidelineSearchAdapter:
    """The agent's guideline search, reporting a missing Qdrant index as "unavailable" instead of an error."""

    def __init__(self) -> None:
        """The retriever is built on the first search (embedded Qdrant + fastembed models)."""
        self.tool = guideline_search_handler()

    def __call__(self, query: Any) -> Any:
        """One search; IndexNotBuiltError becomes ToolUnavailable."""
        from uwcopilot.rag.index import IndexNotBuiltError

        try:
            return self.tool(query)
        except IndexNotBuiltError as error:
            raise ToolUnavailable(f"the guideline index is not built on this server: {error}") from error

    def close(self) -> None:
        """Close the retriever."""
        self.tool.close()


def demo_tool_handlers() -> dict[str, ToolHandler]:
    """The agent's real handlers for the demo: cached search and screening, the risk client as configured."""
    return {
        "search_guidelines": CachedTool(SafeTool("guideline search", GuidelineSearchAdapter())),
        "screen_entity": CachedTool(SafeTool("sanctions screening", screening_handler())),
        "score_risk": SafeTool("risk model", RiskApiClient.from_settings(load_agent_settings().risk_api).score),
    }


def template_llm() -> FakeLLM:
    """The deterministic template memo writer (no language model)."""
    return FakeLLM(template_memo_responder)


# ---------------------------------------------------------------------------------------------------------------
# Files and runs
# ---------------------------------------------------------------------------------------------------------------


def load_demo_files(directories: tuple[Path, ...]) -> dict[str, AnyApplication]:
    """{application id: application} for every SYNTHETIC APP-*.json file in the folders (missing folders skipped)."""
    files: dict[str, AnyApplication] = {}
    for directory in directories:
        for path in sorted(directory.glob("APP-*.json")):
            application = load_application(path)
            if application.data_label == SYNTHETIC_LABEL:
                files[application.application_id] = application
    return dict(sorted(files.items()))


@dataclass(frozen=True)
class FileRun:
    """One agent run of a file, stopped at the human checkpoint."""

    application: AnyApplication
    review: dict[str, Any]
    state: dict[str, Any]
    policy: PolicyResult
    evidence: dict[str, EvidenceItem]


def reviewer_role_for(principal: Principal) -> str:
    """The agent reviewer role a web user acts with on a DEMO file (see the module docstring)."""
    return "senior_underwriter" if "lender_senior" in principal.roles else "underwriter"


def demo_thread_id(principal: Principal, file_id: str) -> str:
    """The user's own LangGraph thread for a file (the owner id is hashed, never shown)."""
    return f"demo-{owner_digest(principal.owner_id)[:16]}-{file_id}"


# ---------------------------------------------------------------------------------------------------------------
# Response pieces
# ---------------------------------------------------------------------------------------------------------------


def screening_panel(results: list[Any]) -> ScreeningPanel:
    """Screening results per party and the list files they were screened against."""
    rows: list[ScreeningOut] = []
    snapshots: dict[str, ListSnapshot] = {}
    for result in results:
        if result.tool != "screen_entity":
            continue
        output = result.output or {}
        candidates = [
            ScreeningCandidateOut(
                list_entry_id=item["list_entry_id"],
                list_name=item["list_name"],
                source=item["source"],
                matched_name=item["matched_name"],
                score=item["score"],
            )
            for item in output.get("candidates", [])
        ]
        for snapshot in output.get("list_snapshots", []):
            snapshots.setdefault(snapshot["sha256"], ListSnapshot.model_validate(snapshot))
        rows.append(
            ScreeningOut(
                party_id=result.party_id,
                result_id=result.result_id,
                status=output.get("status", result.status) if result.status == "ok" else result.status,
                top_score=max((item.score for item in candidates), default=None),
                candidates=candidates,
                error=result.error,
            )
        )
    try:
        configured = list(configured_screening_lists())
    except ScreeningListsError as error:
        configured = [f"invalid setting: {error}"]
    return ScreeningPanel(
        configured_lists=configured, lists_used=list(snapshots.values()), results=rows, note=SCREENING_NOTE
    )


def tool_panel(results: list[Any], tool: str) -> ToolPanel:
    """One tool's overall status: ok when every call was ok, otherwise the first problem."""
    calls = [result for result in results if result.tool == tool]
    if not calls:
        return ToolPanel(status="not_called", detail=f"the agent did not call {tool} for this file")
    problems = [result for result in calls if result.status != "ok"]
    if not problems:
        return ToolPanel(status="ok", detail=f"{len(calls)} call(s) answered")
    first = problems[0]
    return ToolPanel(status=first.status, detail=first.error or first.status)


def action_checks(policy: PolicyResult, application: AnyApplication, role: str) -> list[ActionCheck]:
    """For each action: would validate_decision accept it from this role (with a valid rationale and, where an
    override needs one, an approved override reason)? The real decision is still checked again by the graph."""
    checks = []
    for action in REVIEWER_ACTIONS:
        needs_reason = is_override(action, policy.recommendation)
        probe = {
            "action": action,
            "reviewer_id": PROBE_REVIEWER,
            "role": role,
            "rationale": PROBE_RATIONALE,
            "override_reason": APPROVED_OVERRIDE_REASONS[0] if needs_reason else None,
        }
        decision, errors = validate_decision(probe, policy, application)
        checks.append(
            ActionCheck(action=action, allowed=decision is not None, reasons=errors, needs_override_reason=needs_reason)
        )
    return checks


def memo_parts(review: dict[str, Any]) -> tuple[MemoOut | None, MemoCheckOut | None]:
    """The memo and its check from the review request."""
    memo = review.get("memo")
    check = review.get("memo_check")
    memo_out = None
    if memo:
        memo_out = MemoOut(recommendation=memo["recommendation"], summary=memo["summary"], reasons=memo["reasons"])
    check_out = MemoCheckOut(passed=check["passed"], issues=check.get("issues", [])) if check else None
    return memo_out, check_out


def evidence_parts(run: FileRun) -> tuple[dict[str, EvidenceOut], list[str]]:
    """(non-guideline evidence by id, the guideline section ids the run cites)."""
    evidence = {}
    sections = []
    for evidence_id, item in run.evidence.items():
        if item.kind == "guideline":
            sections.append(evidence_id)
            continue
        evidence[evidence_id] = EvidenceOut(
            evidence_id=evidence_id, kind=item.kind, title=item.title, text=item.text, flags=item.flags
        )
    return evidence, sections


def decision_summary(record: dict[str, Any]) -> DecisionSummary:
    """The essentials of a stored decision record."""
    reviewer = record.get("reviewer") or {}
    return DecisionSummary(
        record_id=record["record_id"],
        record_sha256=record["record_sha256"],
        action=reviewer.get("action", ""),
        final_status=record.get("final_status", ""),
        reviewer_role=reviewer.get("role", ""),
        is_override=bool(reviewer.get("is_override")),
        decided_at=reviewer.get("decided_at"),
    )


def plain(text: Any) -> str:
    """Text for the Markdown record: one line, no backticks (the rationale is the user's own untrusted text)."""
    return " ".join(str(text).replace("`", "'").split())


def fenced_lines(text: Any) -> list[str]:
    """Untrusted text for a fenced block: its own line breaks kept, backticks replaced (so it cannot close the fence),
    each line's spacing tidied, empty lines at the ends dropped."""
    lines = [" ".join(line.replace("`", "'").split()) for line in str(text).splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines or [""]


def record_markdown(record: dict[str, Any]) -> str:
    """The decision record as Markdown (the JSON record is the system of record)."""
    reviewer = record.get("reviewer") or {}
    policy = record.get("policy_result") or {}
    lines = [
        f"# DEMO decision record: {record.get('application_id')} (SYNTHETIC)",
        "",
        "> DEMO on SYNTHETIC data, not a lending decision. The copilot never decides — a person does.",
        "",
        f"- Record id: `{record.get('record_id')}`",
        f"- Record SHA-256: `{record.get('record_sha256')}`",
        f"- Recorded at: {record.get('recorded_at')}",
        f"- Reviewer: {plain(reviewer.get('reviewer_id', ''))} (role: {reviewer.get('role')})",
        f"- Action: {reviewer.get('action')} -> {record.get('final_status')}",
        f"- Reference recommendation (policy): {record.get('reference_recommendation')}",
        f"- Override of the recommendation: {'yes' if reviewer.get('is_override') else 'no'}"
        + (f" ({reviewer.get('override_reason')})" if reviewer.get("override_reason") else ""),
        f"- Memo: template memo ({record.get('stamp') or 'no stamp'}), claim check passed: "
        f"{(record.get('memo_check') or {}).get('passed')}",
        "",
        "## Rationale (as typed by the reviewer)",
        "",
        "```text",
        *fenced_lines(reviewer.get("rationale", "")),
        "```",
        "",
        "## Policy findings",
        "",
    ]
    findings = policy.get("findings") or []
    for finding in findings:
        citations = ", ".join(finding.get("section_ids", []) + finding.get("evidence_ids", []))
        lines.append(f"- {finding.get('rule')}: {plain(finding.get('detail', ''))} [{citations}]")
    if not findings:
        lines.append("- none")
    lines += ["", "## Tool calls", ""]
    for call in record.get("tool_calls") or []:
        error = f" ({plain(call.get('error'))})" if call.get("error") else ""
        lines.append(f"- {call.get('result_id')} {call.get('tool')}: {call.get('status')}{error}")
    versions = record.get("versions") or {}
    lines += [
        "",
        "## Versions",
        "",
        *(f"- {name}: {value}" for name, value in versions.items()),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------------------------


class DecisionRefused(ServiceError):
    """The checkpoint refused the decision (422, with the reasons from validate_decision)."""

    status_code = 422


class LenderDemoService:
    """Queue, file runs, decisions and records of the Lender Desk demo."""

    def __init__(
        self,
        demo_dirs: tuple[Path, ...],
        handlers_factory: Callable[[], Mapping[str, ToolHandler]] = demo_tool_handlers,
    ) -> None:
        """Files and tool handlers are loaded on first use."""
        self.demo_dirs = demo_dirs
        self.handlers_factory = handlers_factory
        self._lock = threading.Lock()
        self._files: dict[str, AnyApplication] | None = None
        self._handlers: Mapping[str, ToolHandler] | None = None

    def files(self) -> dict[str, AnyApplication]:
        """Every demo file, loaded once."""
        with self._lock:
            if self._files is None:
                self._files = load_demo_files(self.demo_dirs)
            return self._files

    def handlers(self) -> Mapping[str, ToolHandler]:
        """The shared tool handlers (one retriever and one screening matcher per process), closed at exit."""
        with self._lock:
            if self._handlers is None:
                self._handlers = self.handlers_factory()
                atexit.register(self.close)
            return self._handlers

    def close(self) -> None:
        """Release what the handlers hold (the embedded Qdrant client complains if it is left open at exit)."""
        with self._lock:
            handlers, self._handlers = self._handlers, None
        for handler in (handlers or {}).values():
            close = getattr(handler, "close", None)
            if callable(close):
                close()

    def application(self, file_id: str) -> AnyApplication:
        """One demo file, or NotFound."""
        application = self.files().get(file_id)
        if application is None:
            raise NotFound("no such demo file")
        return application

    # Queue -----------------------------------------------------------------------------------------------------

    def queue(self, line: str | None = None, query: str | None = None) -> QueueResponse:
        """The demo files, filtered by line and by a case-insensitive search in the id and applicant name."""
        files = self.files()
        counts = {name: 0 for name in LINE_LABELS}
        for application in files.values():
            counts[application.line] += 1
        needle = (query or "").strip().casefold()
        items = []
        for file_id, application in files.items():
            if line and application.line != line:
                continue
            name = applicant_name(application)
            if needle and needle not in file_id.casefold() and needle not in name.casefold():
                continue
            items.append(
                QueueItem(
                    id=file_id,
                    line=application.line,
                    line_label=LINE_LABELS[application.line],
                    applicant=name,
                    requested_amount=authority_exposure(application),
                    amount_label=AMOUNT_LABELS[application.line],
                )
            )
        lines = [LineCount(id=name, label=LINE_LABELS[name], count=count) for name, count in counts.items()]
        return QueueResponse(files=items, total=len(items), lines=lines)

    # Runs ------------------------------------------------------------------------------------------------------

    def new_agent(self, decision_log: DecisionLog) -> Any:
        """The compiled graph with the template memo writer, the shared tools and a fresh in-memory checkpointer."""
        deps = AgentDeps(llm=template_llm(), tool_handlers=self.handlers(), decision_log=decision_log)
        return compile_agent(deps, InMemorySaver())

    def run_file(self, file_id: str) -> FileRun:
        """Run the agent on a file up to the human checkpoint (heavy: call it through the compute runner)."""
        application = self.application(file_id)
        with tempfile.TemporaryDirectory(prefix="uwc-demo-") as folder:
            agent = self.new_agent(DecisionLog(Path(folder) / "unused.jsonl"))
            outcome = start_run(agent, application_inputs(application), f"base-{file_id}")
        if outcome.status != "awaiting_review" or outcome.review_request is None:
            raise ServiceError(f"the agent did not stop at the human checkpoint for {file_id}")
        state = outcome.state
        policy = PolicyResult.model_validate(state["policy_result"])
        evidence = build_evidence(application, policy, tool_results_of(state), injection_hits_of(state))
        return FileRun(
            application=application, review=outcome.review_request, state=state, policy=policy, evidence=evidence
        )

    def file_view(self, run: FileRun, principal: Principal, decision: dict[str, Any] | None) -> LenderFileResponse:
        """The review screen for one user: the run plus the user's role, action checks and own decision."""
        review, policy, application = run.review, run.policy, run.application
        results = tool_results_of(run.state)
        role = reviewer_role_for(principal)
        memo, memo_check = memo_parts(review)
        evidence, sections = evidence_parts(run)
        recommendation = review["reference_recommendation"]
        return LenderFileResponse(
            id=application.application_id,
            line=application.line,
            line_label=LINE_LABELS[application.line],
            applicant=applicant_name(application),
            requested_amount=authority_exposure(application),
            amount_label=AMOUNT_LABELS[application.line],
            reference_recommendation=recommendation,
            recommendation_text=f"The policy suggests: {RECOMMENDATION_WORDS[recommendation]} — you decide.",
            required_reviewer_role=review["required_reviewer_role"],
            your_reviewer_role=role,
            review_queue=review["review_queue"],
            blocking_open=review["blocking_open"],
            referral_targets=review["referral_targets"],
            authority_exposure=review["authority_exposure"],
            approval_limits=review["approval_limits"],
            allowed_actions=review["allowed_actions"],
            action_checks=action_checks(policy, application, role),
            override_reasons=list(APPROVED_OVERRIDE_REASONS),
            findings=policy.findings,
            checks_passed=policy.checks_passed,
            calculations=policy.calculations,
            not_evaluated=policy.not_evaluated,
            screening=screening_panel(results),
            guideline_search=tool_panel(results, "search_guidelines"),
            risk_model=tool_panel(results, "score_risk"),
            tool_results=[ToolStatusOut.model_validate(item) for item in review["tool_results"]],
            tool_rejections=review["tool_rejections"],
            injection_hits=[InjectionHitOut.model_validate(hit) for hit in review["injection_hits"]],
            memo=memo,
            memo_status=review.get("memo_status"),
            memo_note=MEMO_NOTE,
            memo_check=memo_check,
            stamp=review.get("stamp"),
            evidence=evidence,
            cited_sections=sections,
            policy_version=policy.policy_version,
            graph_version=GRAPH_VERSION,
            decision=decision_summary(decision) if decision else None,
        )

    def decide(self, file_id: str, principal: Principal, body: DecisionRequest) -> dict[str, Any]:
        """Run the file on the user's own thread and resume it with the decision (heavy: use the compute runner).

        Returns the decision record the graph wrote. DecisionRefused (422) when the checkpoint refuses it.
        """
        refuse_identifiers(body.rationale, "the rationale")
        application = self.application(file_id)
        decision = {
            "action": body.action,
            "reviewer_id": principal.username,
            "role": reviewer_role_for(principal),
            "rationale": body.rationale,
            "override_reason": body.override_reason or None,
        }
        thread_id = demo_thread_id(principal, file_id)
        with tempfile.TemporaryDirectory(prefix="uwc-demo-") as folder:
            log = DecisionLog(Path(folder) / "decision_records.jsonl")
            agent = self.new_agent(log)
            started = start_run(agent, application_inputs(application), thread_id)
            if started.status != "awaiting_review":
                raise ServiceError(f"the agent did not stop at the human checkpoint for {file_id}")
            outcome = resume_run(agent, thread_id, decision)
            if outcome.status == "awaiting_review":
                rejected = outcome.state.get("rejected_decisions") or []
                reasons = rejected[-1]["errors"] if rejected else ["the checkpoint did not accept the decision"]
                raise DecisionRefused("The checkpoint refused this decision.", reasons=reasons)
            stored = (outcome.state.get("decision_record") or {}).get("record_id")
            record = log.find(stored) if stored else None
        if record is None:
            raise ServiceError("the decision was accepted but no decision record was written")
        return record

    # Sections --------------------------------------------------------------------------------------------------

    def section(self, section_id: str) -> SectionResponse:
        """One guideline or program section's text, or NotFound."""
        sections = guideline_sections()
        if section_id not in sections:
            raise NotFound("no such guideline section")
        title, text = sections[section_id]
        prefix = section_id.split("-", 1)[0]
        return SectionResponse(
            section_id=section_id,
            title=title,
            text=text,
            document=SECTION_DOCUMENTS.get(prefix, "SYNTHETIC guideline"),
            flags=detect_instruction_like_text(text),
        )


def existing_decision_conflict(file_id: str) -> Conflict:
    """The error for a second DEMO decision on the same file."""
    return Conflict(
        f"you already recorded a DEMO decision for {file_id}; delete it (DELETE /api/lender/files/{file_id}/record) "
        "to try another one"
    )
