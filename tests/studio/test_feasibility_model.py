"""The monthly path model against hand-worked numbers, and the stress levers."""

import numpy as np
import pytest
from feasibility_helpers import COST_RATIOS, basic_loan, basic_plan, deterministic_setup, setup_for

from uwcopilot.studio.feasibility import RangeOverride, SiteDemand
from uwcopilot.studio.feasibility.loan import monthly_payment
from uwcopilot.studio.feasibility.model import (
    DAYS_PER_MONTH,
    StressLevers,
    draw,
    input_values,
    run_paths,
    simulate_paths,
    single_point_run,
)
from uwcopilot.studio.profiles import load_profile


def run_deterministic(n: int = 3, levers: StressLevers | None = None, **overrides: float):
    setup = deterministic_setup(**overrides)
    draws = draw(setup, n, np.random.default_rng(0))
    values = input_values(setup, draws)
    return setup, run_paths(setup, values, draws, levers or StressLevers())


def test_hand_worked_first_month_and_month_fourteen() -> None:
    setup, paths = run_deterministic()
    season = load_profile("car_wash").seasonality.values
    cost_share = sum(COST_RATIOS.values())
    labor = 1000 * 16 * 1.1
    # Month 1 (January, t = 0): demand 200 x ramp 0.5 x seasonality, 7 days a week.
    revenue_0 = 200 * 0.5 * season[0] * DAYS_PER_MONTH * 14.0
    noi_0 = revenue_0 * (1 - cost_share) - labor - 9000
    # Month 14 (February of year 2, t = 13): ramp is over, rent has escalated once.
    revenue_13 = 200 * 1.0 * season[1] * DAYS_PER_MONTH * 14.0
    noi_13 = revenue_13 * (1 - cost_share) - labor - 9000 * 1.03
    payment = monthly_payment(1_300_000, 9.0, 120)
    assert paths.revenue[:, 0] == pytest.approx([revenue_0] * 3)
    assert paths.noi[:, 0] == pytest.approx([noi_0] * 3)
    assert paths.revenue[0, 13] == pytest.approx(revenue_13)
    assert paths.noi[0, 13] == pytest.approx(noi_13)
    assert paths.debt_service[0] == pytest.approx([payment] * 36)
    # Ramp after 5 months: 0.5 + 0.5 x 5/10.
    assert paths.revenue[0, 5] == pytest.approx(200 * 0.75 * season[5] * DAYS_PER_MONTH * 14.0)
    starting_cash = 700_000 + 1_300_000 - 1_800_000 * 1.1
    assert paths.starting_cash == pytest.approx([starting_cash] * 3)
    assert paths.cash[0, 0] == pytest.approx(starting_cash + noi_0 - payment - 500.0)
    assert paths.cash[0, -1] == pytest.approx(starting_cash + (paths.net_income[0] - 500.0).sum())


def test_capacity_caps_daily_demand() -> None:
    setup = deterministic_setup()
    draws = draw(setup, 1, np.random.default_rng(0))
    values = input_values(setup, draws)
    uncapped = run_paths(setup, values, draws)
    values["capacity"] = np.array([60.0])
    capped = run_paths(setup, values, draws)
    assert capped.volume[0, 20] == pytest.approx(60.0 * DAYS_PER_MONTH)
    assert uncapped.volume[0, 20] > capped.volume[0, 20]


def test_competitor_entry_cuts_demand_from_its_month() -> None:
    _, base = run_deterministic(n=1)
    _, entered = run_deterministic(n=1, competitor_entry_annual_probability=1.0, competitor_entry_demand_loss=0.2)
    assert entered.competitor_entry_month[0] == 0
    assert entered.revenue[0] == pytest.approx(base.revenue[0] * 0.8)


def test_one_off_shock_hits_one_month() -> None:
    _, base = run_deterministic(n=1)
    _, hit = run_deterministic(n=1, one_off_shock_annual_probability=1.0)
    month = hit.one_off_month[0]
    assert month == 0
    assert hit.revenue[0, 0] == pytest.approx(base.revenue[0, 0] * 0.7)
    repair = 0.01 * 1_800_000
    lost_margin = base.revenue[0, 0] * 0.3 * (1 - sum(COST_RATIOS.values()))
    assert hit.noi[0, 0] == pytest.approx(base.noi[0, 0] - lost_margin - repair)
    assert hit.revenue[0, 1:] == pytest.approx(base.revenue[0, 1:])


def test_rate_shock_raises_payments_on_a_variable_loan() -> None:
    _, shocked = run_deterministic(n=1, rate_shock_annual_probability=1.0, rate_shock_size_pct_points=2.0)
    assert shocked.rate_shock_month[0] == 0
    assert shocked.debt_service[0, 0] == pytest.approx(monthly_payment(1_300_000, 11.0, 120))


def test_revenue_stress_lowers_noi_by_the_same_dollars() -> None:
    _, base = run_deterministic(n=2)
    _, stressed = run_deterministic(n=2, levers=StressLevers("revenue_stress", revenue_drop_share=0.2))
    assert stressed.revenue == pytest.approx(base.revenue * 0.8)
    assert stressed.noi == pytest.approx(base.noi - 0.2 * base.revenue)


def test_rate_stress_adds_points_to_the_whole_loan() -> None:
    _, stressed = run_deterministic(n=1, levers=StressLevers("rate_stress", rate_add_pct=2.0))
    assert stressed.debt_service[0, 0] == pytest.approx(monthly_payment(1_300_000, 11.0, 120))


def test_per_month_units_do_not_multiply_by_days() -> None:
    site = SiteDemand(low=20, mode=20, high=20, unit="booked nights/month", status="SYNTHETIC")
    pins = {"ramp_starting_share": RangeOverride(low=1.0, mode=1.0, high=1.0)}
    plan = basic_plan(capex=30_000.0, owner_equity=20_000.0, capacity=25.0, assumption_overrides=pins)
    setup = setup_for("short_term_rental", site=site, plan=plan, loan=basic_loan(amount=20_000.0, term_months=60))
    run = single_point_run(setup)
    season = load_profile("short_term_rental").seasonality.values
    assert run.volume[0, 0] == pytest.approx(min(20 * season[2], 25))  # opens in March, no days multiplier


def test_same_seed_same_paths_and_noise_has_mean_one() -> None:
    setup = setup_for()
    first = simulate_paths(setup, 400, 7)[2]
    second = simulate_paths(setup, 400, 7)[2]
    third = simulate_paths(setup, 400, 8)[2]
    assert np.array_equal(first.cash, second.cash)
    assert not np.array_equal(first.cash, third.cash)
    z = np.random.default_rng(3).standard_normal(200_000)
    sd = 0.15
    assert np.mean(np.exp(sd * z - 0.5 * sd**2)) == pytest.approx(1.0, abs=0.002)


def test_single_point_run_has_no_shocks_or_noise() -> None:
    setup = setup_for()
    run = single_point_run(setup)
    assert run.competitor_entry_month[0] == -1 and run.one_off_month[0] == -1 and run.rate_shock_month[0] == -1
    assert run.n_paths == 1
