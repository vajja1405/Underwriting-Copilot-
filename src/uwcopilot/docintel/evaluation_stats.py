"""Generic statistics of the Phase 2 evaluation: summed counters, ratios, P/R/F1 and the packet-level bootstrap.

Every metric is a ratio of counts SUMMED over packets (micro-average). Confidence intervals are percentile bootstrap
95% intervals with packet-level resampling (1,000 resamples, seed 42: N_RESAMPLES and BOOTSTRAP_SEED of
agent/evaluation.py). Each resample draws packets with replacement, sums their counts and recomputes the metric, so
values of one packet always move together. A resample in which a metric is undefined (zero denominator) is skipped;
n_resamples_used says how many were left. Ratio, precision and recall metrics also carry the exact
(Clopper-Pearson) interval and flags from evaluation_intervals.py. evaluation_summary.py builds the report sections
from these functions (and re-exports them).
"""

from collections import Counter
from collections.abc import Callable
from typing import Any

import numpy as np

from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES
from uwcopilot.docintel.evaluation_intervals import SMALL_N, SMALL_N_NOTE, add_exact_interval

CountRow = dict[str, float]
Statistic = Callable[[CountRow], float | None]


def totals(rows: list[CountRow]) -> Counter:
    """The counts of all rows added up."""
    summed: Counter = Counter()
    for row in rows:
        summed.update(row)
    return summed


def safe_ratio(numerator: float, denominator: float) -> float | None:
    """numerator / denominator, or None when the denominator is zero."""
    return None if denominator == 0 else numerator / denominator


def f1_of(counts: CountRow, prefix: str) -> float | None:
    """F1 = 2 TP / (2 TP + FP + FN) for the counters prefix.tp / .fp / .fn (None when all are zero)."""
    tp = counts.get(f"{prefix}.tp", 0)
    return safe_ratio(2 * tp, 2 * tp + counts.get(f"{prefix}.fp", 0) + counts.get(f"{prefix}.fn", 0))


def precision_of(counts: CountRow, prefix: str) -> float | None:
    """TP / (TP + FP)."""
    tp = counts.get(f"{prefix}.tp", 0)
    return safe_ratio(tp, tp + counts.get(f"{prefix}.fp", 0))


def recall_of(counts: CountRow, prefix: str) -> float | None:
    """TP / (TP + FN)."""
    tp = counts.get(f"{prefix}.tp", 0)
    return safe_ratio(tp, tp + counts.get(f"{prefix}.fn", 0))


def bootstrap(rows: list[CountRow], statistic: Statistic, names: list[str]) -> dict[str, Any]:
    """The statistic on the summed rows, with a packet-level percentile bootstrap 95% interval.

    names are the counters the statistic reads; only they are resampled (the others do not change the result).
    """
    value = statistic(totals(rows))
    result: dict[str, Any] = {"value": None if value is None else round(value, 4), "ci_low": None, "ci_high": None}
    result["n_packets"] = len(rows)
    if value is None or not rows:
        result["n_resamples_used"] = 0
        return result
    matrix = np.array([[row.get(name, 0.0) for name in names] for row in rows], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(rows), size=(N_RESAMPLES, len(rows)))
    estimates = []
    for sums in matrix[indices].sum(axis=1):
        estimate = statistic(dict(zip(names, sums, strict=True)))
        if estimate is not None:
            estimates.append(estimate)
    result["ci_low"] = round(float(np.quantile(estimates, 0.025)), 4)
    result["ci_high"] = round(float(np.quantile(estimates, 0.975)), 4)
    result["n_resamples_used"] = len(estimates)
    return result


def counts_of(records: list[dict[str, Any]], prefix: str = "") -> list[CountRow]:
    """The counter dicts of the records, with every name prefixed (for paired comparisons)."""
    return [{prefix + name: float(value) for name, value in record["counts"].items()} for record in records]


def ratio_metric(records: list[dict[str, Any]], numerator: str, denominator: str) -> dict[str, Any]:
    """counts[numerator] / counts[denominator] with its bootstrap interval and the two summed counts."""
    rows = counts_of(records)
    summed = totals(rows)
    result = bootstrap(
        rows, lambda counts: safe_ratio(counts.get(numerator, 0), counts.get(denominator, 0)), [numerator, denominator]
    )
    result["numerator"] = int(summed.get(numerator, 0))
    result["denominator"] = int(summed.get(denominator, 0))
    return add_exact_interval(result, result["numerator"], result["denominator"])


def prf_metric(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    """Precision, recall and F1 of prefix.tp / .fp / .fn, each with an interval, and the summed counts."""
    rows = counts_of(records)
    summed = totals(rows)
    names = [f"{prefix}.tp", f"{prefix}.fp", f"{prefix}.fn"]
    tp, fp, fn = (int(summed.get(name, 0)) for name in names)
    f1 = bootstrap(rows, lambda counts: f1_of(counts, prefix), names)
    f1["bootstrap_degenerate"] = f1["ci_low"] is not None and f1["ci_low"] == f1["ci_high"]
    if tp + fp + fn < SMALL_N:
        f1["interval_note"] = SMALL_N_NOTE
    return {
        "precision": add_exact_interval(
            bootstrap(rows, lambda counts: precision_of(counts, prefix), names), tp, tp + fp
        ),
        "recall": add_exact_interval(bootstrap(rows, lambda counts: recall_of(counts, prefix), names), tp, tp + fn),
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def paired_f1_difference(
    first: list[dict[str, Any]], second: list[dict[str, Any]], first_prefix: str, second_prefix: str
) -> dict[str, Any]:
    """F1(first) - F1(second) on the same packets (records aligned by packet_id), with a paired interval."""
    by_id = {record["packet_id"]: record for record in second}
    paired = [record for record in first if record["packet_id"] in by_id]
    rows = []
    for record in paired:
        row = {f"a.{name}": float(value) for name, value in record["counts"].items()}
        row.update({f"b.{name}": float(value) for name, value in by_id[record["packet_id"]]["counts"].items()})
        rows.append(row)

    def difference(counts: CountRow) -> float | None:
        a_value = f1_of(counts, f"a.{first_prefix}")
        b_value = f1_of(counts, f"b.{second_prefix}")
        return None if a_value is None or b_value is None else a_value - b_value

    names = [
        f"{side}.{prefix}.{outcome}"
        for side, prefix in (("a", first_prefix), ("b", second_prefix))
        for outcome in ("tp", "fp", "fn")
    ]
    return bootstrap(rows, difference, names)


def counter_suffixes(records: list[dict[str, Any]], prefix: str) -> list[str]:
    """The distinct counter-name parts after prefix (up to the next dot) in any record."""
    found = set()
    for record in records:
        for name in record["counts"]:
            if name.startswith(prefix):
                found.add(name[len(prefix) :].split(".", 1)[0])
    return sorted(found)
