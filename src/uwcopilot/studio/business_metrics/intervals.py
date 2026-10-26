"""Bootstrap intervals for the S12 metrics (docs/studio/s12_baseline.md, section 4).

The resampling unit is the scenario: the figures of one package are not independent, so a resample draws whole
scenarios with replacement and recomputes the pooled share (sum of numerators / sum of denominators). Percentile
method, 1,000 resamples, seed 42 (the repository's constants in agent.evaluation). A paired difference recomputes
both shares on the same resampled scenarios. Times use the existing percentile bootstrap of agent.evaluation.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np

from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES, percentile_ci
from uwcopilot.rag.metrics import percentile

CONFIDENCE = 0.95
DECIMALS = 4


def resample_indices(n: int, n_resamples: int = N_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> np.ndarray:
    """(n_resamples, n) scenario indices drawn with replacement."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(n_resamples, n))


def pooled(numerators: np.ndarray, denominators: np.ndarray) -> np.ndarray:
    """Pooled share of each resample row (NaN where the row has no figures)."""
    totals = denominators.sum(axis=-1).astype(float)
    hits = numerators.sum(axis=-1).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(totals > 0, hits / totals, np.nan)


def interval(samples: np.ndarray) -> tuple[float, float]:
    """The percentile 95% interval of the finite samples."""
    finite = samples[np.isfinite(samples)]
    alpha = (1.0 - CONFIDENCE) / 2.0
    return float(np.quantile(finite, alpha)), float(np.quantile(finite, 1.0 - alpha))


def check_counts(numerators: Sequence[int], denominators: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    """The counts as arrays; each numerator lies between 0 and its denominator."""
    hits = np.asarray(numerators, dtype=int)
    totals = np.asarray(denominators, dtype=int)
    if hits.shape != totals.shape or hits.ndim != 1:
        raise ValueError("numerators and denominators must be two lists of the same length")
    if np.any(hits < 0) or np.any(hits > totals):
        raise ValueError("every numerator must lie between 0 and its denominator")
    return hits, totals


def pooled_share(numerators: Sequence[int], denominators: Sequence[int]) -> dict[str, Any]:
    """Pooled share k / n over scenarios with its scenario-bootstrap 95% interval (None values when n is 0)."""
    hits, totals = check_counts(numerators, denominators)
    k, n = int(hits.sum()), int(totals.sum())
    block: dict[str, Any] = {"value": None, "ci_low": None, "ci_high": None, "k": k, "n": n, "scenarios": hits.size}
    if n == 0:
        return block
    indices = resample_indices(hits.size)
    low, high = interval(pooled(hits[indices], totals[indices]))
    block.update(value=round(k / n, DECIMALS), ci_low=round(low, DECIMALS), ci_high=round(high, DECIMALS))
    return block


def paired_difference(
    numerators_a: Sequence[int],
    denominators_a: Sequence[int],
    numerators_b: Sequence[int],
    denominators_b: Sequence[int],
) -> dict[str, Any]:
    """Pooled share of a minus pooled share of b, with a paired scenario-bootstrap 95% interval."""
    hits_a, totals_a = check_counts(numerators_a, denominators_a)
    hits_b, totals_b = check_counts(numerators_b, denominators_b)
    if hits_a.size != hits_b.size or hits_a.size == 0:
        raise ValueError("a paired difference needs the same, non-empty set of scenarios for both plans")
    if totals_a.sum() == 0 or totals_b.sum() == 0:
        return {"value": None, "ci_low": None, "ci_high": None, "scenarios": hits_a.size}
    value = hits_a.sum() / totals_a.sum() - hits_b.sum() / totals_b.sum()
    indices = resample_indices(hits_a.size)
    samples = pooled(hits_a[indices], totals_a[indices]) - pooled(hits_b[indices], totals_b[indices])
    low, high = interval(samples)
    return {
        "value": round(float(value), DECIMALS),
        "ci_low": round(low, DECIMALS),
        "ci_high": round(high, DECIMALS),
        "scenarios": hits_a.size,
    }


def spread(values: Sequence[float]) -> dict[str, Any]:
    """min / median / max of per-scenario values (None when empty)."""
    if not values:
        return {"min": None, "median": None, "max": None, "n": 0}
    data = [float(value) for value in values]
    return {
        "min": round(min(data), DECIMALS),
        "median": round(float(np.median(data)), DECIMALS),
        "max": round(max(data), DECIMALS),
        "n": len(data),
    }


def seconds_summary(values: Sequence[float]) -> dict[str, Any]:
    """Median and 95th percentile of per-scenario seconds with percentile bootstrap 95% intervals, min, max, n."""
    if not values:
        return {"median": None, "median_ci95": None, "p95": None, "p95_ci95": None, "min": None, "max": None, "n": 0}
    data = [float(value) for value in values]
    median_low, median_high = percentile_ci(data, 50)
    p95_low, p95_high = percentile_ci(data, 95)
    return {
        "median": round(percentile(data, 50), 3),
        "median_ci95": [round(median_low, 3), round(median_high, 3)],
        "p95": round(percentile(data, 95), 3),
        "p95_ci95": [round(p95_low, 3), round(p95_high, 3)],
        "min": round(min(data), 3),
        "max": round(max(data), 3),
        "n": len(data),
    }
