"""config/studio/levers.yaml and levers.py: the allow-list, bounds, grids and effort (Studio S5)."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from uwcopilot.agent.policy_startup import STARTUP_MAX_TERM_MONTHS, STARTUP_MIN_AMOUNT, STARTUP_MIN_TERM_MONTHS
from uwcopilot.studio.path_to_approval.levers import (
    LEVER_NAMES,
    LEVERS_FILE,
    LeverBounds,
    effort,
    grid,
    load_levers,
    resolve_bounds,
    round_inside,
)


def write_levers(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "levers.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def config_data() -> dict:
    return yaml.safe_load(LEVERS_FILE.read_text(encoding="utf-8"))


def test_config_lists_exactly_the_allow_list_as_design_choices() -> None:
    data = config_data()
    assert data["status"] == "DESIGN_CHOICE"
    assert set(load_levers()) == set(LEVER_NAMES)
    assert set(LEVER_NAMES) == {
        "owner_equity",
        "loan_amount",
        "build_out_cost",
        "working_capital_reserve",
        "price",
        "days_open_per_week",
        "loan_term_months",
        "site",
    }


@pytest.mark.parametrize("fact", ["credit_score", "experience_months", "other_debt", "mature_demand", "note_rate"])
def test_config_cannot_turn_a_fact_into_a_lever(tmp_path: Path, fact: str) -> None:
    data = config_data()
    data["levers"][fact] = dict(data["levers"]["price"])
    with pytest.raises(ValidationError, match="not a plan-choice lever"):
        load_levers(write_levers(tmp_path, data))


def test_config_cannot_change_a_levers_kind_or_balancing(tmp_path: Path) -> None:
    data = config_data()
    data["levers"]["owner_equity"]["kind"] = "price"
    with pytest.raises(ValidationError, match="must have kind"):
        load_levers(write_levers(tmp_path, data))
    data = config_data()
    data["levers"]["loan_amount"]["balanced_by"] = "owner_equity"
    with pytest.raises(ValidationError, match="balanced by"):
        load_levers(write_levers(tmp_path, data))


def test_config_status_must_be_design_choice(tmp_path: Path) -> None:
    data = config_data()
    data["status"] = "SOURCED"
    with pytest.raises(ValidationError):
        load_levers(write_levers(tmp_path, data))


def test_term_bounds_are_the_programs_term_limits() -> None:
    term = load_levers()["loan_term_months"]
    assert min(term.options or []) == STARTUP_MIN_TERM_MONTHS
    assert max(term.options or []) == STARTUP_MAX_TERM_MONTHS


def test_resolve_bounds_bases() -> None:
    levers = load_levers()
    # owner equity: current .. current + 20% of the project cost
    assert resolve_bounds("owner_equity", levers["owner_equity"], 100_000.0, 500_000.0) == (100_000.0, 200_000.0)
    # build-out: 15% saving .. current
    low, high = resolve_bounds("build_out_cost", levers["build_out_cost"], 400_000.0, 500_000.0)
    assert (round(low, 2), high) == (340_000.0, 400_000.0)
    # days: absolute 5..7, widened to include a current value outside them
    assert resolve_bounds("days_open_per_week", levers["days_open_per_week"], 4.0, 1.0) == (4.0, 7.0)


def test_loan_never_goes_below_the_program_minimum_unless_it_already_is() -> None:
    config = load_levers()["loan_amount"]
    low, _ = resolve_bounds("loan_amount", config, 60_000.0, 400_000.0)
    assert low == STARTUP_MIN_AMOUNT
    low, _ = resolve_bounds("loan_amount", config, 30_000.0, 400_000.0)
    assert low == 30_000.0


def test_founder_bounds_win_and_must_include_the_current_value() -> None:
    config = load_levers()["owner_equity"]
    assert resolve_bounds("owner_equity", config, 100_000.0, 500_000.0, LeverBounds(high=120_000.0)) == (
        100_000.0,
        120_000.0,
    )
    with pytest.raises(ValueError, match="below the plan's current value"):
        resolve_bounds("owner_equity", config, 100_000.0, 500_000.0, LeverBounds(high=90_000.0))
    with pytest.raises(ValueError, match="above the plan's current value"):
        resolve_bounds("owner_equity", config, 100_000.0, 500_000.0, LeverBounds(low=110_000.0))
    with pytest.raises(ValidationError):
        LeverBounds(low=5.0, high=1.0)


def test_grids_exclude_the_current_value_and_respect_steps() -> None:
    levers = load_levers()
    term = grid(levers["loan_term_months"], 84.0, 60.0, 120.0, 4)
    assert term == [60.0, 72.0, 96.0, 108.0, 120.0]
    days = grid(levers["days_open_per_week"], 6.0, 5.0, 7.0, 4)
    assert days == [5.0, 7.0]
    amounts = grid(levers["owner_equity"], 100_000.0, 100_000.0, 200_000.0, 4)
    assert amounts == [125_000.0, 150_000.0, 175_000.0, 200_000.0]
    assert all(value % 500 == 0 for value in amounts)
    price = grid(levers["price"], 14.0, 11.9, 16.1, 2)
    assert all(abs(value / 0.25 - round(value / 0.25)) < 1e-9 for value in price)
    assert 14.0 not in price and min(price) >= 11.9 and max(price) <= 16.1


def test_round_inside_never_leaves_the_bounds() -> None:
    assert round_inside(16.1, 0.25, 11.9, 16.1) == 16.0
    assert round_inside(11.9, 0.25, 11.9, 16.1) == 12.0
    assert round_inside(100.3, 500.0, 100.0, 100.6) == 100.3


def test_effort_is_weight_times_size() -> None:
    levers = load_levers()
    assert effort(levers["owner_equity"], 100_000.0, 150_000.0, 500_000.0) == pytest.approx(2.0 * 0.1)
    assert effort(levers["loan_term_months"], 84.0, 120.0, 500_000.0) == pytest.approx(0.5 * 36 / 84)
    assert effort(levers["site"], "a", "b", 500_000.0) == levers["site"].effort_weight
