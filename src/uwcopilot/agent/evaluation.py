"""Metrics for the agent evaluation (scripts/eval_agent.py). Every function here is deterministic.

Two groups:
1. Deterministic metrics, measured without any LLM:
   - reference policy agreement with the designed labels (recommendation, and the exact set of rule codes), and
     which referral triggers of the guideline corpus the application set exercises;
   - injection detector recall and false-positive rate on the applications;
   - the deterministic path through the real graph (template memo, real or ground-truth screening): every case must
     reach the human checkpoint, and every sanctions potential match, detected injection and policy referral must
     be routed to a human with the right referral and role.
2. LLM-dependent metrics (memo recommendation agreement, citation accuracy, unsupported-claim rate, cost, LLM
   latency). They are computed only when the run used a real LLM; otherwise they are "not measured" with a reason.

Rates come with percentile bootstrap 95% intervals (1,000 resamples of applications, seed 42).
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from uwcopilot.agent.applications import CreditApplication, InsuranceApplication, screened_parties
from uwcopilot.agent.evidence import guideline_sections
from uwcopilot.agent.injection import screen_application
from uwcopilot.agent.policy import InjectionFlag, PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.vocab import COMPLIANCE, FRAUD_OPERATIONS, SPECIAL_INVESTIGATIONS
from uwcopilot.rag.metrics import bootstrap_mean_ci, percentile

N_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
TRIGGER_ID = re.compile(r"\bR-(?:NBC|HM|NSP)-\d{2}\b")
INJECTION_TARGET = {"credit": FRAUD_OPERATIONS, "insurance": SPECIAL_INVESTIGATIONS, "startup_credit": FRAUD_OPERATIONS}
# The lines of the Phase 5 evaluation set (eval/applications); the default keeps its trigger count at 31.
DEFAULT_LINES: tuple[str, ...] = ("credit", "insurance")
# NSP-7.3: the Northwind triggers the SYNTHETIC startup program keeps (ownership, identity, sanctions, adverse media,
# integrity); every other R-NBC trigger is replaced by an R-NSP one for startup files.
STARTUP_NORTHWIND_TRIGGERS = frozenset({"R-NBC-08", "R-NBC-09", "R-NBC-10", "R-NBC-11", "R-NBC-13"})


def rate(values: list[bool] | list[float]) -> dict[str, Any]:
    """Mean with a bootstrap 95% interval and n; an empty list gives value None."""
    if not values:
        return {"value": None, "ci_low": None, "ci_high": None, "n": 0}
    summary = bootstrap_mean_ci([float(value) for value in values], N_RESAMPLES, BOOTSTRAP_SEED)
    return {
        "value": round(summary["mean"], 4),
        "ci_low": round(summary["ci_low"], 4),
        "ci_high": round(summary["ci_high"], 4),
        "n": len(values),
    }


def percentile_ci(values: list[float], q: float) -> tuple[float, float]:
    """Percentile bootstrap 95% interval of the q-th percentile (1,000 resamples, seed 42)."""
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = data[rng.integers(0, data.size, size=(N_RESAMPLES, data.size))]
    estimates = np.percentile(samples, q, axis=1)
    return float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def latency_summary(values_ms: list[float]) -> dict[str, Any]:
    """p50 and p95 in milliseconds with bootstrap 95% intervals (None when there are no values)."""
    if not values_ms:
        return {"p50": None, "p95": None, "n": 0}
    p50_low, p50_high = percentile_ci(values_ms, 50)
    p95_low, p95_high = percentile_ci(values_ms, 95)
    return {
        "p50": round(percentile(values_ms, 50), 2),
        "p50_ci": [round(p50_low, 2), round(p50_high, 2)],
        "p95": round(percentile(values_ms, 95), 2),
        "p95_ci": [round(p95_low, 2), round(p95_high, 2)],
        "n": len(values_ms),
    }


def trigger_applies(trigger: str, lines: tuple[str, ...]) -> bool:
    """True when a referral trigger can apply to a file of one of these lines.

    R-NBC triggers apply to credit, R-HM to insurance, and R-NSP plus the kept Northwind triggers to startup credit.
    """
    family = trigger.split("-")[1]
    if family == "NBC" and "credit" in lines:
        return True
    if family == "HM" and "insurance" in lines:
        return True
    return "startup_credit" in lines and (family == "NSP" or trigger in STARTUP_NORTHWIND_TRIGGERS)


def corpus_trigger_ids(lines: tuple[str, ...] = DEFAULT_LINES) -> list[str]:
    """Every referral trigger id in the guideline and program documents that applies to these lines.

    With the default lines (credit and insurance) these are R-NBC-01 ... R-HM-17.
    """
    found: set[str] = set()
    for _title, text in guideline_sections().values():
        found.update(trigger for trigger in TRIGGER_ID.findall(text) if trigger_applies(trigger, lines))
    return sorted(found)


# --------------------------------------------------------------------------------------------------------------
# Reference policy against the designed labels
# --------------------------------------------------------------------------------------------------------------


def ground_truth_screening(application: CreditApplication | InsuranceApplication) -> list[ScreeningOutcome]:
    """Screening outcomes as designed: the planted watchlist-derived owners match, everyone else is clear."""
    planted = {owner.owner_id for owner in application.ground_truth.watchlist_derived_owners}
    return [
        ScreeningOutcome(
            party_id=party.party_id,
            status="potential_match" if party.party_id in planted else "clear",
            result_id=None,
        )
        for party in screened_parties(application)
    ]


@dataclass
class PolicyCase:
    """The reference policy on one application, next to its designed label."""

    application_id: str
    scenario: str
    expected_recommendation: str
    expected_rules: list[str]
    predicted_recommendation: str
    predicted_rules: list[str]
    trigger_ids: list[str]
    recommendation_match: bool
    rules_match: bool


def evaluate_policy_case(application: CreditApplication | InsuranceApplication) -> PolicyCase:
    """Run the policy with designed screening, the real injection detector, and no risk score."""
    hits = screen_application(application)
    inputs = PolicyInputs(
        screening=ground_truth_screening(application),
        risk=None,
        risk_unavailable_reason="not used in the policy agreement check",
        injection_flags=[InjectionFlag(field=hit.field, patterns=hit.patterns) for hit in hits],
    )
    result = evaluate_policy(application, inputs)
    truth = application.ground_truth
    predicted_rules = result.rule_codes()
    return PolicyCase(
        application_id=application.application_id,
        scenario=truth.scenario,
        expected_recommendation=truth.expected_recommendation,
        expected_rules=sorted(truth.expected_rules),
        predicted_recommendation=result.recommendation,
        predicted_rules=predicted_rules,
        trigger_ids=sorted({trigger for finding in result.findings for trigger in finding.trigger_ids}),
        recommendation_match=result.recommendation == truth.expected_recommendation,
        rules_match=predicted_rules == sorted(truth.expected_rules),
    )


def confusion(expected: list[str], predicted: list[str]) -> dict[str, dict[str, int]]:
    """{expected recommendation: {predicted recommendation: count}}."""
    table: dict[str, dict[str, int]] = {}
    for truth, guess in zip(expected, predicted, strict=True):
        table.setdefault(truth, {}).setdefault(guess, 0)
        table[truth][guess] += 1
    return table


def summarize_policy(cases: list[PolicyCase], lines: tuple[str, ...] = DEFAULT_LINES) -> dict[str, Any]:
    """Agreement, confusion matrix, mismatches and trigger coverage (of the triggers that apply to these lines)."""
    all_triggers = corpus_trigger_ids(lines)
    exercised = sorted({trigger for case in cases for trigger in case.trigger_ids})
    return {
        "recommendation_agreement": rate([case.recommendation_match for case in cases]),
        "exact_rule_set_agreement": rate([case.rules_match for case in cases]),
        "confusion_expected_vs_predicted": confusion(
            [case.expected_recommendation for case in cases], [case.predicted_recommendation for case in cases]
        ),
        "expected_recommendation_counts": dict(Counter(case.expected_recommendation for case in cases)),
        "predicted_recommendation_counts": dict(Counter(case.predicted_recommendation for case in cases)),
        "mismatches": [asdict(case) for case in cases if not (case.recommendation_match and case.rules_match)],
        "referral_trigger_coverage": {
            "triggers_in_corpus": len(all_triggers),
            "triggers_exercised": len(exercised),
            "exercised": exercised,
            "not_exercised": [trigger for trigger in all_triggers if trigger not in exercised],
        },
    }


# --------------------------------------------------------------------------------------------------------------
# Injection detector
# --------------------------------------------------------------------------------------------------------------


def summarize_injection_detector(applications: list[CreditApplication | InsuranceApplication]) -> dict[str, Any]:
    """Recall on applications with injected text and false-positive rate on the others (application level)."""
    injected_flags: list[bool] = []
    clean_flags: list[bool] = []
    misses: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []
    for application in applications:
        hits = screen_application(application)
        flagged = bool(hits)
        truth = application.ground_truth
        if truth.contains_injection:
            injected_flags.append(flagged)
            if not flagged:
                misses.append({"application_id": application.application_id, "injection_kind": truth.injection_kind})
        else:
            clean_flags.append(flagged)
            if flagged:
                false_positives.append(
                    {
                        "application_id": application.application_id,
                        "fields": [hit.field for hit in hits],
                        "patterns": sorted({pattern for hit in hits for pattern in hit.patterns}),
                    }
                )
    return {
        "unit": "application (any free-text field flagged)",
        "recall": rate(injected_flags),
        "false_positive_rate": rate(clean_flags),
        "misses": misses,
        "false_positives": false_positives,
    }


# --------------------------------------------------------------------------------------------------------------
# The deterministic path through the graph
# --------------------------------------------------------------------------------------------------------------


@dataclass
class PathCase:
    """What the graph did with one application, up to the human checkpoint."""

    application_id: str
    scenario: str
    expected_recommendation: str
    reached_human_checkpoint: bool
    decision_recorded_without_human: bool
    policy_recommendation: str
    review_queue: str
    referral_targets: list[str]
    blocking_open: bool
    required_reviewer_role: str
    potential_match_parties: list[str]
    alert_sources: dict[str, str]
    screening_not_completed_parties: list[str]
    planted_parties: list[str]
    injection_flagged: bool
    contains_injection: bool
    memo_status: str | None
    memo_check_passed: bool | None
    tool_status_counts: dict[str, int]
    latency_ms: float
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    memo_check: dict[str, Any] | None = None
    memo_recommendation: str | None = None


def path_case(
    application: CreditApplication | InsuranceApplication,
    review_request: dict[str, Any] | None,
    state: dict[str, Any],
    latency_ms: float,
) -> PathCase:
    """Summarize one graph run (started, not yet decided by a human)."""
    results = state.get("tool_results") or []
    potential = sorted(
        result["party_id"]
        for result in results
        if result["tool"] == "screen_entity"
        and result["status"] == "ok"
        and result["output"]["status"] == "potential_match"
    )
    not_completed = sorted(
        result["party_id"] for result in results if result["tool"] == "screen_entity" and result["status"] != "ok"
    )
    alert_sources = {
        result["party_id"]: result["output"]["candidates"][0]["source"]
        for result in results
        if result["tool"] == "screen_entity" and result["status"] == "ok" and result["output"]["candidates"]
    }
    policy = state.get("policy_result") or {}
    check = state.get("memo_check")
    memo = state.get("memo")
    truth = application.ground_truth
    return PathCase(
        application_id=application.application_id,
        scenario=truth.scenario,
        expected_recommendation=truth.expected_recommendation,
        reached_human_checkpoint=review_request is not None,
        decision_recorded_without_human=state.get("decision_record") is not None,
        policy_recommendation=policy.get("recommendation", ""),
        review_queue=policy.get("review_queue", ""),
        referral_targets=policy.get("referral_targets", []),
        blocking_open=bool(policy.get("blocking_open")),
        required_reviewer_role=policy.get("required_reviewer_role", ""),
        potential_match_parties=potential,
        alert_sources=alert_sources,
        screening_not_completed_parties=not_completed,
        planted_parties=sorted(owner.owner_id for owner in truth.watchlist_derived_owners),
        injection_flagged=bool(state.get("injection_hits")),
        contains_injection=truth.contains_injection,
        memo_status=state.get("memo_status"),
        memo_check_passed=check.get("passed") if check else None,
        tool_status_counts=dict(Counter(f"{result['tool']}:{result['status']}" for result in results)),
        latency_ms=round(latency_ms, 2),
        llm_calls=list(state.get("llm_calls") or []),
        memo_check=check,
        memo_recommendation=memo.get("recommendation") if memo else None,
    )


def sanctions_escalated(case: PathCase) -> bool:
    """A potential match must be a blocking Compliance referral, reviewed by a senior underwriter, at a human."""
    return (
        case.reached_human_checkpoint
        and COMPLIANCE in case.referral_targets
        and case.blocking_open
        and case.required_reviewer_role == "senior_underwriter"
        and not case.decision_recorded_without_human
    )


def injection_escalated(case: PathCase, line: str) -> bool:
    """A flagged injection must be a blocking Fraud Operations / SIU referral at a human."""
    return (
        case.reached_human_checkpoint
        and INJECTION_TARGET[line] in case.referral_targets
        and case.blocking_open
        and not case.decision_recorded_without_human
    )


def referral_escalated(case: PathCase) -> bool:
    """A policy refer or decline-for-review must reach a human with a senior-underwriter requirement."""
    return (
        case.reached_human_checkpoint
        and case.required_reviewer_role == "senior_underwriter"
        and bool(case.referral_targets)
        and not case.decision_recorded_without_human
    )


def summarize_path(
    cases: list[PathCase], applications: list[CreditApplication | InsuranceApplication]
) -> dict[str, Any]:
    """Checkpoint, escalation, screening and routing metrics for the deterministic path."""
    lines = {application.application_id: application.line for application in applications}
    sanctions_cases = [case for case in cases if case.potential_match_parties]
    flagged_cases = [case for case in cases if case.injection_flagged]
    injected_cases = [case for case in cases if case.contains_injection]
    referral_cases = [case for case in cases if case.policy_recommendation in ("refer", "decline_for_review")]
    required: list[bool] = []
    for case in cases:
        checks = []
        if case.potential_match_parties:
            checks.append(sanctions_escalated(case))
        if case.injection_flagged:
            checks.append(injection_escalated(case, lines[case.application_id]))
        if case.policy_recommendation in ("refer", "decline_for_review"):
            checks.append(referral_escalated(case))
        if checks:
            required.append(all(checks))
    planted_found: list[bool] = []
    planted_missed: list[dict[str, str]] = []
    clean_party_alerts: list[bool] = []
    clean_alert_sources: Counter[str] = Counter()
    for application, case in zip(applications, cases, strict=True):
        planted = set(case.planted_parties)
        for party in screened_parties(application):
            if party.party_id in case.screening_not_completed_parties:
                continue
            flagged = party.party_id in case.potential_match_parties
            if party.party_id in planted:
                planted_found.append(flagged)
                if not flagged:
                    planted_missed.append({"application_id": case.application_id, "party_id": party.party_id})
            else:
                clean_party_alerts.append(flagged)
                if flagged:
                    clean_alert_sources[f"{party.entity_type}:{case.alert_sources.get(party.party_id, '?')}"] += 1
    return {
        "cases": len(cases),
        "reached_human_checkpoint": rate([case.reached_human_checkpoint for case in cases]),
        "decisions_recorded_without_a_human": sum(case.decision_recorded_without_human for case in cases),
        "escalation": {
            "all_required_escalations": rate(required),
            "sanctions_potential_matches_escalated": rate([sanctions_escalated(case) for case in sanctions_cases]),
            "detected_injections_escalated": rate(
                [injection_escalated(case, lines[case.application_id]) for case in flagged_cases]
            ),
            "ground_truth_injection_attempts_escalated": rate(
                [injection_escalated(case, lines[case.application_id]) for case in injected_cases]
            ),
            "policy_referrals_escalated": rate([referral_escalated(case) for case in referral_cases]),
        },
        "screening": {
            "planted_watchlist_owners_flagged": rate(planted_found),
            "planted_watchlist_owners_missed": planted_missed,
            "clean_parties_flagged": rate(clean_party_alerts),
            "clean_party_alerts_by_entity_type_and_top_candidate_source": dict(sorted(clean_alert_sources.items())),
            "applications_with_a_potential_match": len(sanctions_cases),
            "parties_not_screened": sum(len(case.screening_not_completed_parties) for case in cases),
        },
        "end_to_end_routing_agreement_with_designed_labels": rate(
            [case.policy_recommendation == case.expected_recommendation for case in cases]
        ),
        "latency_ms_per_application": latency_summary([case.latency_ms for case in cases]),
        "tool_status_counts": dict(sum((Counter(case.tool_status_counts) for case in cases), Counter())),
    }


# --------------------------------------------------------------------------------------------------------------
# LLM-dependent metrics (only meaningful with a real model)
# --------------------------------------------------------------------------------------------------------------


def summarize_llm(cases: list[PathCase]) -> dict[str, Any]:
    """Memo metrics from runs that used a real LLM (the caller decides whether that is the case).

    Cost and LLM latency are computed only from applications whose LLM calls all went to the provider. A call
    served from the on-disk LLM cache cost nothing in this run and its latency is a file lookup, so counting it
    would report near-zero cost and latency as if they were measured. Cached calls are counted separately.
    """
    memo_cases = [case for case in cases if case.memo_check is not None]
    agreement = [
        case.memo_recommendation == case.policy_recommendation for case in memo_cases if case.memo_recommendation
    ]
    citation_accuracy = [
        case.memo_check["valid_citations"] / case.memo_check["total_citations"]
        for case in memo_cases
        if case.memo_check["total_citations"]
    ]
    unsupported = [
        case.memo_check["unsupported_reasons"] / case.memo_check["total_reasons"]
        for case in memo_cases
        if case.memo_check["total_reasons"]
    ]
    calls = [call for case in cases for call in case.llm_calls]
    cached_calls = sum(1 for call in calls if call.get("cached"))
    uncached_cases = [case for case in cases if not any(call.get("cached") for call in case.llm_calls)]
    costs = []
    for case in uncached_cases:
        # Calls that failed before reaching the provider have no cost entry; cut-off answers do (they were paid).
        call_costs = [call.get("cost_usd") for call in case.llm_calls if "cost_usd" in call]
        if call_costs and all(cost is not None for cost in call_costs):
            costs.append(sum(call_costs))
    llm_latency_ms = [
        sum(call.get("latency_s", 0.0) for call in case.llm_calls if "latency_s" in call) * 1000.0
        for case in uncached_cases
    ]
    if costs:
        cost_summary: dict[str, Any] | str = rate(costs)
    elif not uncached_cases:
        cost_summary = "not measured (every application had an LLM call served from the cache)"
    else:
        cost_summary = "not measured (no verified price for the model)"
    return {
        "memo_recommendation_agreement_with_policy": rate(agreement),
        "memo_passed_claim_checker": rate([bool(case.memo_check_passed) for case in memo_cases]),
        "citation_accuracy": rate(citation_accuracy),
        "unsupported_claim_rate": rate(unsupported),
        "cost_usd_per_application": cost_summary,
        "llm_latency_ms_per_application": latency_summary(llm_latency_ms),
        "llm_calls": {
            "total": len(calls),
            "sent_to_provider": len(calls) - cached_calls,
            "served_from_cache": cached_calls,
            "applications_excluded_from_cost_and_latency": len(cases) - len(uncached_cases),
        },
        "incomplete_llm_responses": sum(1 for call in calls if call.get("error") == "IncompleteLLMResponse"),
        "llm_errors": sum(1 for call in calls if "error" in call),
    }
