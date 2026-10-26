"""Scenario-bootstrap intervals of the S12 metrics (docs/studio/s12_baseline.md, section 4), on hand-made counts."""

import numpy as np
import pytest

from uwcopilot.studio.business_metrics.intervals import (
    paired_difference,
    pooled,
    pooled_share,
    resample_indices,
    seconds_summary,
    spread,
)


def test_the_pooled_share_is_the_sum_of_hits_over_the_sum_of_figures() -> None:
    block = pooled_share([1, 3, 0], [2, 4, 4])
    assert (block["k"], block["n"], block["scenarios"]) == (4, 10, 3)
    assert block["value"] == 0.4 and block["ci_low"] <= block["value"] <= block["ci_high"]


def test_the_interval_is_seeded_and_resamples_whole_scenarios() -> None:
    first = pooled_share([5, 1, 9, 2], [10, 10, 10, 10])
    assert first == pooled_share([5, 1, 9, 2], [10, 10, 10, 10])
    indices = resample_indices(4)
    assert indices.shape == (1000, 4) and indices.min() >= 0 and indices.max() <= 3
    shares = pooled(np.array([5, 1, 9, 2])[indices], np.array([10, 10, 10, 10])[indices])
    assert first["ci_low"] == pytest.approx(round(float(np.quantile(shares, 0.025)), 4))


def test_scenarios_with_one_share_give_a_point_interval() -> None:
    block = pooled_share([2, 4, 6], [4, 8, 12])
    assert block["value"] == block["ci_low"] == block["ci_high"] == 0.5


def test_no_figures_gives_no_value() -> None:
    block = pooled_share([0, 0], [0, 0])
    assert block["value"] is None and block["n"] == 0


@pytest.mark.parametrize(("hits", "totals"), [([3], [2]), ([-1], [2]), ([1, 2], [3])])
def test_impossible_counts_are_refused(hits: list[int], totals: list[int]) -> None:
    with pytest.raises(ValueError):
        pooled_share(hits, totals)


def test_the_paired_difference_uses_the_same_scenarios_for_both_plans() -> None:
    block = paired_difference([8, 9, 7], [10, 10, 10], [1, 0, 2], [10, 10, 10])
    assert block["value"] == pytest.approx(0.7) and block["scenarios"] == 3
    assert block["ci_low"] <= block["value"] <= block["ci_high"]
    same = paired_difference([1, 2], [4, 4], [1, 2], [4, 4])
    assert same["value"] == same["ci_low"] == same["ci_high"] == 0.0


def test_a_paired_difference_needs_the_same_scenarios() -> None:
    with pytest.raises(ValueError):
        paired_difference([1], [2], [1, 1], [2, 2])
    with pytest.raises(ValueError):
        paired_difference([], [], [], [])


def test_spread_and_seconds_summary() -> None:
    assert spread([3.0, 1.0, 2.0]) == {"min": 1.0, "median": 2.0, "max": 3.0, "n": 3}
    assert spread([])["n"] == 0
    summary = seconds_summary([1.0, 2.0, 3.0, 4.0, 10.0])
    assert summary["median"] == 3.0 and summary["n"] == 5 and summary["max"] == 10.0
    low, high = summary["median_ci95"]
    assert low <= summary["median"] <= high
    assert seconds_summary([])["median"] is None
