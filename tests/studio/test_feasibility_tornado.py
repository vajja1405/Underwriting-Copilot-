"""Tornado: one input at a time between its P10 and P90, every other draw held fixed."""

import numpy as np
import pytest
from feasibility_helpers import basic_loan, basic_plan, car_wash_site, setup_for

from uwcopilot.studio.feasibility.model import simulate_paths
from uwcopilot.studio.feasibility.tornado import TOP_DRIVERS, tornado, year2_net_income_p50


@pytest.fixture(scope="module")
def chart():
    # The founder states the price at the profile's reference price, so the elasticity cannot matter.
    setup = setup_for(site=car_wash_site(), plan=basic_plan(price=14.0), loan=basic_loan(rate_type="fixed"))
    draws, values, paths = simulate_paths(setup, 300, 5)
    return setup, tornado(setup, values, draws, 1.25, paths)


def test_bars_sorted_and_top_drivers(chart) -> None:
    setup, result = chart
    swings = [bar.swing for bar in result.bars]
    assert swings == sorted(swings, reverse=True)
    assert result.top_drivers == [bar.name for bar in result.bars[:TOP_DRIVERS]]
    assert [bar.rank for bar in result.bars] == list(range(1, len(result.bars) + 1))
    for bar in result.bars:
        assert bar.swing == pytest.approx(abs(bar.metric_at_p90 - bar.metric_at_p10))
        item = setup.distributions[bar.name]
        assert bar.input_p10 == pytest.approx(item.percentile(0.1))
        assert bar.input_p90 == pytest.approx(item.percentile(0.9))


def test_fixed_inputs_are_not_swung(chart) -> None:
    setup, result = chart
    names = {bar.name for bar in result.bars}
    assert "price" not in names  # typed by the founder
    assert names == {name for name, item in setup.distributions.items() if item.varies}


def test_holding_the_seed_isolates_one_input(chart) -> None:
    _, result = chart
    bars = {bar.name: bar for bar in result.bars}
    # Price equals the reference price, so elasticity has no effect at all: the swing is exactly zero.
    assert bars["price_elasticity"].swing == 0.0
    assert bars["mature_demand"].metric_at_p90 > bars["mature_demand"].metric_at_p10
    assert bars["monthly_rent"].metric_at_p90 < bars["monthly_rent"].metric_at_p10


def test_base_metric_is_the_year2_median(chart) -> None:
    setup, result = chart
    _, _, paths = simulate_paths(setup, 300, 5)
    assert result.base_metric == pytest.approx(year2_net_income_p50(paths))
    assert np.isfinite(result.base_p_dscr_year2)
