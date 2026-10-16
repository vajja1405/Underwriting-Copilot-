"""Evaluate the underwriting agent on the SYNTHETIC applications (`make eval-agent`).

Writes results/agent_metrics.json and results/agent_per_application.jsonl.

What is measured, and how:
1. Reference policy vs the designed labels (eval/applications ground truth): recommendation agreement, exact rule
   agreement, confusion matrix, and which referral triggers of the corpus the set exercises. Screening is taken
   from the design (planted watchlist owners match), so this measures the policy alone.
2. Injection detector recall and false-positive rate, per application.
3. The deterministic path through the real LangGraph graph for every application, up to the human checkpoint:
   real sanctions screening (full OFAC + OpenSanctions lists, default) or the designed outcomes (--screening
   ground_truth), real guideline retrieval (--retrieval, default hybrid), the risk service if UWC_RISK_API_URL is
   set. Memos come from the deterministic template writer (FAKE LLM — test mode), so this measures routing and
   escalation, not writing. No case may be decided without a human.
4. LLM-dependent metrics (memo agreement, citation accuracy, unsupported-claim rate, cost, LLM latency) need a real
   model: run with --provider anthropic|openai and a key in .env. Without one they are written as "not measured"
   and no LLM call is made.

Rates have percentile bootstrap 95% intervals (1,000 resamples of applications, seed 42).
"""

import argparse
import json
import os
import platform
import subprocess
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from uwcopilot.agent.applications import CreditApplication, InsuranceApplication, load_applications
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.evaluation import (
    BOOTSTRAP_SEED,
    DEFAULT_LINES,
    N_RESAMPLES,
    PathCase,
    evaluate_policy_case,
    path_case,
    summarize_injection_detector,
    summarize_llm,
    summarize_path,
    summarize_policy,
)
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import GRAPH_VERSION, AgentDeps
from uwcopilot.agent.memo import PROMPT_VERSION
from uwcopilot.agent.policy import POLICY_VERSION
from uwcopilot.agent.policy_startup import STARTUP_POLICY_VERSION
from uwcopilot.agent.runner import compile_agent, start_run
from uwcopilot.agent.settings import load_agent_settings
from uwcopilot.agent.synthetic_apps import GENERATOR_VERSION
from uwcopilot.agent.tools import close_tool_handlers, guideline_search_handler, screening_handler
from uwcopilot.common.paths import RAW_DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.factory import MissingAPIKeyError, get_llm_client
from uwcopilot.observability.tracing import AgentTracer, build_tracer_provider
from uwcopilot.risk.client import RiskApiClient, describe_risk_service
from uwcopilot.tools.registry import ToolHandler, ToolUnavailable
from uwcopilot.tools.schemas import ListSnapshot, ScreeningCandidate, ScreeningRequest, ScreeningResult

DEFAULT_APPLICATIONS = REPO_ROOT / "eval" / "applications"
NO_KEY_REASON = (
    "not measured: no LLM provider was used (no ANTHROPIC_API_KEY or OPENAI_API_KEY configured, or --provider none); "
    "no LLM call was made"
)
FAKE_SNAPSHOT_SHA = "0" * 64


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Evaluate the underwriting agent on the SYNTHETIC applications.")
    parser.add_argument("--applications-dir", type=Path, default=DEFAULT_APPLICATIONS)
    parser.add_argument("--provider", choices=["none", "anthropic", "openai"], default="none")
    parser.add_argument("--screening", choices=["full", "ground_truth"], default="full")
    parser.add_argument("--retrieval", choices=["hybrid_rerank", "hybrid", "dense", "bm25", "off"], default="hybrid")
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N applications")
    parser.add_argument(
        "--llm-cache",
        action="store_true",
        help="serve repeated LLM requests from the on-disk cache (off by default, so cost and latency are measured)",
    )
    parser.add_argument("--output", type=Path, default=RESULTS_DIR / "agent_metrics.json")
    parser.add_argument("--per-application", type=Path, default=RESULTS_DIR / "agent_per_application.jsonl")
    return parser.parse_args(argv)


def git_state() -> dict[str, Any]:
    """The commit the code ran from and whether the working tree had uncommitted changes."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=REPO_ROOT)
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True, cwd=REPO_ROOT
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "uncommitted_changes": None}
    return {"commit": commit.stdout.strip(), "uncommitted_changes": bool(status.stdout.strip())}


def ground_truth_screening_handler(
    applications: list[CreditApplication | InsuranceApplication], sdn_sha256: str | None
) -> ToolHandler:
    """A screen_entity handler that returns the designed outcome: planted names match, all others are clear."""
    planted: dict[str, str] = {}
    for application in applications:
        for truth in application.ground_truth.watchlist_derived_owners:
            owner = next(owner for owner in application.owners if owner.owner_id == truth.owner_id)
            planted[owner.name] = truth.derived_from_list_entry_id
    snapshot = ListSnapshot(
        source="OFAC",
        file_name="designed-ground-truth (no list was screened)",
        sha256=sdn_sha256 or FAKE_SNAPSHOT_SHA,
        snapshot_date=None,
    )

    def screen(request: ScreeningRequest) -> ScreeningResult:
        """The designed outcome for one name."""
        entry = planted.get(request.name)
        candidates = []
        if entry is not None:
            candidates.append(
                ScreeningCandidate(
                    list_entry_id=entry,
                    list_name="OFAC SDN (designed ground truth)",
                    source="OFAC",
                    matched_name=request.name,
                    score=1.0,
                    explanation=["designed ground truth: this owner was derived from the list entry"],
                )
            )
        return ScreeningResult(
            request=request,
            candidates=candidates,
            status="potential_match" if candidates else "clear",
            list_snapshots=[snapshot],
        )

    return screen


def retrieval_off_handler(query: Any) -> Any:
    """Used with --retrieval off: the search tool is reported as unavailable."""
    del query
    raise ToolUnavailable("guideline retrieval was turned off for this evaluation run (--retrieval off)")


def build_llm(provider: str, use_cache: bool = False) -> tuple[LLMClient, bool]:
    """(client, is_real). provider "none" gives the deterministic template memo writer (fake provider).

    The LLM cache is off unless use_cache is set: a cached answer costs nothing and returns in milliseconds, so a
    re-run served from the cache would otherwise report near-zero cost and latency. summarize_llm() also leaves
    any cached call out of cost and latency.
    """
    if provider == "none":
        return get_llm_client("fake", fake_responses=template_memo_responder), False
    return get_llm_client(provider, use_cache=use_cache), True


def warm_up(handlers: dict[str, ToolHandler]) -> dict[str, float]:
    """Load the matcher and the retriever once before timing (their load time is reported separately)."""
    timings: dict[str, float] = {}
    started = time.perf_counter()
    try:
        handlers["screen_entity"](ScreeningRequest(name="Warmup Synthetic Name LLC", entity_type="organization"))
    except ToolUnavailable:
        pass
    timings["screening_load_s"] = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    try:
        from uwcopilot.tools.schemas import GuidelineQuery

        handlers["search_guidelines"](GuidelineQuery(query="debt service coverage ratio minimum", top_k=1))
    except ToolUnavailable:
        pass
    timings["retrieval_load_s"] = round(time.perf_counter() - started, 2)
    return timings


def run_path(
    applications: list[CreditApplication | InsuranceApplication],
    llm: LLMClient,
    handlers: dict[str, ToolHandler],
    tracer: AgentTracer,
    log_dir: Path,
) -> list[PathCase]:
    """Run every application through the graph up to the human checkpoint (no decision is ever submitted)."""
    deps = AgentDeps(llm=llm, tool_handlers=handlers, decision_log=DecisionLog(log_dir / "unused.jsonl"), tracer=tracer)
    agent = compile_agent(deps, InMemorySaver())
    cases = []
    for application in applications:
        payload = application.model_dump(mode="json")
        started = time.perf_counter()
        outcome = start_run(agent, payload, f"eval-{application.application_id}", tracer)
        latency_ms = (time.perf_counter() - started) * 1000.0
        cases.append(path_case(application, outcome.review_request, outcome.state, latency_ms))
        print(
            f"{application.application_id} policy={cases[-1].policy_recommendation:24s} "
            f"queue={cases[-1].review_queue:28s} matches={cases[-1].potential_match_parties} "
            f"{latency_ms:8.1f} ms"
        )
    return cases


def application_lines(applications: list) -> tuple[str, ...]:
    """The lines whose referral triggers the coverage metric counts.

    A Phase 5 set (credit and insurance files, or a --limit slice of one) keeps the credit and insurance triggers,
    as before. A set with startup files adds the startup program's triggers, and a startup-only set counts only them.
    """
    present = {application.line for application in applications}
    if "startup_credit" not in present:
        return DEFAULT_LINES
    if present == {"startup_credit"}:
        return ("startup_credit",)
    return (*DEFAULT_LINES, "startup_credit")


def startup_count(applications: list) -> dict[str, int]:
    """{"startup_credit": n} when the set has startup files, else {} (the Phase 5 report keeps its keys)."""
    count = sum(1 for application in applications if application.line == "startup_credit")
    return {"startup_credit": count} if count else {}


def policy_version_for(lines: tuple[str, ...]) -> str:
    """The reference policy version(s) that evaluated these lines."""
    versions = []
    if "credit" in lines or "insurance" in lines:
        versions.append(POLICY_VERSION)
    if "startup_credit" in lines:
        versions.append(STARTUP_POLICY_VERSION)
    return "; ".join(versions) or POLICY_VERSION


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation and write the result files."""
    args = parse_args(argv)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    applications = load_applications(args.applications_dir)
    if args.limit is not None:
        applications = applications[: args.limit]
    manifest_path = args.applications_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    try:
        llm, real_llm = build_llm(args.provider, use_cache=args.llm_cache)
    except MissingAPIKeyError as error:
        print(f"Refusing to run with --provider {args.provider}: {error}")
        return 2

    lines = application_lines(applications)
    policy_cases = [evaluate_policy_case(application) for application in applications]
    policy_summary = summarize_policy(policy_cases, lines)
    detector_summary = summarize_injection_detector(applications)

    settings = load_agent_settings()
    if args.screening == "full":
        if not (RAW_DATA_DIR / "ofac" / "sdn.csv").exists():
            print("data/raw/ofac/sdn.csv is missing: run `make data` or use --screening ground_truth.")
            return 1
        screen = screening_handler()
    else:
        screen = ground_truth_screening_handler(applications, manifest.get("watchlist_source", {}).get("sha256"))
    handlers: dict[str, ToolHandler] = {
        "screen_entity": screen,
        "search_guidelines": retrieval_off_handler
        if args.retrieval == "off"
        else guideline_search_handler(args.retrieval),
        "score_risk": RiskApiClient.from_settings(settings.risk_api).score,
    }
    provider = build_tracer_provider(settings.otlp_endpoint) if settings.otlp_endpoint else None
    tracer = AgentTracer(provider)
    load_timings = warm_up(handlers)
    try:
        with tempfile.TemporaryDirectory() as log_dir:
            path_cases = run_path(applications, llm, handlers, tracer, Path(log_dir))
    finally:
        close_tool_handlers(handlers)
    if provider is not None:
        provider.force_flush()
        provider.shutdown()
    path_summary = summarize_path(path_cases, applications)

    if real_llm:
        llm_summary: dict[str, Any] = {"status": "measured", **summarize_llm(path_cases)}
    else:
        llm_summary = {
            "status": NO_KEY_REASON,
            "memo_recommendation_agreement_with_policy": NO_KEY_REASON,
            "citation_accuracy": NO_KEY_REASON,
            "unsupported_claim_rate": NO_KEY_REASON,
            "cost_usd_per_application": NO_KEY_REASON,
            "llm_latency_ms_p50_p95": NO_KEY_REASON,
            "memo_writer_used_instead": "deterministic template memo (FAKE LLM — test mode); its memos are not "
            "evidence of LLM quality and are not scored as such",
        }
    tool_status = path_summary["tool_status_counts"]
    report = {
        "generated_at": started_at,
        "generated_by": "scripts/eval_agent.py",
        "data_label": "SYNTHETIC",
        "status": "measured (deterministic metrics)" + ("" if real_llm else "; LLM-dependent metrics not measured"),
        "applications": {
            "dir": str(args.applications_dir.relative_to(REPO_ROOT))
            if args.applications_dir.is_relative_to(REPO_ROOT)
            else str(args.applications_dir),
            "count": len(applications),
            "credit": sum(1 for application in applications if application.line == "credit"),
            "insurance": sum(1 for application in applications if application.line == "insurance"),
            **startup_count(applications),
            "set_sha256": manifest.get("applications_sha256"),
            "generator_version": manifest.get("generator_version", GENERATOR_VERSION),
            "limited_to_first": args.limit,
        },
        "versions": {
            "policy_version": policy_version_for(lines),
            "prompt_version": PROMPT_VERSION,
            "graph_version": GRAPH_VERSION,
            "git": git_state(),
        },
        "configuration": {
            "screening": "full OFAC SDN + OpenSanctions lists (uwcopilot.kyc.screening default matcher)"
            if args.screening == "full"
            else "designed ground truth (planted owners match, no list screened)",
            "retrieval_mode": args.retrieval,
            "risk_tool": describe_risk_service(settings.risk_api),
            "llm": f"{llm.provider}:{llm.model}"
            + ("" if real_llm else " (deterministic template memo, FAKE LLM — test mode)"),
            "tracing": f"exported to {settings.otlp_endpoint}"
            if settings.otlp_endpoint
            else "spans created, not exported",
            "bootstrap": {"resamples": N_RESAMPLES, "seed": BOOTSTRAP_SEED, "confidence": 0.95, "unit": "application"},
            "warm_up_s": load_timings,
        },
        "reference_policy": policy_summary,
        "injection_detector": detector_summary,
        "deterministic_path": path_summary,
        "llm_dependent": llm_summary,
        "tool_runs": {
            "search_guidelines_ok": tool_status.get("search_guidelines:ok", 0),
            "screen_entity_ok": tool_status.get("screen_entity:ok", 0),
            "score_risk_ok": tool_status.get("score_risk:ok", 0),
            "score_risk_unavailable": tool_status.get("score_risk:unavailable", 0),
        },
        "environment": {
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "latency_note": "wall-clock on a shared machine; deterministic path only (template memo, no LLM time)",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with args.per_application.open("w", encoding="utf-8") as handle:
        for policy_case, case in zip(policy_cases, path_cases, strict=True):
            row = {"policy_check": asdict(policy_case), "path": asdict(case)}
            row["path"].pop("llm_calls", None)
            handle.write(json.dumps(row) + "\n")
    print(
        f"Policy agreement {policy_summary['recommendation_agreement']['value']}; "
        f"detector recall {detector_summary['recall']['value']}, "
        f"FPR {detector_summary['false_positive_rate']['value']}; "
        f"checkpoint reached {path_summary['reached_human_checkpoint']['value']}; "
        f"required escalations {path_summary['escalation']['all_required_escalations']['value']}"
    )
    print(f"Wrote {args.output} and {args.per_application}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
