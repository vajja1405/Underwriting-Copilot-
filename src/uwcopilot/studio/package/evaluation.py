"""Measurements of the lender handoff for the startup line (scripts/eval_studio_handoff.py).

Two sets, both SYNTHETIC:
1. the 35 startup applications of eval/startup_applications (designed labels): the existing agent graph runs on each
   up to its human checkpoint (template memo); measured: agreement of the graph's reference recommendation and
   exact rule set with the designed labels, checkpoint reached, decisions recorded without a human (must be 0),
   memo checks passed and unsupported numbers, and the startup open items with the top-ranked evidence request;
2. the 36 golden founder scenarios (S4 scenarios + package/golden.py): package built, PDF rendered and audited
   (starts with %PDF, page count, every page's labels, untraced numbers in the package text and in the PDF text),
   then handoff(): checkpoint reached, decisions without a human, memo check, unsupported numbers, the reference
   recommendation and the ranked evidence requests. These scenarios have no designed label, so agreement with
   labels is "not applicable" for them.
Rates come with percentile bootstrap 95% intervals over cases (1,000 resamples, seed 42: agent.evaluation.rate) and
a Wilson 95% interval (proportion()).
"""

import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from langgraph.checkpoint.memory import InMemorySaver

from uwcopilot.agent.applications import StartupCreditApplication
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.evaluation import rate
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps, injection_hits_of, policy_inputs_from, tool_results_of
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.runner import compile_agent, start_run
from uwcopilot.common.stats import rate_summary
from uwcopilot.evidence.open_items import detect_open_items
from uwcopilot.evidence.startup_requests import rank_startup_requests
from uwcopilot.evidence.state import evaluate, from_application
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.studio.narrative.golden import GoldenScenario
from uwcopilot.studio.package.application import to_application
from uwcopilot.studio.package.audit import audit_pdf, pages_missing_labels, pdf_page_count
from uwcopilot.studio.package.build import build_package
from uwcopilot.studio.package.golden import VARIANTS, project_from_golden, scenario_index
from uwcopilot.studio.package.handoff import handoff, unsupported_count
from uwcopilot.studio.package.pdf import render_pdf
from uwcopilot.tools.registry import ToolHandler

NOT_APPLICABLE_LABELS = "not applicable: the golden founder scenarios carry no designed label"
PDF_MAGIC = b"%PDF"


def application_row(
    application: StartupCreditApplication, handlers: Mapping[str, ToolHandler], log_dir: Path
) -> dict[str, Any]:
    """One eval application through the graph to the checkpoint, with its startup open items (module docstring)."""
    deps = AgentDeps(
        llm=FakeLLM(template_memo_responder),
        tool_handlers=dict(handlers),
        decision_log=DecisionLog(log_dir / "decision_records.jsonl"),
    )
    agent = compile_agent(deps, InMemorySaver())
    started = time.perf_counter()
    outcome = start_run(agent, application.model_dump(mode="json"), f"eval-{application.application_id}")
    latency_ms = (time.perf_counter() - started) * 1000.0
    state = outcome.state
    policy = PolicyResult.model_validate(state["policy_result"])
    truth = application.ground_truth
    inputs = policy_inputs_from(tool_results_of(state), injection_hits_of(state))
    case = from_application(
        application.model_dump(mode="json"), injection_flags=inputs.injection_flags, screening=inputs.screening
    )
    current = evaluate(case)
    items = detect_open_items(case, policy_result=current)
    ranked = rank_startup_requests(case, items, current)
    memo_check = state.get("memo_check") or {}
    return {
        "application_id": application.application_id,
        "scenario": truth.scenario if truth else None,
        "expected_recommendation": truth.expected_recommendation if truth else None,
        "expected_rules": sorted(truth.expected_rules) if truth else None,
        "reference_recommendation": policy.recommendation,
        "rules": policy.rule_codes(),
        "recommendation_match": truth is not None and policy.recommendation == truth.expected_recommendation,
        "rules_match": truth is not None and policy.rule_codes() == sorted(truth.expected_rules),
        "reached_checkpoint": outcome.status == "awaiting_review" and outcome.review_request is not None,
        "decision_recorded_without_human": state.get("decision_record") is not None,
        "memo_status": state.get("memo_status"),
        "memo_check_passed": bool(memo_check.get("passed")),
        "unsupported_numbers": unsupported_count(memo_check),
        "review_queue": policy.review_queue,
        "required_reviewer_role": policy.required_reviewer_role,
        "open_items": [item.item_id for item in items],
        "top_request": ranked[0].request_id if ranked else None,
        "latency_ms": round(latency_ms, 2),
    }


def golden_row(scenario: GoldenScenario, handlers: Mapping[str, ToolHandler], log_dir: Path) -> dict[str, Any]:
    """One golden founder scenario end to end: package, PDF, audits, application and handoff (module docstring)."""
    started = time.perf_counter()
    package = build_package(project_from_golden(scenario))
    built = time.perf_counter()
    pdf = render_pdf(package)
    rendered = time.perf_counter()
    pdf_untraced = audit_pdf(pdf, package.ledger)
    missing = pages_missing_labels(pdf, package)
    application = to_application(package)
    result = handoff(package, tool_handlers=handlers, log_dir=log_dir)
    finished = time.perf_counter()
    return {
        "scenario_id": scenario.scenario_id,
        "metro_id": scenario.metro_id,
        "profile_id": scenario.profile_id,
        "metro_mode": scenario.metro_mode,
        "document_variant": scenario_index(scenario) % VARIANTS,
        "package_id": package.package_id,
        "claims": len(package.ledger.claims),
        "status_counts": package.ledger.status_counts(),
        "package_numbers": package.audit.numbers,
        "package_untraced": list(package.audit.untraced),
        "pdf_starts_with_pdf": pdf.startswith(PDF_MAGIC),
        "pdf_pages": pdf_page_count(pdf),
        "pdf_bytes": len(pdf),
        "pdf_untraced": pdf_untraced,
        "pages_missing_labels": {key: value for key, value in missing.items() if value},
        "application_id": application.application_id,
        "reached_checkpoint": result.reached_checkpoint,
        "decision_recorded_without_human": result.decision_recorded_without_human,
        "memo_status": result.memo_status,
        "memo_check_passed": bool(result.memo_check_passed),
        "unsupported_numbers": result.unsupported_numbers,
        "reference_recommendation": result.reference_recommendation,
        "evidence_engine_agrees_with_agent": result.evidence_engine_recommendation == result.reference_recommendation,
        "open_items": [item["item_id"] for item in result.review_request.open_items],
        "evidence_requests": [request.request_id for request in result.review_request.evidence_requests],
        "seconds": {
            "build": round(built - started, 3),
            "render_pdf": round(rendered - built, 3),
            "audit_and_handoff": round(finished - rendered, 3),
            "total": round(finished - started, 3),
        },
    }


def proportion(flags: list[bool]) -> dict[str, Any]:
    """A rate of yes/no outcomes: value with its bootstrap 95% interval (agent.evaluation.rate), the count of yes
    (k) and the Wilson 95% interval, which stays informative when every case agrees (a bootstrap of all-equal values
    is a single point)."""
    block = rate(flags)
    summary = rate_summary(sum(bool(flag) for flag in flags), len(flags))
    return {**block, "k": summary["k"], "wilson_ci95": summary["wilson_ci95"]}


def count_summary(values: list[float]) -> dict[str, Any]:
    """min / median / max of a list (None when empty)."""
    if not values:
        return {"min": None, "median": None, "max": None, "n": 0}
    return {"min": min(values), "median": float(np.median(values)), "max": max(values), "n": len(values)}


def summarize_applications(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agreement with the designed labels, checkpoint and decision counts for the eval applications."""
    labelled = [row for row in rows if row["expected_recommendation"] is not None]
    return {
        "cases": len(rows),
        "labelled_cases": len(labelled),
        "recommendation_agreement_with_labels": proportion([row["recommendation_match"] for row in labelled]),
        "exact_rule_set_agreement_with_labels": proportion([row["rules_match"] for row in labelled]),
        "reached_human_checkpoint": proportion([row["reached_checkpoint"] for row in rows]),
        "decisions_recorded_without_a_human": sum(row["decision_recorded_without_human"] for row in rows),
        "decisions_recorded_without_a_human_rate": proportion([row["decision_recorded_without_human"] for row in rows]),
        "memo_check_passed": proportion([row["memo_check_passed"] for row in rows]),
        "unsupported_numbers_in_memos": sum(row["unsupported_numbers"] for row in rows),
        "mismatches": [
            {
                key: row[key]
                for key in ("application_id", "expected_recommendation", "reference_recommendation", "rules")
            }
            for row in labelled
            if not (row["recommendation_match"] and row["rules_match"])
        ],
        "reference_recommendation_counts": dict(Counter(row["reference_recommendation"] for row in rows)),
        "startup_open_item_types": dict(Counter(item.split("-")[1] for row in rows for item in row["open_items"])),
        "top_request_counts": dict(Counter(row["top_request"] for row in rows if row["top_request"])),
        "latency_ms_median": float(np.median([row["latency_ms"] for row in rows])) if rows else None,
    }


def summarize_golden(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Package, PDF, audit and handoff results of the golden founder scenarios."""
    return {
        "cases": len(rows),
        "metro_modes": dict(Counter(row["metro_mode"] for row in rows)),
        "agreement_with_labels": NOT_APPLICABLE_LABELS,
        "pdf_starts_with_pdf": proportion([row["pdf_starts_with_pdf"] for row in rows]),
        "pdf_pages": count_summary([row["pdf_pages"] for row in rows]),
        "every_page_labelled": proportion([not row["pages_missing_labels"] for row in rows]),
        "package_text_audit_empty": proportion([not row["package_untraced"] for row in rows]),
        "pdf_text_audit_empty": proportion([not row["pdf_untraced"] for row in rows]),
        "package_numbers_read_back": int(sum(row["package_numbers"] for row in rows)),
        "untraced_numbers_package_text": int(sum(len(row["package_untraced"]) for row in rows)),
        "untraced_numbers_pdf_text": int(sum(len(row["pdf_untraced"]) for row in rows)),
        "reached_human_checkpoint": proportion([row["reached_checkpoint"] for row in rows]),
        "decisions_recorded_without_a_human": sum(row["decision_recorded_without_human"] for row in rows),
        "decisions_recorded_without_a_human_rate": proportion([row["decision_recorded_without_human"] for row in rows]),
        "memo_check_passed": proportion([row["memo_check_passed"] for row in rows]),
        "unsupported_numbers_in_memos": sum(row["unsupported_numbers"] for row in rows),
        "evidence_engine_agrees_with_agent": proportion([row["evidence_engine_agrees_with_agent"] for row in rows]),
        "reference_recommendation_counts": dict(Counter(row["reference_recommendation"] for row in rows)),
        "evidence_requests_per_package": count_summary([len(row["evidence_requests"]) for row in rows]),
        "top_request_counts": dict(Counter(row["evidence_requests"][0] for row in rows if row["evidence_requests"])),
        "claims_per_package": count_summary([row["claims"] for row in rows]),
        "seconds_per_scenario": count_summary([row["seconds"]["total"] for row in rows]),
    }


def summarize_all(application_rows: list[dict[str, Any]], golden_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Checkpoint and no-decision results over both sets together."""
    rows = application_rows + golden_rows
    return {
        "cases": len(rows),
        "reached_human_checkpoint": proportion([row["reached_checkpoint"] for row in rows]),
        "decisions_recorded_without_a_human": sum(row["decision_recorded_without_human"] for row in rows),
        "decisions_recorded_without_a_human_rate": proportion([row["decision_recorded_without_human"] for row in rows]),
        "unsupported_numbers_in_memos": sum(row["unsupported_numbers"] for row in rows),
    }
