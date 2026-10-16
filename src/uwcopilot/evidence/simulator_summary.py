"""Summaries of evidence-request simulator runs (innovation S5) with bootstrap confidence intervals.

Every interval is a percentile bootstrap over CASES: 1,000 resamples, seed 42 (agent.evaluation.N_RESAMPLES and
BOOTSTRAP_SEED). Per-case means use agent.evaluation.rate; the paired policy comparison uses
rag.metrics.paired_bootstrap_difference on the same cases (a minus b per case, then the mean); the share of
zero-value requests is a ratio of sums (zero-value requests / all requests) with its own case-level bootstrap.

decision_error_rate counts every case. A planted problem that no open item stands for (an embedded instruction the
scanner misses, for example) cannot be requested by any policy, so the error it causes is the same for every policy;
decision_error_rate_when_every_planted_problem_detected leaves those cases out so the policies' own stopping errors
can be read on their own.

decision_errors_by_kind splits a policy's decision errors: a planted problem no open item stands for
(undetected_planted_problem; no policy can request it), a voi run that stopped while open requests remained because
each had a VOI of exactly 0 (stopped_with_every_open_request_at_zero_voi: the myopia of a one-step score when two
problems each keep the recommendation), and the rest (other).

Only computed runs are summarized; runs that could not be computed are counted with their reasons.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES, rate
from uwcopilot.evidence.simulator_models import SimulationResult
from uwcopilot.rag.metrics import paired_bootstrap_difference

# The per-case metrics compared between two policies on the same cases.
PAIRED_METRICS: tuple[str, ...] = (
    "n_requests",
    "cost_weighted_requests",
    "n_zero_value_requests",
    "decision_error",
    "requests_to_stable",
)
DIGITS = 4


def computed(results: Iterable[SimulationResult]) -> list[SimulationResult]:
    """The runs that were computed."""
    return [result for result in results if result.computed]


def select(results: Iterable[SimulationResult], policy: str, variant: str) -> list[SimulationResult]:
    """The runs of one policy under one priors variant."""
    return [result for result in results if result.policy == policy and result.priors_variant == variant]


def ratio_ci(numerators: Sequence[float], denominators: Sequence[float]) -> dict[str, Any]:
    """sum(numerators) / sum(denominators) with a case-level percentile bootstrap (resamples with a zero
    denominator are dropped and counted)."""
    top = np.asarray(numerators, dtype=float)
    bottom = np.asarray(denominators, dtype=float)
    if top.size == 0 or bottom.sum() == 0:
        return {"value": None, "ci_low": None, "ci_high": None, "n_cases": int(top.size), "n_valid_resamples": 0}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, top.size, size=(N_RESAMPLES, top.size))
    sums_top = top[indices].sum(axis=1)
    sums_bottom = bottom[indices].sum(axis=1)
    valid = sums_bottom > 0
    ratios = sums_top[valid] / sums_bottom[valid]
    return {
        "value": round(float(top.sum() / bottom.sum()), DIGITS),
        "ci_low": round(float(np.quantile(ratios, 0.025)), DIGITS),
        "ci_high": round(float(np.quantile(ratios, 0.975)), DIGITS),
        "n_cases": int(top.size),
        "n_valid_resamples": int(valid.sum()),
    }


def metric_value(result: SimulationResult, metric: str) -> float | None:
    """One per-case metric as a number (decision_error as 0/1; requests_to_stable may be None)."""
    value = getattr(result, metric)
    if value is None:
        return None
    return float(value)


def decision_errors_by_kind(runs: Sequence[SimulationResult]) -> dict[str, int]:
    """The decision errors of computed runs, by kind (module docstring)."""
    kinds = {"undetected_planted_problem": 0, "stopped_with_every_open_request_at_zero_voi": 0, "other": 0}
    for result in runs:
        if not result.decision_error:
            continue
        if result.undetected_problem_ids:
            kinds["undetected_planted_problem"] += 1
        elif result.stop_reason == "no_nonzero_voi":
            kinds["stopped_with_every_open_request_at_zero_voi"] += 1
        else:
            kinds["other"] += 1
    return kinds


def policy_metrics(results: Sequence[SimulationResult]) -> dict[str, Any]:
    """Per-case means (with CIs) of one policy's runs."""
    runs = computed(results)
    stable = [float(result.requests_to_stable) for result in runs if result.requests_to_stable is not None]
    return {
        "n_cases": len(runs),
        "n_not_computed": len(results) - len(runs),
        "requests_per_case": rate([float(result.n_requests) for result in runs]),
        "cost_weighted_requests_per_case": rate([result.cost_weighted_requests for result in runs]),
        "zero_value_requests_per_case": rate([float(result.n_zero_value_requests) for result in runs]),
        "zero_value_share_of_requests": ratio_ci(
            [result.n_zero_value_requests for result in runs], [result.n_requests for result in runs]
        ),
        "decision_error_rate": rate([bool(result.decision_error) for result in runs]),
        "decision_error_rate_when_every_planted_problem_detected": rate(
            [bool(result.decision_error) for result in runs if not result.undetected_problem_ids]
        ),
        "requests_to_stable_when_reached": rate(stable),
        "decision_errors_by_kind": decision_errors_by_kind(runs),
        "stop_reasons": dict(sorted(Counter(result.stop_reason for result in runs).items())),
    }


def paired(first: Sequence[SimulationResult], second: Sequence[SimulationResult]) -> dict[str, Any]:
    """Per-case first-minus-second differences on the cases both computed (requests_to_stable: on the cases where
    both runs reached the full-information recommendation)."""
    by_case = {result.case_id: result for result in computed(second)}
    pairs = [(result, by_case[result.case_id]) for result in computed(first) if result.case_id in by_case]
    summary: dict[str, Any] = {"n_cases": len(pairs)}
    for metric in PAIRED_METRICS:
        values = [(metric_value(a, metric), metric_value(b, metric)) for a, b in pairs]
        kept = [(a, b) for a, b in values if a is not None and b is not None]
        if not kept:
            summary[metric] = {"mean_difference": None, "ci_low": None, "ci_high": None, "n": 0}
            continue
        difference = paired_bootstrap_difference(
            [a for a, _ in kept], [b for _, b in kept], N_RESAMPLES, BOOTSTRAP_SEED
        )
        summary[metric] = {
            "mean_first": round(float(np.mean([a for a, _ in kept])), DIGITS),
            "mean_second": round(float(np.mean([b for _, b in kept])), DIGITS),
            "mean_difference": round(difference["mean_difference"], DIGITS),
            "ci_low": round(difference["ci_low"], DIGITS),
            "ci_high": round(difference["ci_high"], DIGITS),
            "n": len(kept),
        }
    return summary


def oracle_metrics(results: Sequence[SimulationResult]) -> dict[str, Any]:
    """The oracle's runs: how many were computed and reachable, and their cost on reachable cases."""
    runs = computed(results)
    reached = [result for result in runs if result.stop_reason == "oracle_reached"]
    reasons = Counter(str(result.not_computed_reason) for result in results if not result.computed)
    return {
        "n_cases": len(results),
        "n_computed": len(runs),
        "not_computed_reasons": dict(sorted(reasons.items())),
        "n_unreachable": sum(1 for result in runs if result.stop_reason == "oracle_unreachable"),
        "n_reached": len(reached),
        "requests_per_case_when_reached": rate([float(result.n_requests) for result in reached]),
        "cost_weighted_requests_per_case_when_reached": rate([result.cost_weighted_requests for result in reached]),
    }


def case_counts(results: Sequence[SimulationResult]) -> dict[str, Any]:
    """Cases, open requests at the start, and planted problems no open item stands for (one row per case)."""
    first_runs: dict[str, SimulationResult] = {}
    for result in results:
        first_runs.setdefault(result.case_id, result)
    rows = list(first_runs.values())
    runs = computed(rows)
    undetected: Counter[str] = Counter()
    for result in runs:
        undetected.update(result.undetected_problem_types)
    return {
        "n_cases": len(rows),
        "n_computed": len(runs),
        "not_computed_reasons": dict(
            Counter(str(result.not_computed_reason) for result in rows if not result.computed)
        ),
        "n_clean_cases": sum(1 for result in rows if not result.problem_types),
        "mean_planted_problems_per_case": (
            round(sum(len(result.problem_types) for result in rows) / len(rows), DIGITS) if rows else None
        ),
        "open_requests_at_start": dict(sorted(Counter(result.n_initial_open_requests for result in runs).items())),
        "cases_with_undetected_planted_problems": sum(1 for result in runs if result.undetected_problem_ids),
        "undetected_planted_problems_by_type": dict(sorted(undetected.items())),
        "initial_equals_full_information": sum(
            1 for result in runs if result.initial_recommendation == result.full_information_recommendation
        ),
    }


def summarize(results: Sequence[SimulationResult], policies: Sequence[str], variants: Sequence[str]) -> dict[str, Any]:
    """Metrics per priors variant and policy, the paired comparisons against the checklist, and the oracle."""
    by_variant: dict[str, Any] = {}
    voi_vs_checklist: dict[str, Any] = {}
    lookahead_vs_checklist: dict[str, Any] = {}
    for variant in variants:
        by_variant[variant] = {
            policy: policy_metrics(select(results, policy, variant)) for policy in policies if policy != "oracle"
        }
        checklist = select(results, "checklist", variant)
        voi_vs_checklist[variant] = paired(select(results, "voi", variant), checklist)
        lookahead_vs_checklist[variant] = paired(select(results, "voi_lookahead", variant), checklist)
    return {
        "cases": case_counts(results),
        "by_priors_variant": by_variant,
        "paired_voi_minus_checklist": voi_vs_checklist,
        "paired_voi_lookahead_minus_checklist": lookahead_vs_checklist,
        "oracle_documented_priors": oracle_metrics(select(results, "oracle", "documented")),
    }
