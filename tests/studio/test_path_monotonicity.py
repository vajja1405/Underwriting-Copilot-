"""Monotonicity sanity (Studio S5): under common random numbers, a lever moved further in its helpful direction never
makes a probability worse, for the levers where that must hold exactly (search.py's pair heuristic relies on it).

- more owner equity (borrow that much less): lower debt service, same cash at opening;
- a longer loan term: lower payments;
- a lower build-out cost (borrow that much less): lower debt service, more cash after the overrun;
- a bigger cash reserve (founder's own cash): more cash, the same DSCR.
"""

import pytest
from path_helpers import car_wash_case, prepared

from uwcopilot.studio.path_to_approval import Change

LEVERS_UP = ("owner_equity", "loan_term_months", "working_capital_reserve")


def outcomes_along(lever: str, values: list[float]):
    ready = prepared(car_wash_case())
    return [ready.evaluator.evaluate([Change(lever=lever, value=value)]) for value in values]


def helpful_values(lever: str) -> list[float]:
    ready = prepared(car_wash_case())
    current = float(ready.space.current[lever])
    values = [float(value) for value in ready.space.grids[lever]]
    if lever == "build_out_cost":
        return [current] + sorted((value for value in values if value < current), reverse=True)
    return [current] + sorted(value for value in values if value > current)


@pytest.mark.parametrize("lever", [*LEVERS_UP, "build_out_cost"])
def test_probabilities_never_get_worse_along_a_helpful_lever(lever: str) -> None:
    values = helpful_values(lever)
    assert len(values) >= 3
    ready = prepared(car_wash_case())
    outcomes = [ready.evaluator.evaluate([]) if index == 0 else None for index in range(len(values))]
    for index, value in enumerate(values[1:], start=1):
        outcomes[index] = ready.evaluator.evaluate([Change(lever=lever, value=value)])
    for before, after in zip(outcomes, outcomes[1:], strict=False):
        assert after.p_dscr_at_least_target_year2 >= before.p_dscr_at_least_target_year2
        assert after.p_stressed_dscr_at_least_min_year2 >= before.p_stressed_dscr_at_least_min_year2
        assert after.p_cash_negative_within_months <= before.p_cash_negative_within_months
        assert after.shortfall <= before.shortfall + 1e-12


def test_a_bigger_reserve_moves_cash_only() -> None:
    values = helpful_values("working_capital_reserve")
    outcomes = outcomes_along("working_capital_reserve", values[1:])
    base = prepared(car_wash_case()).evaluator.evaluate([])
    for outcome in outcomes:
        assert outcome.p_dscr_at_least_target_year2 == base.p_dscr_at_least_target_year2
        assert outcome.p_stressed_dscr_at_least_min_year2 == base.p_stressed_dscr_at_least_min_year2
    assert outcomes[-1].p_cash_negative_within_months < base.p_cash_negative_within_months


def test_borrowing_less_trades_cash_for_coverage() -> None:
    """The loan lever is NOT monotone in every target: less debt helps DSCR but leaves less cash at opening."""
    ready = prepared(car_wash_case())
    base = ready.evaluator.evaluate([])
    less = ready.evaluator.evaluate([Change(lever="loan_amount", value=1_000_000.0)])
    assert less.p_dscr_at_least_target_year2 >= base.p_dscr_at_least_target_year2
    assert less.p_cash_negative_within_months >= base.p_cash_negative_within_months
