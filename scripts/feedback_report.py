"""SIMULATED reviewer feedback and decision lineage over the SYNTHETIC agent applications (`make feedback-report`).

Writes sections "feedback" and "lineage" of results/governance_metrics.json (risk.results.update_results) and the
SIMULATED decision log data/governance/simulated_review_log.jsonl (gitignored; rewritten on every run).

What it does:
1. Runs every application in eval/applications (65 SYNTHETIC files: 32 credit, 33 insurance) through the real agent
   graph up to the human checkpoint, offline. Screening returns the designed outcome (planted watchlist owners match,
   the handler of `scripts/eval_agent.py --screening ground_truth`; no list is screened). Guideline search is switched
   off. The risk tool reports "unavailable": the trained credit model is not in this checkout, so no model scores a
   file; the lineage still names the configured model version and says it did not answer. The memo is the
   deterministic template (FAKE LLM - test mode); no language model is called and nothing is paid for.
2. A SIMULATED reviewer decides each file. Its choices come from made-up probabilities (SimulationParameters,
   seed 42), never from data about real reviewers:
   - with probability override_probability it tries to override (approve or decline against the recommendation,
     among the actions the checkpoint lists as allowed for the file), giving one approved reason code, or two with
     probability second_reason_probability, and a short note with probability reason_text_probability;
   - otherwise it takes the action that matches the recommendation;
   - it acts as a senior_underwriter when the file needs one, otherwise with probability senior_probability.
   Every decision goes through the real checkpoint (agent.hitl.validate_decision). A refused decision is tried again
   as a senior_underwriter, then replaced by a referral; the refusals stay in the record (rejected_reviewer_attempts).
   Each file uses six uniform draws taken in application-id order, so one file's draws never depend on another's.
3. The graph writes each accepted decision as a chained record with reviewer_label "SIMULATED" and its lineage block.
4. governance/feedback.py summarizes the log (override rate with a bootstrap 95% CI, 1,000 resamples, seed 42, unit =
   decision; counts per reason, role, action and line). governance/lineage.py checks every record's lineage against
   the working tree (STALE / not checkable counts).

The feedback numbers describe the SIMULATION (its made-up probabilities and the checkpoint's rules), not real
reviewers: they show that review actions and reasons are recorded and counted, nothing about how often real
underwriters override. The lineage check runs right after the records are written, so matching hashes are expected;
it becomes informative when an older log is checked later (scripts/lineage.py).
"""

import argparse
import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from eval_agent import ground_truth_screening_handler, retrieval_off_handler
from uwcopilot.agent.applications import application_inputs, load_applications
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps
from uwcopilot.agent.memo import PROMPT_VERSION
from uwcopilot.agent.runner import RunOutcome, compile_agent, resume_run, start_run
from uwcopilot.agent.vocab import FAKE_LLM_STAMP, MATCHING_ACTION
from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.feedback import (
    OVERRIDE_REASONS_FILE,
    load_override_reasons,
    summarize_feedback,
)
from uwcopilot.governance.lineage import LINEAGE_VERSION, summarize_lineage_checks
from uwcopilot.governance.provenance import display_path
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.risk.results import update_results
from uwcopilot.tools.registry import ToolHandler, ToolUnavailable

DEFAULT_APPLICATIONS = REPO_ROOT / "eval" / "applications"
DEFAULT_LOG = DATA_DIR / "governance" / "simulated_review_log.jsonl"
DEFAULT_OUTPUT = RESULTS_DIR / "governance_metrics.json"
SIMULATED = "SIMULATED"
DATA_LABEL = "SYNTHETIC applications; SIMULATED reviewer actions"
RISK_UNAVAILABLE = (
    "score_risk is not called in the SIMULATED reviewer run: the trained credit model is not in this checkout and no "
    "risk service is started"
)
ACCEPT_RATIONALE = "SIMULATED reviewer: took the action that matches the reference recommendation."
OVERRIDE_RATIONALE = "SIMULATED reviewer: overrode the reference recommendation for the reason codes given."
FALLBACK_RATIONALE = "SIMULATED reviewer: referred the file after the checkpoint refused the first choice."
REASON_NOTE = "SIMULATED note: the reasons are documented in the file."


@dataclass(frozen=True)
class SimulationParameters:
    """The SIMULATED reviewer's made-up probabilities (not estimated from any data) and its seed."""

    seed: int = 42
    override_probability: float = 0.15
    senior_probability: float = 0.35
    second_reason_probability: float = 0.25
    reason_text_probability: float = 0.5


@dataclass(frozen=True)
class Draws:
    """The six uniform draws one file uses, taken whatever branch the file follows."""

    role: float
    override: float
    pick: float
    second_reason: float
    reason_text: float
    reasons: tuple[str, str]


@dataclass
class CheckpointStats:
    """What happened at the checkpoint across all files."""

    override_drawn: int = 0
    override_not_possible: int = 0
    override_attempt_refused: int = 0
    refused_attempts: int = 0
    escalated_to_senior: int = 0
    fell_back_to_refer: int = 0
    final_actions: dict[str, int] = field(default_factory=dict)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="SIMULATED reviewer feedback + lineage on SYNTHETIC applications.")
    parser.add_argument("--applications-dir", type=Path, default=DEFAULT_APPLICATIONS)
    parser.add_argument("--limit", type=int, default=None, help="use only the first N applications")
    parser.add_argument("--seed", type=int, default=SimulationParameters.seed)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="the SIMULATED decision log (rewritten)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-data-files", action="store_true", help="do not hash raw data files in the lineage check")
    return parser.parse_args(argv)


def draw(rng: random.Random, codes: tuple[str, ...]) -> Draws:
    """The draws for one file (always the same number, so later files do not depend on this one's branch)."""
    first, second = rng.sample(codes, 2)
    return Draws(rng.random(), rng.random(), rng.random(), rng.random(), rng.random(), (first, second))


def simulated_choice(review: dict[str, Any], draws: Draws, params: SimulationParameters) -> dict[str, Any]:
    """The SIMULATED reviewer's first decision for a file, from the review request and the file's draws."""
    recommendation = review["reference_recommendation"]
    needs_senior = review["required_reviewer_role"] == "senior_underwriter"
    role = "senior_underwriter" if needs_senior or draws.role < params.senior_probability else "underwriter"
    decision: dict[str, Any] = {"reviewer_id": f"{SIMULATED}-{role}", "role": role}
    overrides = [
        action
        for action in ("approve", "decline")
        if action in review["allowed_actions"] and action != MATCHING_ACTION[recommendation]
    ]
    if draws.override < params.override_probability and overrides:
        reason_count = 2 if draws.second_reason < params.second_reason_probability else 1
        decision |= {
            "action": overrides[int(draws.pick * len(overrides))],
            "rationale": OVERRIDE_RATIONALE,
            "reason_codes": list(draws.reasons[:reason_count]),
        }
        if draws.reason_text < params.reason_text_probability:
            decision["reason_text"] = REASON_NOTE
        return decision
    return decision | {"action": MATCHING_ACTION[recommendation], "rationale": ACCEPT_RATIONALE}


def offline_handlers(applications: list[Any], sdn_sha256: str | None) -> dict[str, ToolHandler]:
    """Designed screening, retrieval off, risk tool unavailable (module docstring)."""

    def risk_unavailable(request: Any) -> Any:
        """The risk tool is not available in this run."""
        del request
        raise ToolUnavailable(RISK_UNAVAILABLE)

    return {
        "screen_entity": ground_truth_screening_handler(applications, sdn_sha256),
        "search_guidelines": retrieval_off_handler,
        "score_risk": risk_unavailable,
    }


def decide_file(agent: Any, thread_id: str, choice: dict[str, Any], stats: CheckpointStats) -> RunOutcome:
    """Submit the choice; if refused, retry as a senior_underwriter, then refer. Returns the completed outcome."""
    outcome = resume_run(agent, thread_id, choice)
    if outcome.status == "completed":
        return outcome
    stats.refused_attempts += 1
    if choice.get("reason_codes"):
        stats.override_attempt_refused += 1
    if choice["role"] != "senior_underwriter":
        stats.escalated_to_senior += 1
        senior = choice | {"role": "senior_underwriter", "reviewer_id": f"{SIMULATED}-senior_underwriter"}
        outcome = resume_run(agent, thread_id, senior)
        if outcome.status == "completed":
            return outcome
        stats.refused_attempts += 1
    stats.fell_back_to_refer += 1
    refer = {
        "action": "refer",
        "reviewer_id": f"{SIMULATED}-senior_underwriter",
        "role": "senior_underwriter",
        "rationale": FALLBACK_RATIONALE,
    }
    outcome = resume_run(agent, thread_id, refer)
    if outcome.status != "completed":
        raise RuntimeError(f"the checkpoint refused even a senior referral for {thread_id}")
    return outcome


def run_simulation(
    applications: list[Any], deps: AgentDeps, params: SimulationParameters, codes: tuple[str, ...]
) -> CheckpointStats:
    """Run every file to the checkpoint and let the SIMULATED reviewer decide it (records go to deps.decision_log)."""
    agent = compile_agent(deps, InMemorySaver())
    rng = random.Random(params.seed)
    stats = CheckpointStats()
    for application in applications:
        draws = draw(rng, codes)
        thread_id = f"sim-{application.application_id}"
        started = start_run(agent, application_inputs(application), thread_id)
        if started.status != "awaiting_review" or started.review_request is None:
            raise RuntimeError(f"{application.application_id} did not stop at the human checkpoint")
        choice = simulated_choice(started.review_request, draws, params)
        if draws.override < params.override_probability:
            stats.override_drawn += 1
            if "reason_codes" not in choice:
                stats.override_not_possible += 1
        outcome = decide_file(agent, thread_id, choice, stats)
        action = outcome.state["human_decision"]["action"]
        stats.final_actions[action] = stats.final_actions.get(action, 0) + 1
    return stats


def lineage_overview(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The distinct model versions, prompt hashes and eval-set hashes the records name."""
    models: set[tuple[str, str, str]] = set()
    prompts: set[tuple[str, str]] = set()
    eval_sets: set[tuple[str, str]] = set()
    for record in records:
        lineage = record.get("lineage") or {}
        for model in lineage.get("models") or []:
            models.add((model["product"], str(model.get("model_version_label")), str(model.get("tool_status"))))
        for prompt_id, prompt in (lineage.get("prompts") or {}).items():
            prompts.add((prompt_id, prompt["sha256"]))
        for set_id, eval_set in (lineage.get("eval_sets") or {}).items():
            eval_sets.add((set_id, str(eval_set.get("set_sha256"))))
    return {
        "models": [{"product": p, "model_version_label": label, "tool_status": s} for p, label, s in sorted(models)],
        "prompts": [{"prompt_id": prompt_id, "sha256": digest} for prompt_id, digest in sorted(prompts)],
        "eval_sets": [{"eval_set": set_id, "set_sha256": digest} for set_id, digest in sorted(eval_sets)],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the simulation, summarize it and write the two results sections."""
    args = parse_args(argv)
    started_at = time.perf_counter()
    params = SimulationParameters(seed=args.seed)
    applications = load_applications(args.applications_dir)
    if args.limit is not None:
        applications = applications[: args.limit]
    manifest_path = args.applications_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    reasons = load_override_reasons()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.unlink(missing_ok=True)  # a generated simulation log: each run starts a fresh chain
    log = DecisionLog(args.log)
    deps = AgentDeps(
        llm=get_llm_client("fake", fake_responses=template_memo_responder),
        tool_handlers=offline_handlers(applications, manifest.get("watchlist_source", {}).get("sha256")),
        decision_log=log,
        reviewer_label=SIMULATED,
    )
    stats = run_simulation(applications, deps, params, reasons.codes())
    records = log.records()
    chain_problems = log.verify_chain()
    lines_present = sorted({application.line for application in applications})

    feedback_section = {
        "data_label": DATA_LABEL,
        "status": "measured on a SIMULATED reviewer log",
        "interpretation": "These rates describe the simulation (its made-up probabilities and the checkpoint's "
        "rules), not real reviewers. They show that review actions and reason codes are recorded and counted; they "
        "say nothing about how often real underwriters override.",
        "applications": {
            "dir": display_path(args.applications_dir),
            "count": len(applications),
            "by_line": {line: sum(1 for app in applications if app.line == line) for line in lines_present},
            "set_sha256": manifest.get("applications_sha256"),
            "limited_to_first": args.limit,
        },
        "simulation": {
            **asdict(params),
            "probabilities_are": "made up for this demonstration; not estimated from any data",
            "draws": "six uniform draws per application in application-id order (random.Random(seed))",
            "refused_decision_rule": "retry as senior_underwriter, then refer (the refusals stay in the record)",
        },
        "configuration": {
            "screening": "designed ground truth (planted owners match; no list screened)",
            "guideline_search": "off (reported unavailable)",
            "risk_tool": RISK_UNAVAILABLE,
            "memo": f"deterministic template memo ({FAKE_LLM_STAMP}); no language model called",
        },
        "override_reason_list": {
            "file": display_path(OVERRIDE_REASONS_FILE),
            "version": reasons.version,
            "sha256": sha256_of_file(OVERRIDE_REASONS_FILE),
            "codes": list(reasons.codes()),
        },
        "checkpoint": {**asdict(stats), "decisions_recorded": len(records)},
        "decision_log": {
            "file": display_path(args.log),
            "note": "gitignored; rewritten on every run",
            "records": len(records),
            "chain_problems": len(chain_problems),
        },
        "summary": summarize_feedback(records),
    }
    lineage_summary = summarize_lineage_checks(
        records, prompt_labels={"agent_memo_prompt": PROMPT_VERSION}, hash_data_files=not args.no_data_files
    )
    lineage_section = {
        "data_label": DATA_LABEL,
        "status": "measured on the SIMULATED decision log written by this run",
        "lineage_version": LINEAGE_VERSION,
        "interpretation": "Each record's lineage hashes were compared with the working tree right after the records "
        "were written, so matching hashes are expected. not_checkable items are files that are not present here "
        "(the Lending Club data needs a Kaggle key; some raw files were not kept); they are not assumed to match.",
        "checks": lineage_summary,
        "versions_named": lineage_overview(records),
    }
    datasets = ["lending_club", "fremtpl2"]
    update_results(args.output, "lineage", lineage_section, datasets=datasets)
    update_results(args.output, "feedback", feedback_section)

    summary = feedback_section["summary"]
    rate = summary["override_rate"]
    print(
        f"{len(records)} SIMULATED decisions on SYNTHETIC files: {summary['overrides']} overrides; override rate "
        f"{rate.get('value')} [{rate.get('ci_low')}, {rate.get('ci_high')}] (describes the simulation, not reviewers)"
    )
    print(f"Lineage: records by status {lineage_summary['records_by_status']}")
    print(f"Lineage: checks by status {lineage_summary['checks_by_status']}")
    print(f"Wrote sections 'lineage' and 'feedback' of {display_path(args.output)}; log {display_path(args.log)}")
    print(f"Took {time.perf_counter() - started_at:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
