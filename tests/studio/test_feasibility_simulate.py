"""simulate(): labels, shape, reproducibility, consistency with the paths, and the lender handoff."""

import numpy as np
import pytest
from feasibility_helpers import METRO, basic_loan, basic_plan, car_wash_site

from uwcopilot.agent.applications import ProjectionYear, SimulationOutputs
from uwcopilot.agent.policy_startup import program_ratio
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.feasibility import SIMULATOR_VERSION, default_dscr_target, run_simulation, simulate
from uwcopilot.studio.feasibility.assumptions import custom_dscr_target
from uwcopilot.studio.feasibility.loan import annual_debt_service
from uwcopilot.studio.feasibility.metrics import dscr_by_year
from uwcopilot.studio.feasibility.projection import projection_years, simulation_outputs
from uwcopilot.studio.feasibility.simulate import add_months

PATHS = 400


@pytest.fixture(scope="module")
def run():
    return run_simulation("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(), n=PATHS, seed=11)


def test_labels_and_shape(run) -> None:
    result = run.result
    assert result.label == SIMULATION_LABEL and result.disclaimer == DISCLAIMER
    assert result.data_label == "SYNTHETIC" and result.metro_mode == "synthetic"
    assert result.simulator_version == SIMULATOR_VERSION
    assert (result.n_paths, result.months, len(result.monthly), len(result.yearly)) == (PATHS, 36, 36, 3)
    assert result.monthly[0].calendar_month == "2027-03" and result.monthly[-1].calendar_month == "2030-02"
    assert set(result.p_dscr_at_least_target) == {"year_1", "year_2", "year_3"}
    assert 0 <= result.p_cash_negative_within_24_months <= 1
    assert result.dscr_target == default_dscr_target()
    assert len(result.shocks) == 3
    assert {item.name for item in result.distributions} == set(run.setup.distributions)


def test_bands_are_ordered(run) -> None:
    for month in run.result.monthly:
        assert month.revenue_p10 <= month.revenue_p50 <= month.revenue_p90
        assert month.net_income_p10 <= month.net_income_p50 <= month.net_income_p90
        assert month.cash_p10 <= month.cash_p50 <= month.cash_p90
    for year in run.result.yearly:
        assert year.revenue_p10 <= year.revenue_p50 <= year.revenue_p90
        assert year.noi_p10 <= year.noi_p50 <= year.noi_p90


def test_probabilities_match_the_paths(run) -> None:
    target = run.result.dscr_target.value
    year2 = dscr_by_year(run.paths, 2)
    assert run.result.p_dscr_at_least_target["year_2"] == pytest.approx(np.mean(year2 >= target))
    assert run.result.p_cash_negative_within_24_months == pytest.approx(np.mean(np.any(run.paths.cash[:, :24] < 0, 1)))
    counts = run.result.break_even.paths_by_month
    assert sum(counts.values()) == PATHS
    assert run.result.break_even.share_not_within_horizon == pytest.approx(counts["not within horizon"] / PATHS)


def test_same_seed_same_result_and_seed_matters() -> None:
    args = ("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan())
    first = simulate(*args, n=200, seed=3, with_tornado=False)
    second = simulate(*args, n=200, seed=3, with_tornado=False)
    other = simulate(*args, n=200, seed=4, with_tornado=False)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.monthly != other.monthly
    assert first.tornado is None


def test_projected_dscr_is_the_lender_formula(run) -> None:
    loan = run.result.loan
    service = round(annual_debt_service(loan.amount, loan.annual_rate_pct, loan.term_months), 2)
    assert run.result.projected_dscr_base_case == program_ratio(run.result.yearly[1].noi_p50, service)
    assert loan.annual_debt_service == pytest.approx(12 * loan.monthly_payment, abs=0.06)


def test_stress_preset_runs_both_tests_on_a_variable_loan(run) -> None:
    names = [item.name for item in run.result.stress]
    assert names == ["revenue_stress", "rate_stress"]
    revenue = run.result.stress[0]
    assert revenue.applies and revenue.base_case_dscr < run.result.projected_dscr_base_case
    fixed = simulate("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(rate_type="fixed"), n=100)
    assert not fixed.stress[1].applies and fixed.stress[1].p_dscr_year2_at_least_min is None


def test_custom_target_and_bad_requests() -> None:
    args = ("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan())
    easy = simulate(*args, n=200, dscr_target=custom_dscr_target(0.5), with_tornado=False, with_stress=False)
    hard = simulate(*args, n=200, dscr_target=custom_dscr_target(3.0), with_tornado=False, with_stress=False)
    assert easy.p_dscr_at_least_target["year_2"] >= hard.p_dscr_at_least_target["year_2"]
    with pytest.raises(ValueError):
        simulate(*args, n=0)
    with pytest.raises(ValueError):
        simulate(*args, n=10, months=12)


def test_lender_handoff_validates_against_the_lender_schema(run) -> None:
    years = projection_years(run.result)
    assert [type(item) for item in years] == [ProjectionYear] * 3
    assert years[1].noi_p50 == run.result.yearly[1].noi_p50
    outputs = simulation_outputs(run.result)
    assert isinstance(outputs, SimulationOutputs)
    assert outputs.p_dscr_ge_target_year2 == run.result.p_dscr_at_least_target["year_2"]
    assert outputs.label == SIMULATION_LABEL and outputs.dscr_target == run.result.dscr_target.value
    short = simulate("car_wash", METRO, car_wash_site(), basic_plan(), basic_loan(), n=50, months=24)
    with pytest.raises(ValueError, match="36 months"):
        projection_years(short)


def test_add_months() -> None:
    from datetime import date

    assert add_months(date(2027, 11, 1), 0) == "2027-11"
    assert add_months(date(2027, 11, 1), 2) == "2028-01"


def test_siteiq_score_feeds_the_simulator() -> None:
    # Contract with S2: a real SiteIQ SiteScore on the SYNTHETIC metro is accepted as the site input, and its demand
    # range, coverage score and competition percentile reach the simulation.
    from uwcopilot.studio.data.fabric import get_fabric
    from uwcopilot.studio.feasibility import as_site_demand
    from uwcopilot.studio.siteiq import score_site

    fabric = get_fabric(METRO)
    tract = fabric.tract(fabric.tract_ids()[0])
    score = score_site("laundromat", METRO, tract.lat, tract.lon)
    site = as_site_demand(score)
    assert (site.low, site.mode, site.high, site.unit) == (
        score.demand.low,
        score.demand.mode,
        score.demand.high,
        score.demand.unit,
    )
    assert site.coverage_score == score.coverage_score
    competition = [factor for factor in score.factors if factor.factor == "competition"]
    if competition and competition[0].percentile is not None:
        assert site.competition_percentile == pytest.approx(competition[0].percentile / 100.0)
    plan = basic_plan(capex=400_000.0, owner_equity=200_000.0)
    result = simulate("laundromat", METRO, score, plan, basic_loan(amount=300_000.0), n=100, with_tornado=False)
    mature = next(item for item in result.distributions if item.name == "mature_demand")
    assert (mature.low, mature.mode, mature.high) == (site.low, site.mode, site.high)
    assert result.site_demand == site
