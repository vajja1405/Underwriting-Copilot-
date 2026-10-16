"""SYNTHETIC worlds, hidden truths and the validation scores."""

import numpy as np
import pytest
from feasibility_helpers import setup_for, synthetic_inputs

from uwcopilot.studio.feasibility import load_assumptions
from uwcopilot.studio.feasibility.validation import (
    CONDITIONS,
    DOWNTURN_MONTHS,
    band_coverage,
    base_rate_brier,
    brier,
    reliability_table,
    summarize,
    true_draws,
    true_path,
    validate_profile,
)
from uwcopilot.studio.feasibility.worlds import anchor_demand, anchor_rent, generate_world, single_point_dscr
from uwcopilot.studio.profiles import load_profile


def world_for(profile_id: str, seed: int):
    profile = load_profile(profile_id)
    inputs = synthetic_inputs(profile)
    return generate_world(profile, inputs, load_assumptions(), 0, np.random.default_rng(seed))


def test_world_is_reproducible_and_synthetic() -> None:
    first, second = world_for("laundromat", 5), world_for("laundromat", 5)
    assert first == second
    assert first.site.status == "SYNTHETIC" and "SYNTHETIC validation world" in first.site.source
    assert first.plan.owner_equity + first.loan.amount >= first.plan.capex


def test_anchored_world_hits_its_target_dscr() -> None:
    world = world_for("car_wash", 3)
    assert world.anchored
    setup = setup_for("car_wash", site=world.site, plan=world.plan, loan=world.loan)
    assert single_point_dscr(setup, world.site.mode) == pytest.approx(world.target_single_point_dscr, rel=1e-6)


def test_unreachable_target_is_marked_not_anchored() -> None:
    setup = setup_for()
    demand, reached = anchor_demand(setup, 1000.0, 600.0)
    assert (demand, reached) == (600.0, False)
    rent, reached = anchor_rent(setup, 1000.0, 600.0, 9000.0)
    assert (rent, reached) == (9000.0, False)


def test_rent_quote_anchors_a_plan_that_capacity_cannot_carry() -> None:
    # Pick a target the capacity mode cannot reach but a lower rent can: the rent lever finds that rent.
    setup = setup_for()
    ceiling = setup.distributions["capacity"].mode
    at_capacity = single_point_dscr(setup, ceiling)
    target = at_capacity + 0.2
    rent, reached = anchor_rent(setup, target, ceiling, setup.distributions["monthly_rent"].mode)
    assert reached and 0 <= rent < setup.distributions["monthly_rent"].mode
    assert single_point_dscr(setup, ceiling, rent=rent) == pytest.approx(target, rel=1e-6)


def test_every_world_records_its_anchoring_lever() -> None:
    profile = load_profile("short_term_rental")
    inputs = synthetic_inputs(profile)
    levers = set()
    for seed in range(8):
        world = generate_world(profile, inputs, load_assumptions(), seed, np.random.default_rng(seed))
        levers.add(world.anchored_by)
        assert world.anchored == (world.anchored_by != "none")
        if world.anchored_by == "rent_quote":
            assert world.plan.monthly_rent is not None
            assert world.site.mode == profile.capacity.max_demand.point()
            setup = setup_for("short_term_rental", site=world.site, plan=world.plan, loan=world.loan)
            dscr = single_point_dscr(setup, world.site.mode)
            assert dscr == pytest.approx(world.target_single_point_dscr, rel=1e-4)
    # Under the profile's modes a short-term rental does not cover its costs at full occupancy, so its worlds are
    # anchored by the rent quote (a property of the current ASSUMPTION ranges, checked here so a change is noticed).
    assert "rent_quote" in levers and levers <= {"demand", "rent_quote", "none"}


def test_true_draws_differ_only_in_the_misspecified_condition() -> None:
    setup = setup_for()
    well = true_draws(setup, np.random.default_rng(1), "well_specified")
    wrong = true_draws(setup, np.random.default_rng(1), "misspecified")
    assert well.extra_demand_multiplier is None and well.n_paths == 1
    assert wrong.extra_demand_multiplier is not None and wrong.extra_demand_multiplier.shape == (1, 36)
    assert wrong.noise_z.shape == (1, 36)
    # The site-level error is one factor; a downturn, when drawn, lowers DOWNTURN_MONTHS months further.
    distinct = np.unique(np.round(wrong.extra_demand_multiplier, 12))
    assert len(distinct) in (1, 2)
    if len(distinct) == 2:
        assert np.sum(wrong.extra_demand_multiplier == distinct.min()) == DOWNTURN_MONTHS


def test_many_true_futures_each_get_their_own_effects() -> None:
    setup = setup_for()
    wrong = true_draws(setup, np.random.default_rng(3), "misspecified", n_paths=50)
    assert wrong.n_paths == 50 and wrong.noise_z.shape == (50, 36)
    assert wrong.extra_demand_multiplier.shape == (50, 36)
    assert len(np.unique(np.round(wrong.extra_demand_multiplier[:, 0], 12))) > 1
    paths = true_path(setup, 3, "well_specified", n_paths=20)
    assert paths.revenue.shape == (20, 36)


def test_band_coverage_and_brier_hand_worked() -> None:
    forecast = np.tile(np.arange(1, 101, dtype=float).reshape(100, 1), (1, 4))
    truth = np.array([[50.0, 5.0, 95.0, 10.9]])  # P10 = 10.9, P90 = 90.1
    assert band_coverage(forecast, truth) == 0.5
    # Several true paths: the share over every (path, month) pair.
    assert band_coverage(forecast, np.vstack([truth, np.full((1, 4), 50.0)])) == 0.75
    assert brier(np.array([0.8, 0.2]), np.array([1, 0])) == pytest.approx(0.04)
    assert base_rate_brier(np.array([1.0, 0.0, 0.0, 0.0])) == pytest.approx(0.1875)


def test_validate_profile_small_run_and_summary() -> None:
    profile = load_profile("retail_storefront")
    scores = validate_profile(profile, 4, synthetic_inputs(profile), load_assumptions(), 6, 200, 36, 42, 1.25)
    assert set(scores) == set(CONDITIONS)
    for items in scores.values():
        assert len(items) == 6
        assert all(0 <= item.p_dscr <= 1 and 0 <= item.coverage_revenue <= 1 for item in items)
    # Same worlds and the same forecasts under both conditions: only the truth changes.
    assert [item.p_dscr for item in scores["well_specified"]] == [item.p_dscr for item in scores["misspecified"]]
    summary = summarize(scores["well_specified"], 50, 42)
    assert summary["n_worlds"] == 6
    assert sum(summary["anchored_by"].values()) == 6
    assert summary["n_worlds_not_anchored"] == summary["anchored_by"]["none"]
    assert {"brier_simulator", "brier_single_forecast", "brier_base_rate_in_sample"} <= set(summary["dscr_year2"])
    assert 0 <= summary["dscr_year2"]["single_forecast_yes_rate"] <= 1
    assert summary["band_coverage_revenue"]["n_resamples"] == 50
    assert 0 <= summary["expected_band_coverage_revenue"]["estimate"] <= 1
    gap = summary["probability_gap_dscr_year2"]
    assert 0 <= gap["mean_true_probability"] <= 1 and gap["mean_abs_gap"]["estimate"] >= 0
    table = reliability_table(scores["well_specified"])
    assert sum(row["n_worlds"] for row in table) == 6


def test_well_specified_bands_cover_about_eighty_percent() -> None:
    # A property of the code: when the truth comes from the simulator's own model, about 80% of true months
    # fall inside P10-P90. Seeded, so the result is the same on every run.
    profile = load_profile("laundromat")
    scores = validate_profile(profile, 1, synthetic_inputs(profile), load_assumptions(), 40, 300, 36, 7, 1.25)
    coverage = np.mean([item.coverage_revenue for item in scores["well_specified"]])
    assert 0.7 <= coverage <= 0.9
    # With 100 extra true futures per world the noise is much smaller; the reference for 300 paths is
    # 0.8 x 299 / 301 (about 0.795).
    expected = np.mean([item.expected_coverage_revenue for item in scores["well_specified"]])
    assert abs(expected - 0.8 * 299 / 301) < 0.02
    gaps = [abs(item.p_dscr - item.true_p_dscr) for item in scores["well_specified"]]
    assert np.mean(gaps) < 0.1
