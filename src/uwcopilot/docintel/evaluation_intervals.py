"""Exact confidence intervals for a proportion, reported next to the bootstrap interval (Phase 2 evaluation).

The packet-level percentile bootstrap (evaluation_summary.bootstrap) cannot bound a proportion of 0 or 1: every
resample is also 0 (or 1), so its interval collapses to [0, 0] (or [1, 1]) and suggests a certainty that the data
do not have (0 false positives in 60 packets is compatible with a true rate of several percent). With few items
every interval is wide. So every ratio metric (numerator / denominator) also carries:

- exact_ci_low / exact_ci_high: the Clopper-Pearson ("exact") 95% interval of numerator out of denominator, from
  the beta distribution. It treats the counted items as independent. For per-packet rates (one count per packet)
  that is right; for item-level counts (fields, rows, problems) items of one packet are not independent, so the
  exact interval can be narrower than one that respects packets. Read both.
- bootstrap_degenerate: True when the bootstrap interval has zero width (a 0% or 100% metric).
- interval_note: "n < 10: interval not informative" when the denominator is below SMALL_N.

How skipped bootstrap resamples are handled: a resample whose denominator is zero (for example no problem of that
type was drawn) leaves the metric undefined, so it is skipped; n_resamples_used says how many remained, and the
bootstrap interval is then conditional on the metric being defined.
"""

from typing import Any

from scipy.stats import beta

CONFIDENCE = 0.95
SMALL_N = 10
SMALL_N_NOTE = "n < 10: interval not informative"


def clopper_pearson(successes: int, trials: int, confidence: float = CONFIDENCE) -> tuple[float, float] | None:
    """The exact two-sided interval of successes / trials, or None when trials is 0."""
    if trials <= 0:
        return None
    alpha = 1.0 - confidence
    low = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    high = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return low, high


def add_exact_interval(metric: dict[str, Any], successes: int, trials: int) -> dict[str, Any]:
    """The metric dict with exact_ci_low / exact_ci_high, bootstrap_degenerate and interval_note added."""
    exact = clopper_pearson(successes, trials)
    metric["exact_ci_low"] = None if exact is None else round(exact[0], 4)
    metric["exact_ci_high"] = None if exact is None else round(exact[1], 4)
    low, high = metric.get("ci_low"), metric.get("ci_high")
    metric["bootstrap_degenerate"] = low is not None and low == high
    if trials < SMALL_N:
        metric["interval_note"] = SMALL_N_NOTE
    return metric
