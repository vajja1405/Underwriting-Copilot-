"""Guardrails (Studio S5): only plan choices move; every attempt to move a fact fails."""

import pytest
from path_helpers import FAST, car_wash_case, north_lot, prepared, search

from uwcopilot.studio.data.fabric import get_fabric
from uwcopilot.studio.path_to_approval import (
    Change,
    FounderFacts,
    GuardrailViolation,
    LeverError,
    OtherDebt,
    OutOfBounds,
    guardrails,
)
from uwcopilot.studio.path_to_approval.guardrails import NOT_A_LEVER, apply_changes
from uwcopilot.studio.path_to_approval.levers import BALANCED_BY
from uwcopilot.studio.path_to_approval.models import VERIFIABLE_FIELDS
from uwcopilot.studio.path_to_approval.space import LEVER_WRITES


def sources_minus_uses(case) -> float:
    """equity + loan + other debt - build-out - reserve (reserve = what is left), so it checks equity+loan >= capex."""
    return case.plan.owner_equity + case.loan.amount + case.facts.other_debt_amount - case.plan.capex


@pytest.mark.parametrize("name", sorted(NOT_A_LEVER))
def test_moving_a_fact_or_assumption_fails(name: str) -> None:
    ready = prepared(car_wash_case())
    with pytest.raises(GuardrailViolation, match="is not a plan choice"):
        ready.evaluator.evaluate([Change(lever=name, value=1.0)])


def test_credit_score_and_experience_cannot_be_raised() -> None:
    facts = FounderFacts(credit_score=640, experience_months=6)
    ready = prepared(car_wash_case(facts=facts))
    for name, value in (("credit_score", 760.0), ("experience_months", 36.0)):
        with pytest.raises(GuardrailViolation, match="fact"):
            apply_changes(ready.space, [Change(lever=name, value=value)])


def test_an_unknown_name_is_refused_too() -> None:
    ready = prepared(car_wash_case())
    with pytest.raises(GuardrailViolation, match="allow-list"):
        apply_changes(ready.space, [Change(lever="hide_the_seller_note", value=0.0)])


def test_a_verified_figure_never_moves() -> None:
    facts = FounderFacts(verified_fields=["loan.amount"])
    ready = prepared(car_wash_case(facts=facts))
    for lever in ("owner_equity", "loan_amount", "build_out_cost"):
        assert "verified" in ready.space.not_available[lever]
    with pytest.raises(GuardrailViolation, match="verified"):
        apply_changes(ready.space, [Change(lever="loan_amount", value=1_000_000.0)])
    result = search(car_wash_case(facts=facts))
    for item in result.change_sets + ([result.closest] if result.closest else []):
        assert item.applied_plan.loan.amount == 1_100_000.0


def test_every_verifiable_field_is_written_by_some_lever() -> None:
    written = {path for paths in LEVER_WRITES.values() for path in paths}
    assert set(VERIFIABLE_FIELDS) <= written | {"site"}
    with pytest.raises(ValueError, match="unknown verified field"):
        FounderFacts(verified_fields=["facts.credit_score"])


def test_other_debt_is_never_hidden_or_reduced() -> None:
    debt = OtherDebt(name="seller note", amount=50_000.0, monthly_payment=1_000.0)
    case = car_wash_case(facts=FounderFacts(other_debt=[debt]))
    result = search(case, [north_lot()])
    assert result.before.annual_debt_service > search(car_wash_case()).before.annual_debt_service
    for item in result.change_sets + ([result.closest] if result.closest else []):
        assert item.applied_plan.facts == case.facts
        assert item.plan_after.other_debt_amount == 50_000.0
        assert "1 listed" in item.unchanged


def test_a_tampered_change_cannot_slip_a_fact_through(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = prepared(car_wash_case(facts=FounderFacts(credit_score=650)))
    real_changed_plan = guardrails.changed_plan

    def also_edit_credit(space, changes, amounts):
        case = real_changed_plan(space, changes, amounts)
        return case.model_copy(update={"facts": FounderFacts(credit_score=800)})

    monkeypatch.setattr(guardrails, "changed_plan", also_edit_credit)
    with pytest.raises(GuardrailViolation, match="facts"):
        apply_changes(ready.space, [Change(lever="loan_term_months", value=120.0)])


def test_a_tampered_change_cannot_edit_the_site_demand(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = prepared(car_wash_case())
    real_changed_plan = guardrails.changed_plan

    def also_boost_demand(space, changes, amounts):
        case = real_changed_plan(space, changes, amounts)
        site = case.site.model_copy(update={"mode": case.site.mode * 2, "high": case.site.high * 2})
        return case.model_copy(update={"site": site})

    monkeypatch.setattr(guardrails, "changed_plan", also_boost_demand)
    with pytest.raises(GuardrailViolation, match="site"):
        apply_changes(ready.space, [Change(lever="price", value=15.0)])


def test_a_candidate_in_another_metro_is_refused() -> None:
    with pytest.raises(GuardrailViolation, match="own metro"):
        search(car_wash_case(), [north_lot(metro_id="houston_tx")], fabric=get_fabric("synthetic_metro"))


def test_values_outside_the_bounds_and_repeated_levers_are_refused() -> None:
    ready = prepared(car_wash_case())
    with pytest.raises(OutOfBounds):
        apply_changes(ready.space, [Change(lever="owner_equity", value=10_000_000.0)])
    with pytest.raises(OutOfBounds):
        apply_changes(ready.space, [Change(lever="loan_term_months", value=360.0)])
    with pytest.raises(LeverError, match="once"):
        apply_changes(ready.space, [Change(lever="price", value=15.0), Change(lever="price", value=13.0)])
    with pytest.raises(LeverError, match="candidate"):
        apply_changes(ready.space, [Change(lever="site", value="not a candidate")])


def test_locked_and_inapplicable_levers_are_not_available() -> None:
    ready = prepared(car_wash_case(), locked=["price"])
    assert ready.space.not_available["price"] == "locked by you"
    assert "staffing plan" in ready.space.not_available["days_open_per_week"]
    assert "candidate" in ready.space.not_available["site"]
    with pytest.raises(LeverError, match="locked"):
        apply_changes(ready.space, [Change(lever="price", value=15.0)])
    no_quote = prepared(car_wash_case(plan_changes={"capex": None, "owner_equity": 1_000_000.0}))
    assert "ASSUMPTION" in no_quote.space.not_available["build_out_cost"]


@pytest.mark.parametrize(
    ("lever", "value"),
    [
        ("owner_equity", 650_000.0),
        ("loan_amount", 1_050_000.0),
        ("build_out_cost", 1_400_000.0),
        ("working_capital_reserve", 250_000.0),
    ],
)
def test_dollar_levers_keep_sources_equal_to_uses(lever: str, value: float) -> None:
    ready = prepared(car_wash_case())
    before = ready.space.amounts
    after = apply_changes(ready.space, [Change(lever=lever, value=value)])
    amounts = ready.space.plan_amounts(after)
    sources = amounts.owner_equity + amounts.loan_amount + amounts.other_debt_amount
    assert sources == pytest.approx(amounts.build_out_cost + amounts.cash_reserve_at_opening)
    if lever == "owner_equity":
        assert after.loan.amount == before.loan - 50_000.0
    if lever == "build_out_cost":
        assert after.loan.amount == before.loan - 100_000.0
    if lever == "working_capital_reserve":
        assert after.plan.owner_equity == before.equity + 50_000.0


@pytest.mark.parametrize("loan", [899_999.75, 899_999.755, 899_999.76, 899_999.765, 899_999.77])
def test_a_loan_cut_to_a_zero_reserve_never_trips_the_simulators_cash_check(loan: float) -> None:
    """Regression: amounts were rounded to the cent AFTER the guardrail check, so a zero reserve could be a cent short
    and the simulator raised a plain ValueError. Now the change is either refused (OutOfBounds) or simulated."""
    case = car_wash_case(
        plan_changes={"owner_equity": 600_000.37, "capex": 1_500_000.13},
        loan_changes={"amount": 1_100_000.41},
    )
    ready = prepared(case)
    change = [Change(lever="loan_amount", value=loan)]
    try:
        outcome = ready.evaluator.evaluate(change)
    except OutOfBounds:
        return
    after = apply_changes(ready.space, change)
    assert sources_minus_uses(after) >= 0
    assert 0 <= outcome.p_cash_negative_within_months <= 1


def test_a_reserve_cut_would_lower_owner_equity_below_its_bounds() -> None:
    """The reserve lever spends owner equity, so with the default bounds it can only add the founder's own cash."""
    ready = prepared(car_wash_case())
    with pytest.raises(OutOfBounds, match="owner_equity"):
        apply_changes(ready.space, [Change(lever="working_capital_reserve", value=100_000.0)])
    borrow_less = apply_changes(ready.space, [Change(lever="loan_amount", value=1_050_000.0)])
    assert ready.space.plan_amounts(borrow_less).cash_reserve_at_opening == 150_000.0


def test_every_change_set_only_edits_its_own_levers_fields() -> None:
    result = search(car_wash_case(), [north_lot()])
    assert result.change_sets
    for item in result.change_sets:
        allowed = {path for change in item.changes for path in LEVER_WRITES[change.lever]}
        for change in item.changes:
            assert set(change.fields_changed) <= allowed
        assert item.applied_plan.facts == result.change_sets[0].applied_plan.facts
        assert item.applied_plan.plan.assumption_overrides == {}


def test_guardrail_rules_are_listed_on_every_result() -> None:
    result = search(car_wash_case(), settings=FAST)
    assert any("Facts never move" in rule for rule in result.guardrails)
    assert any("not an approval" in rule for rule in result.guardrails)


AMOUNT_OF = {
    "owner_equity": lambda amounts: amounts.owner_equity,
    "loan_amount": lambda amounts: amounts.loan_amount,
    "build_out_cost": lambda amounts: amounts.build_out_cost,
    "working_capital_reserve": lambda amounts: amounts.cash_reserve_at_opening,
}


@pytest.mark.parametrize("locked", sorted(AMOUNT_OF))
def test_a_locked_dollar_amount_never_moves_through_another_lever(locked: str) -> None:
    """Regression: a lock only took the lever itself off the list, so the amount still moved through the levers
    balanced against it (with owner equity locked, the top change-set was "keep $127,500 more cash in reserve,
    funded by your own cash": equity $600,000 -> $727,500). A lock now freezes the amount, like a bound would."""
    case = car_wash_case(mode=380.0)
    result = search(case, [north_lot(380.0)], locked=[locked])
    before = AMOUNT_OF[locked](result.plan_before)
    items = result.change_sets + ([result.closest] if result.closest else [])
    assert items
    for item in items:
        assert AMOUNT_OF[locked](item.plan_after) == before
        assert locked not in {change.lever for change in item.changes}
    ready = prepared(case, [north_lot(380.0)], locked=[locked])
    assert ready.space.bounds[locked] == (ready.space.amounts.value_of(locked),) * 2
    for lever, absorbed_by in BALANCED_BY.items():
        if absorbed_by == locked:
            assert "which you locked" in ready.space.not_available[lever]


def test_a_lock_is_enforced_even_for_a_change_set_built_by_hand() -> None:
    """The reserve lever is not available with owner equity locked, and any amounts that would move the locked
    equity are refused by the bounds check, whichever lever produced them."""
    ready = prepared(car_wash_case(), locked=["owner_equity"])
    with pytest.raises(LeverError, match="which you locked"):
        apply_changes(ready.space, [Change(lever="working_capital_reserve", value=300_000.0)])
    moved_equity = guardrails.Amounts(equity=650_000.0, loan=1_050_000.0, other=0.0, capex=1_500_000.0)
    with pytest.raises(OutOfBounds, match="owner_equity"):
        guardrails.check_amounts(ready.space, moved_equity)
