"""SIMULATED streams and the validation summary (SYNTHETIC data, offline)."""

import numpy as np
import pandas as pd
import pytest
from monitoring_test_helpers import synthetic_policies, toy_frequency

from uwcopilot.governance.monitoring import MonitoredPool, build_monitored_pool
from uwcopilot.governance.monitoring_config import (
    BootstrapSettings,
    CovariateShift,
    Scenario,
    StreamSettings,
    Subgroup,
    load_monitoring_config,
)
from uwcopilot.governance.monitoring_stream import (
    StreamOutcome,
    draw_rows,
    first_alert_delay,
    rate_with_ci,
    run_scenario,
    simulate_outcomes,
    simulate_stream,
    subgroup_rows,
    summarize_scenario,
)
from uwcopilot.risk.data.fremtpl2 import RATING_FEATURES

BOOTSTRAP = BootstrapSettings(resamples=1000, seed=42, confidence=0.95)
NO_DRIFT = Scenario(description="SYNTHETIC no drift")
YOUNG = Scenario(
    description="SYNTHETIC young drivers",
    covariate_shift=CovariateShift(subgroup=Subgroup(column="DrivAge", below=30), target_share=0.4),
)
MORE_CLAIMS = Scenario(description="SYNTHETIC claim rate x 3", claim_rate_multiplier=3.0)


def settings(n_streams: int = 4) -> StreamSettings:
    """A short stream: 6 windows of 1,500 policies (about 70 predicted claims each), drift from window 3."""
    return StreamSettings(
        window_size=1500,
        n_windows=6,
        drift_start_window=3,
        n_streams=n_streams,
        seed=42,
        scenarios={"no_drift": NO_DRIFT, "young": YOUNG, "more_claims": MORE_CLAIMS},
        demonstration={"scenario": "young", "stream": 0},
    )


@pytest.fixture(scope="module")
def pool_and_frame() -> tuple[MonitoredPool, pd.DataFrame]:
    """A pool of 4,000 SYNTHETIC policies binned against 4,000 others."""
    thresholds = load_monitoring_config().thresholds
    reference, pool_frame = synthetic_policies(4000, seed=11), synthetic_policies(4000, seed=12)
    pool = build_monitored_pool(
        reference, toy_frequency(reference), pool_frame, toy_frequency(pool_frame), RATING_FEATURES, thresholds
    )
    return pool, pool_frame


def test_a_shifted_window_has_the_target_share_and_no_repeated_row(
    pool_and_frame: tuple[MonitoredPool, pd.DataFrame],
) -> None:
    pool, pool_frame = pool_and_frame
    groups = subgroup_rows(pool_frame, Subgroup(column="DrivAge", below=30))
    rows = draw_rows(np.random.default_rng(0), pool.size, 500, (groups, 0.4))
    assert rows.size == 500 and np.unique(rows).size == 500
    assert (pool_frame["DrivAge"].to_numpy()[rows] < 30).mean() == pytest.approx(0.4)
    region = subgroup_rows(pool_frame, Subgroup(column="Region", equals="R11"))
    assert set(pool_frame["Region"].iloc[region.inside]) == {"R11"}
    with pytest.raises(ValueError):
        draw_rows(np.random.default_rng(0), pool.size, 4000, (groups, 0.9))


def test_simulated_outcomes_add_thin_or_keep_claims() -> None:
    rng = np.random.default_rng(1)
    recorded = np.array([0, 1, 2, 0, 3] * 2000)
    expected = np.full(recorded.size, 1.2)
    assert np.array_equal(simulate_outcomes(rng, recorded, expected, 1.0), recorded)
    more = simulate_outcomes(rng, recorded, expected, 1.5)
    assert (more >= recorded).all() and more.sum() - recorded.sum() == pytest.approx(0.5 * expected.sum(), rel=0.05)
    fewer = simulate_outcomes(rng, recorded, expected, 0.5)
    assert (fewer <= recorded).all() and fewer.sum() == pytest.approx(0.5 * recorded.sum(), rel=0.05)
    with pytest.raises(ValueError):
        simulate_outcomes(rng, recorded, expected, 0.0)


def test_streams_are_reproducible_and_clean_windows_match_across_scenarios(
    pool_and_frame: tuple[MonitoredPool, pd.DataFrame],
) -> None:
    pool, pool_frame = pool_and_frame
    stream_settings = settings()
    groups = subgroup_rows(pool_frame, YOUNG.covariate_shift.subgroup)
    first = simulate_stream(pool, YOUNG, stream_settings, 1, groups)
    again = simulate_stream(pool, YOUNG, stream_settings, 1, groups)
    clean = simulate_stream(pool, NO_DRIFT, stream_settings, 1)
    more = simulate_stream(pool, MORE_CLAIMS, stream_settings, 1)
    assert all(np.array_equal(a.rows, b.rows) for a, b in zip(first, again, strict=True))
    for index in range(stream_settings.drift_start_window):
        assert np.array_equal(first[index].rows, clean[index].rows)
        assert np.array_equal(more[index].observed_claims, clean[index].observed_claims)
    assert [window.drifted for window in first] == [False, False, False, True, True, True]
    assert not any(window.drifted for window in clean)
    with pytest.raises(ValueError):
        simulate_stream(pool, YOUNG, stream_settings, 1, None)


def test_the_monitor_finds_strong_injected_drift_and_stays_quiet_otherwise(
    pool_and_frame: tuple[MonitoredPool, pd.DataFrame],
) -> None:
    pool, pool_frame = pool_and_frame
    thresholds = load_monitoring_config().thresholds
    stream_settings = settings(n_streams=3)
    groups = subgroup_rows(pool_frame, YOUNG.covariate_shift.subgroup)
    quiet = run_scenario(pool, NO_DRIFT, stream_settings, thresholds)
    assert all(not any(name == "input_drift" for _, name in outcome.alerting_checks) for outcome in quiet)
    young = run_scenario(pool, YOUNG, stream_settings, thresholds, groups)
    assert all(first_alert_delay(outcome.alert_windows, 3) == 0 for outcome in young)
    claims = run_scenario(pool, MORE_CLAIMS, stream_settings, thresholds)
    assert all((3, "calibration_ratio") in outcome.alerting_checks for outcome in claims)
    assert all(not any(name == "input_drift" for _, name in outcome.alerting_checks) for outcome in claims)


def test_delay_counts_windows_from_the_drift_start() -> None:
    assert first_alert_delay((1, 5, 7), drift_start=3) == 2
    assert first_alert_delay((0, 1), drift_start=3) is None
    assert first_alert_delay((), drift_start=3) is None


def outcome(stream: int, alerts: tuple[int, ...], checks: tuple[tuple[int, str], ...] = ()) -> StreamOutcome:
    """A hand-made stream outcome."""
    return StreamOutcome(
        stream=stream, alert_windows=alerts, warn_windows=alerts, alerting_checks=checks, warning_checks=checks
    )


def test_summary_of_a_no_drift_scenario_counts_streams_with_any_alert() -> None:
    outcomes = [outcome(0, ()), outcome(1, (4,), ((4, "calibration_ratio"),)), outcome(2, ()), outcome(3, ())]
    outcomes[2] = StreamOutcome(2, (), (5,), (), ((5, "calibration_ratio"), (5, "input_drift")))
    summary = summarize_scenario(outcomes, NO_DRIFT, settings(), BOOTSTRAP)
    assert summary["warn_rate"]["numerator"] == 2
    assert summary["warn_rate_by_check"]["calibration_ratio"]["numerator"] == 2
    assert summary["warn_rate_by_check"]["input_drift"]["numerator"] == 1
    rate = summary["false_alert_rate"]
    assert (rate["estimate"], rate["numerator"], rate["denominator"]) == (0.25, 1, 4)
    assert rate["ci_low"] <= 0.25 <= rate["ci_high"] and rate["unit"] == "stream"
    assert summary["false_alert_rate_by_check"]["calibration_ratio"]["numerator"] == 1
    assert summary["windows_with_an_alert"] == {"numerator": 1, "denominator": 24, "estimate": 1 / 24}


def test_summary_of_a_drifted_scenario_reports_detection_delay_and_pre_drift_alerts() -> None:
    outcomes = [
        outcome(0, (3,), ((3, "input_drift"),)),
        outcome(1, (1, 5), ((1, "calibration_ece"), (5, "input_drift"))),
        outcome(2, ()),
        outcome(3, (4,), ((4, "score_drift"),)),
    ]
    summary = summarize_scenario(outcomes, YOUNG, settings(), BOOTSTRAP)
    assert summary["detection_rate"]["numerator"] == 3
    delay = summary["detection_delay_windows"]
    assert delay["mean"]["estimate"] == pytest.approx(1.0)  # delays 0, 2, 1
    assert delay["streams_by_delay"] == {"0": 1, "1": 1, "2": 1} and delay["n_detected_streams"] == 3
    assert summary["pre_drift_false_alert_rate"]["numerator"] == 1
    assert summary["detection_rate_by_check"]["input_drift"]["numerator"] == 2
    assert summary["detection_rate_by_check"]["calibration_ece"]["numerator"] == 0


def test_no_detection_means_no_delay_and_rates_need_streams() -> None:
    summary = summarize_scenario([outcome(0, ()), outcome(1, (1,))], YOUNG, settings(2), BOOTSTRAP)
    assert summary["detection_rate"]["estimate"] == 0.0
    delay = summary["detection_delay_windows"]
    assert delay["status"] == "not available" and "mean" not in delay
    assert (delay["n_streams"], delay["n_detected_streams"], delay["max_possible_delay"]) == (2, 0, 2)
    assert {k: rate["numerator"] for k, rate in delay["detected_within_windows"].items()} == {"0": 0, "1": 0, "2": 0}
    with pytest.raises(ValueError):
        rate_with_ci([], BOOTSTRAP)


def test_the_delay_says_it_covers_detected_streams_only_and_is_capped() -> None:
    """P6-STAT-3: 6 windows, drift from window 3, so a delay is at most 2; undetected streams stay in a denominator."""
    outcomes = [
        outcome(0, (3,)),  # delay 0
        outcome(1, (5,)),  # delay 2, the last window: the cap
        outcome(2, ()),  # not detected
        outcome(3, (1, 4)),  # delay 1 (the alert in window 1 is before the drift)
    ]
    delay = summarize_scenario(outcomes, YOUNG, settings(), BOOTSTRAP)["detection_delay_windows"]
    assert delay["mean"]["estimate"] == pytest.approx(1.0) and delay["mean"]["unit"] == "detected stream"
    assert delay["mean_over"].startswith("detected streams only")
    assert (delay["n_streams"], delay["n_detected_streams"]) == (4, 3)
    assert (delay["max_possible_delay"], delay["streams_at_max_possible_delay"]) == (2, 1)
    within = delay["detected_within_windows"]
    assert [within[k]["numerator"] for k in ("0", "1", "2")] == [1, 2, 3]
    assert all(rate["denominator"] == 4 and rate["unit"] == "stream" for rate in within.values())


def test_rates_carry_the_exact_interval_and_flag_a_misleading_bootstrap() -> None:
    """P6-STAT-4: 0 or 1 of 300 gives a zero-width or too narrow bootstrap interval; the exact one is recorded."""
    none = rate_with_ci([False] * 300, BOOTSTRAP)
    assert (none["ci_low"], none["ci_high"]) == (0.0, 0.0)
    assert none["exact_ci_low"] == 0.0 and none["exact_ci_high"] == pytest.approx(0.0122, abs=1e-4)
    assert "zero-width" in none["bootstrap_interval_note"]
    one = rate_with_ci([True] + [False] * 299, BOOTSTRAP)
    assert one["ci_high"] < 0.012
    assert one["exact_ci_low"] == pytest.approx(0.0001, abs=1e-4)
    assert one["exact_ci_high"] == pytest.approx(0.0184, abs=1e-4)
    assert "too narrow" in one["bootstrap_interval_note"]
    every = rate_with_ci([True] * 300, BOOTSTRAP)
    assert every["exact_ci_low"] == pytest.approx(0.9878, abs=1e-4) and every["exact_ci_high"] == 1.0
    assert "zero-width" in every["bootstrap_interval_note"]
    assert "too narrow" in rate_with_ci([True] * 299 + [False], BOOTSTRAP)["bootstrap_interval_note"]
    half = rate_with_ci([True, False] * 150, BOOTSTRAP)
    assert half["bootstrap_interval_note"] is None and half["exact_ci_low"] < 0.5 < half["exact_ci_high"]
    assert half["method"] == "percentile bootstrap" and half["exact_method"].startswith("Clopper-Pearson")
