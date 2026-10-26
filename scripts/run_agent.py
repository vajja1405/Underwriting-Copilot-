"""Run the underwriting agent on one SYNTHETIC application, stop at the human checkpoint, and accept a decision.

    uv run python scripts/run_agent.py --application eval/applications/APP-C-008.json --provider fake
    uv run python scripts/run_agent.py --application ... --provider fake --thread-id <id> \\
        --decision refer --reviewer-id jdoe --role senior_underwriter --rationale "..."

- --provider anthropic|openai needs the matching API key in the repo .env; without it the script refuses to run
  and makes no call. --provider fake uses the deterministic template memo writer, and every printed section is
  stamped "FAKE LLM — test mode".
- The run state is saved in a SQLite checkpoint (UWC_AGENT_DB, default .cache/agent/checkpoints.sqlite). Without
  --decision the script prints the review and exits; the run waits in the checkpoint and can be resumed later, by
  this or another process, with --thread-id and --decision.
- A decision the guidelines do not allow (for example a decline by an underwriter) is refused and the run keeps
  waiting. A valid one appends a decision record to UWC_DECISION_LOG.
- An override (an approve or decline against the recommendation) needs at least one reason code from
  config/override_reasons.yaml: --reason-code (repeat it for several) or the older --override-reason; --reason-text
  adds optional free text about the reasons. The record stores review_action (accept or override), the reason codes
  and the lineage block (governance/lineage.py; print it with scripts/lineage.py).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from uwcopilot.agent.applications import inputs_sha256, load_application
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps
from uwcopilot.agent.hitl import APPROVED_OVERRIDE_REASONS
from uwcopilot.agent.runner import (
    RunOutcome,
    close_checkpointer,
    compile_agent,
    current_outcome,
    open_sqlite_checkpointer,
    resume_run,
    start_run,
    thread_exists,
)
from uwcopilot.agent.settings import load_agent_settings
from uwcopilot.agent.tools import close_tool_handlers, default_tool_handlers
from uwcopilot.agent.vocab import FAKE_LLM_STAMP, REVIEWER_ACTIONS, REVIEWER_ROLES
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.factory import MissingAPIKeyError, get_llm_client
from uwcopilot.llm.settings import load_llm_settings
from uwcopilot.observability.tracing import AgentTracer, build_tracer_provider

EXIT_REFUSED = 2
EXIT_DECISION_REFUSED = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Run the underwriting agent with a human approval checkpoint.")
    parser.add_argument("--application", type=Path, required=True, help="an eval/applications/APP-*.json file")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "fake"],
        default=None,
        help="LLM provider (default: UWC_LLM_PROVIDER from .env)",
    )
    parser.add_argument("--thread-id", default=None, help="checkpoint thread (default: application id + input hash)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite checkpoint file (default: UWC_AGENT_DB)")
    parser.add_argument(
        "--retrieval",
        choices=["hybrid_rerank", "hybrid", "dense", "bm25"],
        default=None,
        help="guideline retrieval mode (default: the RAG default, hybrid_rerank)",
    )
    parser.add_argument("--decision", choices=REVIEWER_ACTIONS, default=None, help="the human decision")
    parser.add_argument("--reviewer-id", default=None)
    parser.add_argument("--role", choices=REVIEWER_ROLES, default=None)
    parser.add_argument("--rationale", default=None)
    parser.add_argument("--override-reason", default=None, help="one reason code (older form of --reason-code)")
    parser.add_argument(
        "--reason-code",
        action="append",
        default=[],
        dest="reason_codes",
        help=f"an approved reason code; repeat for several ({', '.join(APPROVED_OVERRIDE_REASONS)})",
    )
    parser.add_argument("--reason-text", default=None, help="optional free text about the reason codes")
    return parser.parse_args(argv)


def build_llm(provider: str) -> LLMClient:
    """The LLM client; raises MissingAPIKeyError for a real provider without a key."""
    if provider == "fake":
        return get_llm_client("fake", fake_responses=template_memo_responder)
    return get_llm_client(provider)


def stamp(fake: bool) -> str:
    """The banner printed on every section when the fake LLM wrote the memo."""
    return f"[{FAKE_LLM_STAMP}] " if fake else ""


def format_review(review: dict[str, Any], fake: bool) -> str:
    """The human checkpoint, as text: recommendation, memo with citations, checks, escalations, tools."""
    prefix = stamp(fake)
    lines = [
        f"{prefix}=== Human approval checkpoint: {review['application_id']} ({review['line']}, SYNTHETIC) ===",
        f"{prefix}Reference policy recommendation: {review['reference_recommendation']}",
        f"{prefix}Review queue: {review['review_queue']}; required reviewer role: {review['required_reviewer_role']}; "
        f"blocking referral open: {review['blocking_open']}",
        f"{prefix}Allowed actions: {', '.join(review['allowed_actions'])}",
    ]
    memo = review.get("memo")
    lines.append(f"{prefix}--- Memo (status: {review['memo_status']}) ---")
    if memo:
        lines.append(f"{prefix}Memo recommendation: {memo['recommendation']}")
        lines.append(f"{prefix}Summary: {memo['summary']}")
        for index, reason in enumerate(memo["reasons"], start=1):
            lines.append(f"{prefix}  {index}. {reason['text']} [{', '.join(reason['citations'])}]")
        cited = sorted({citation for reason in memo["reasons"] for citation in reason["citations"]})
        lines.append(f"{prefix}Citations used: {', '.join(cited) or 'none'}")
    else:
        lines.append(f"{prefix}No usable memo.")
    check = review.get("memo_check") or {}
    lines.append(f"{prefix}Claim checker passed: {check.get('passed')}")
    for issue in check.get("issues", []):
        lines.append(f"{prefix}  ! {issue['kind']}: {issue['detail']}")
    lines.append(f"{prefix}--- Escalations ---")
    for finding in review["escalations"]:
        targets = ", ".join(finding["refer_to"])
        lines.append(
            f"{prefix}  - {finding['rule']} -> {targets}{' (blocking)' if finding['blocking'] else ''}: "
            f"{finding['detail']} [{', '.join(finding['section_ids'] + finding['evidence_ids'])}]"
        )
    if not review["escalations"]:
        lines.append(f"{prefix}  none")
    lines.append(f"{prefix}--- Tool calls ---")
    for result in review["tool_results"]:
        lines.append(
            f"{prefix}  {result['result_id']} {result['tool']} ({result['purpose']}): {result['status']}"
            + (f" - {result['error']}" if result.get("error") else "")
        )
    for rejection in review["tool_rejections"]:
        lines.append(f"{prefix}  rejected {rejection['tool']} ({rejection['origin']}): {rejection['reason']}")
    for hit in review["injection_hits"]:
        lines.append(f"{prefix}  injection screen flagged {hit['field']}: {', '.join(hit['patterns'])}")
    for rejection in review.get("previous_rejections", []):
        lines.append(f"{prefix}  previous decision refused: {'; '.join(rejection['errors'])}")
    return "\n".join(lines)


def decision_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    """The human decision given on the command line, or None."""
    if args.decision is None:
        return None
    decision: dict[str, Any] = {
        "action": args.decision,
        "reviewer_id": args.reviewer_id,
        "role": args.role,
        "rationale": args.rationale,
    }
    if args.override_reason:
        decision["override_reason"] = args.override_reason
    if args.reason_codes:
        decision["reason_codes"] = list(args.reason_codes)
    if args.reason_text:
        decision["reason_text"] = args.reason_text
    return decision


def report(outcome: RunOutcome, fake: bool, thread_id: str, application_path: Path) -> int:
    """Print where the thread stands and return an exit code."""
    if outcome.status == "awaiting_review":
        print(format_review(outcome.review_request or {}, fake))
        print(
            f"\n{stamp(fake)}Waiting for a human decision. Resume with:\n  uv run python scripts/run_agent.py "
            f"--application {application_path} --provider {'fake' if fake else '<provider>'} --thread-id {thread_id} "
            "--decision <approve|decline|refer|request_more_information> --reviewer-id <id> "
            '--role <underwriter|senior_underwriter> --rationale "..."'
        )
        return 0
    record = outcome.state.get("decision_record") or {}
    print(f"{stamp(fake)}Decision recorded: {outcome.state.get('status')}")
    print(
        f"{stamp(fake)}Decision record {record.get('record_id')} (sha256 {record.get('record_sha256')}) in "
        f"{record.get('path')}"
    )
    print(json.dumps(outcome.state.get("human_decision"), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Start or resume one run."""
    args = parse_args(argv)
    llm_settings = load_llm_settings()
    provider = args.provider or llm_settings.provider
    try:
        llm = build_llm(provider)
    except MissingAPIKeyError as error:
        print(
            f"Refusing to run: {error}\nUse --provider fake for an offline test run (output is stamped "
            f"'{FAKE_LLM_STAMP}').",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    fake = provider == "fake"
    application = load_application(args.application)
    thread_id = args.thread_id or f"{application.application_id}-{inputs_sha256(application)[:12]}"
    settings = load_agent_settings()
    tracer_provider = build_tracer_provider(settings.otlp_endpoint) if settings.otlp_endpoint else None
    tracer = AgentTracer(tracer_provider)
    handlers = default_tool_handlers(settings, args.retrieval)
    deps = AgentDeps(
        llm=llm,
        tool_handlers=handlers,
        decision_log=DecisionLog(settings.decision_log),
        tracer=tracer,
    )
    saver = open_sqlite_checkpointer(args.db or settings.checkpoint_db)
    try:
        agent = compile_agent(deps, saver)
        if thread_exists(agent, thread_id):
            outcome = current_outcome(agent, thread_id)
            print(f"{stamp(fake)}Thread {thread_id} found in the checkpoint ({outcome.status}).")
        else:
            outcome = start_run(agent, application.model_dump(mode="json"), thread_id, tracer)
        decision = decision_from_args(args)
        if decision is not None and outcome.status == "awaiting_review":
            outcome = resume_run(agent, thread_id, decision, tracer)
            if outcome.status == "awaiting_review":
                print(format_review(outcome.review_request or {}, fake))
                print(f"\n{stamp(fake)}The decision was refused; the run is still waiting.", file=sys.stderr)
                return EXIT_DECISION_REFUSED
        return report(outcome, fake, thread_id, args.application)
    finally:
        close_checkpointer(saver)
        close_tool_handlers(handlers)
        if tracer_provider is not None:
            tracer_provider.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
