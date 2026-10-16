"""SiteIQ -> simulator demand contract: sampled percentiles (not a triangle over the bounds), incomplete estimates
shown, and no DSCR probability for a year without a full year of loan payments. Every input is SYNTHETIC."""

import numpy as np
import pytest
from feasibility_helpers import METRO, basic_loan, basic_plan, car_wash_site

from uwcopilot.studio import PERCENTILE_LEVELS
from uwcopilot.studio.feasibility import as_site_demand, simulate
from uwcopilot.studio.feasibility.distributions import empirical, triangular
from uwcopilot.studio.feasibility.simulate import run_simulation
from uwcopilot.studio.profiles import load_profile
from uwcopilot.studio.siteiq.demand import estimate_demand
from uwcopilot.studio.siteiq.factors import RawFactor


def data(factor: str, value: float | None) -> RawFactor:
    if value is None:
        return RawFactor(factor, None, "UNKNOWN", "within 1 mile", "no value in this test")
    return RawFactor(factor, value, "SYNTHETIC", "within 1 mile", "hand-worked test value")


def triangle_median(low: float, mode: float, high: float) -> float:
    """The exact median of a triangular distribution."""
    if (mode - low) / (high - low) >= 0.5:
        return low + np.sqrt(0.5 * (high - low) * (mode - low))
    return high - np.sqrt(0.5 * (high - low) * (high - mode))


def restaurant_estimate(workers: float | None = 3000.0):
    measured = {"resident_population": data("resident_population", 9000.0)}
    measured["daytime_workers"] = data("daytime_workers", workers)
    return estimate_demand(load_profile("restaurant_cafe"), measured, "within 1 mile")


def test_one_rate_formula_percentiles_match_the_rate_triangle() -> None:
    profile = load_profile("car_wash")
    rate = profile.demand.inputs["capture_rate"]
    result = estimate_demand(profile, {"traffic_aadt": data("traffic_aadt", 20000.0)}, "nearest road")
    assert len(result.percentiles) == len(PERCENTILE_LEVELS)
    assert result.percentiles[0] == result.low and result.percentiles[-1] == result.high
    assert result.percentiles == sorted(result.percentiles)
    expected_median = 20000 * triangle_median(rate.low, rate.mode, rate.high)
    assert result.p50 == pytest.approx(expected_median, rel=0.02)
    assert result.p10 < result.p50 < result.p90 and "seed" in result.sample_method


def test_simulated_demand_follows_the_percentiles_not_a_triangle_over_the_bounds() -> None:
    estimate = restaurant_estimate()
    site = as_site_demand(estimate)
    assert site.percentiles == estimate.percentiles
    plan = basic_plan(capex=500_000.0, owner_equity=200_000.0)
    run = run_simulation("restaurant_cafe", METRO, site, plan, basic_loan(amount=400_000.0), n=4000, with_tornado=False)
    sampled = run.values["mature_demand"]
    mature = run.setup.distributions["mature_demand"]
    assert mature.kind == "empirical" and mature.mode == estimate.mode  # one-forecast case keeps the mode
    assert float(np.median(sampled)) == pytest.approx(estimate.p50, rel=0.05)
    # The old triangle over (all-low, mode, all-high) has its median far above the sampled one.
    assert float(np.median(sampled)) < 0.8 * triangle_median(estimate.low, estimate.mode, estimate.high)


def test_empirical_distribution_checks_and_quantiles() -> None:
    points = [float(step) for step in range(101)]
    item = empirical("x", "test", "units", points, 40.0, "MODEL_ESTIMATE", "test")
    assert item.percentile(0.5) == pytest.approx(50.0) and item.percentile(0.105) == pytest.approx(10.5)
    assert item.mean() == pytest.approx(50.0)
    with pytest.raises(ValueError, match="percentile points"):
        empirical("x", "test", "units", points[:-1], 40.0, "MODEL_ESTIMATE", "test")
    with pytest.raises(ValueError, match="must not decrease"):
        dipped = points[:50] + [48.0] + points[51:]
        empirical("x", "test", "units", dipped, 40.0, "MODEL_ESTIMATE", "test")
    assert triangular("y", "test", "units", 1.0, 2.0, 3.0, "ASSUMPTION", "test").points is None


def test_incomplete_estimate_reaches_the_result_with_a_note() -> None:
    estimate = restaurant_estimate(workers=None)
    assert not estimate.complete
    site = as_site_demand(estimate)
    assert not site.complete and any("daytime_workers" in item for item in site.missing)
    plan = basic_plan(capex=500_000.0, owner_equity=200_000.0)
    result = simulate("restaurant_cafe", METRO, site, plan, basic_loan(amount=400_000.0), n=200, with_tornado=False)
    assert not result.site_demand.complete
    assert any("INCOMPLETE" in note and "daytime_workers" in note for note in result.notes)


def test_complete_estimate_has_no_incomplete_note() -> None:
    result = simulate("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(), n=100, with_tornado=False)
    assert result.site_demand.complete and not any("INCOMPLETE" in note for note in result.notes)


@pytest.mark.parametrize(("term", "reported"), [(24, ["year_1", "year_2"]), (30, ["year_1", "year_2"])])
def test_no_dscr_probability_for_a_year_without_twelve_payments(term: int, reported: list[str]) -> None:
    result = simulate(
        "car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(term_months=term), n=200, with_tornado=False
    )
    assert sorted(result.p_dscr_at_least_target) == reported
    year_3 = result.yearly[2]
    assert year_3.dscr_p10 is None and year_3.dscr_p50 is None and year_3.dscr_p90 is None
    assert any(note.startswith("year 3: no DSCR") and f"{term}-month loan" in note for note in result.notes)


def test_a_full_third_year_keeps_its_probability() -> None:
    result = simulate(
        "car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(term_months=36), n=200, with_tornado=False
    )
    assert sorted(result.p_dscr_at_least_target) == ["year_1", "year_2", "year_3"]
    assert result.yearly[2].dscr_p50 is not None
