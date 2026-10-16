"""find_paths (Studio S5): single-lever then pair search, ranking, explanations, quick mode, infeasible plans."""

import re

import pytest
from path_helpers import FAST, car_wash_case, north_lot, prepared, search

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.data.fabric_snapshot import SnapshotFabric
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.path_to_approval import (
    FULL_SETTINGS,
    QUICK_SETTINGS,
    Change,
    EvaluationCache,
    LeverBounds,
    OutOfBounds,
    SearchSettings,
    custom_target,
    find_paths,
)
from uwcopilot.studio.path_to_approval.exhaustive import exhaustive_search
from uwcopilot.studio.path_to_approval.explain import NOT_AN_APPROVAL, OWNER_CASH_CAVEAT, dollars
from uwcopilot.studio.path_to_approval.guardrails import apply_changes
from uwcopilot.studio.path_to_approval.models import PlanAmounts
from uwcopilot.studio.path_to_approval.program import program_checks, refusals
from uwcopilot.studio.path_to_approval.search import Tried, change_set, clear_caches
from uwcopilot.studio.path_to_approval.targets import default_target

# Sentence openings that would turn a simulated scenario into an instruction (Studio rule C: no advice claims).
IMPERATIVE_OPENINGS = (
    "Borrow",
    "Put",
    "Keep",
    "Charge",
    "Open",
    "Ask",
    "Use",
    "Bring",
    "Raise",
    "Spend",
    "Lower",
    "Choose",
    "Switch",
    "Cut",
    "Add",
    "Increase",
    "Reduce",
)

VOLATILE = {"search": {"runtime_ms", "cache_hits", "evaluations"}}


def test_finds_the_smallest_change_sets_first() -> None:
    result = search(car_wash_case(), [north_lot()])
    assert result.status == "paths_found"
    assert not result.before.meets_target
    assert 1 <= len(result.change_sets) <= 3
    keys = [(item.n_levers, item.effort) for item in result.change_sets]
    assert keys == sorted(keys)
    assert [item.rank for item in result.change_sets] == list(range(1, len(result.change_sets) + 1))
    for item in result.change_sets:
        assert item.meets_target and item.after.meets_target
        assert item.before == result.before
        assert item.n_levers == len(item.changes) <= 2
        assert item.after.n_paths == FAST.n_paths


def test_singles_come_before_pairs_and_pairs_only_fill_up() -> None:
    result = search(car_wash_case(), [north_lot()])
    sizes = [item.n_levers for item in result.change_sets]
    assert sizes == sorted(sizes)
    if result.search.pairs_screened:
        assert sizes.count(1) < FAST.max_results


def test_explanation_is_plain_labeled_and_never_promises_approval() -> None:
    result = search(car_wash_case(), [north_lot()])
    for item in result.change_sets:
        assert SIMULATION_LABEL in item.explanation
        assert NOT_AN_APPROVAL in item.explanation
        assert "In this simulation all three targets would be met." in item.explanation
        assert "Unchanged: your credit score, your experience" in item.unchanged
        lowered = item.explanation.lower()
        assert "will be approved" not in lowered and "guarantee" not in lowered
    assert result.label == SIMULATION_LABEL
    assert result.disclaimer == DISCLAIMER
    assert NOT_AN_APPROVAL in result.message


def sentence_openings(text: str) -> list[str]:
    """The first word of every sentence."""
    return [sentence.split()[0] for sentence in re.split(r"(?<=[.:;])\s+", text) if sentence.split()]


def test_explanations_describe_a_scenario_not_an_instruction() -> None:
    """Regression: every change used to be an order ("Borrow $106,000 more ...", "Put in ..."), which reads as
    financial advice (Studio rule C). Now each change-set is a conditional scenario with its simulated result."""
    result = search(car_wash_case(), [north_lot()], target=custom_target(dscr_min=10.0, p_dscr_min=0.99))
    items = result.change_sets + ([result.closest] if result.closest else [])
    items += search(car_wash_case(), [north_lot()]).change_sets
    assert items
    for item in items:
        assert item.explanation.startswith("If you ")
        assert "simulated" in item.explanation and "In this simulation" in item.explanation
        openings = sentence_openings(item.explanation)
        assert not set(openings) & set(IMPERATIVE_OPENINGS), openings
    for message in (result.message, search(car_wash_case(), [north_lot()]).message):
        assert not set(sentence_openings(message)) & set(IMPERATIVE_OPENINGS)


def test_applied_plan_is_the_plan_with_the_change_applied() -> None:
    result = search(car_wash_case(), [north_lot()])
    top = result.change_sets[0]
    rerun = search(top.applied_plan, [north_lot()])
    assert rerun.before == top.after
    assert rerun.status == "already_meets_target"
    assert rerun.change_sets == [] and rerun.closest is None
    assert "already meets the three targets" in rerun.message


def test_same_seed_same_answer() -> None:
    first = search(car_wash_case(), [north_lot()])
    second = search(car_wash_case(), [north_lot()])
    assert first.model_dump(exclude=VOLATILE) == second.model_dump(exclude=VOLATILE)
    shared = EvaluationCache()
    warm_first = search(car_wash_case(), [north_lot()], cache=shared)
    warm_second = search(car_wash_case(), [north_lot()], cache=shared)
    assert warm_second.search.evaluations == 0 and warm_second.search.cache_hits > 0
    assert warm_first.change_sets == warm_second.change_sets


def test_another_seed_can_change_the_numbers_but_not_the_labels() -> None:
    other = search(car_wash_case(), [north_lot()], settings=FAST.model_copy(update={"seed": 7}))
    assert other.before.seed == 7
    assert other.label == SIMULATION_LABEL


def test_infeasible_target_gives_no_path_and_the_closest_result() -> None:
    impossible = custom_target(dscr_min=10.0, p_dscr_min=0.99)
    result = search(car_wash_case(), [north_lot()], target=impossible)
    assert result.status == "no_path_within_bounds"
    assert result.change_sets == []
    assert result.message.startswith("No path within your bounds")
    assert result.closest is not None and result.closest.rank == 0
    assert not result.closest.meets_target
    assert result.closest.after.shortfall < result.before.shortfall
    assert "these would still be missed: the year-2 DSCR target" in result.closest.explanation
    assert "The closest result is shown." in result.message


def test_no_path_message_does_not_claim_an_exhaustive_search() -> None:
    """Regression: "no change of one or two plan choices inside your bounds reaches all three targets" claimed more
    than a heuristic search over a grid can show, and it was also shown when the budget ran out."""
    impossible = custom_target(dscr_min=10.0, p_dscr_min=0.99)
    complete = search(car_wash_case(), [north_lot()], target=impossible)
    assert complete.search.search_complete
    assert "the search did not find" in complete.message and "not exhaustive" in complete.message
    assert "no change of one or two plan choices inside your bounds reaches" not in complete.message
    stopped = search(
        car_wash_case(mode=300.0), [north_lot(300.0)], settings=SearchSettings(n_paths=200, max_evaluations=3)
    )
    assert stopped.status == "no_path_within_bounds" and stopped.search.budget_exhausted
    assert stopped.message.startswith("No path within your bounds found yet: the search stopped early")


def test_every_lever_locked_gives_no_path_and_no_closest() -> None:
    every = ["owner_equity", "loan_amount", "build_out_cost", "working_capital_reserve", "price", "loan_term_months"]
    result = search(car_wash_case(), locked=every)
    assert result.status == "no_path_within_bounds"
    assert result.levers_considered == []
    assert result.closest is None
    assert any("No change within your bounds" in note for note in result.notes)


def test_founder_bounds_are_respected() -> None:
    bounds = {
        "owner_equity": LeverBounds(high=620_000.0),
        "loan_amount": LeverBounds(low=1_090_000.0, high=1_120_000.0),
    }
    result = search(car_wash_case(), bounds=bounds)
    for item in result.change_sets + ([result.closest] if result.closest else []):
        assert item.plan_after.owner_equity <= 620_000.0
        assert 1_090_000.0 <= item.plan_after.loan_amount <= 1_120_000.0 or any(
            change.lever in ("owner_equity", "build_out_cost") for change in item.changes
        )


def test_a_change_set_never_breaks_a_program_rule_the_plan_meets() -> None:
    """A plan just above the NSP-3.1 equity minimum (21.0%): borrowing more than about $85,000 extra would push the
    owner's share below 20%, so those loan values are refused even under a cash-only target that wants them."""
    case = car_wash_case(
        plan_changes={"owner_equity": 357_000.0, "capex": 1_500_000.0}, loan_changes={"amount": 1_343_000.0}
    )
    cash_only = custom_target(p_dscr_min=0.0, p_stressed_min=0.0, p_cash_negative_max=0.0)
    # Locking owner equity also takes out the reserve lever (it spends owner equity), so only the loan moves.
    others = ["owner_equity", "build_out_cost", "price", "loan_term_months"]
    result = search(case, target=cash_only, locked=others)
    assert [lever.name for lever in result.levers_considered] == ["loan_amount"]
    assert result.plan_before.checks.meets_equity_minimum
    assert result.search.blocked_by_program > 0
    for item in result.change_sets + ([result.closest] if result.closest else []):
        assert item.plan_after.checks.meets_equity_minimum
        assert item.plan_after.checks.equity_injection_pct >= 20.0


def test_quick_mode_keeps_the_full_paths_and_finds_what_full_finds() -> None:
    """Regression: quick mode once searched with 500 paths and re-checked with 1,000. For this plan the re-check
    dropped every change-set (each only just met the cash target with 500 paths), so the website would have said
    "no path" while full mode found two single-lever paths. Quick mode now keeps the 1,000 paths and the seed."""
    cache = EvaluationCache()
    full = find_paths(car_wash_case(), [north_lot()], mode="full", cache=cache)
    quick = find_paths(car_wash_case(), [north_lot()], mode="quick", cache=cache)
    assert quick.settings == QUICK_SETTINGS and QUICK_SETTINGS.confirm_paths is None
    assert QUICK_SETTINGS.n_paths == FULL_SETTINGS.n_paths and QUICK_SETTINGS.seed == FULL_SETTINGS.seed
    assert QUICK_SETTINGS.steps_per_side < FULL_SETTINGS.steps_per_side
    assert full.status == quick.status == "paths_found"
    assert quick.before == full.before
    assert quick.search.cache_hits > 0  # the same paths and seed, so quick reuses full's outcomes
    for item in quick.change_sets:
        assert item.after.n_paths == FULL_SETTINGS.n_paths and item.after.meets_target
    # The coarse grid is a subset of the fine one, so quick's smallest change is never smaller than full's.
    quick_top, full_top = quick.change_sets[0], full.change_sets[0]
    assert (quick_top.n_levers, quick_top.effort) >= (full_top.n_levers, full_top.effort)
    assert any(note.startswith("Quick mode: a coarser search") for note in quick.notes)


def test_a_search_with_fewer_paths_can_still_be_rechecked() -> None:
    """SearchSettings.confirm_paths: search with fewer paths, re-check what is returned with more."""
    settings = FAST.model_copy(update={"n_paths": 200, "confirm_paths": 300})
    result = search(car_wash_case(), [north_lot()], settings=settings)
    assert result.before.n_paths == 300
    for item in result.change_sets:
        assert item.after.n_paths == 300 and item.after.meets_target
    assert any(note.startswith("Searched with 200 simulated paths") for note in result.notes)


def test_budget_limits_the_search_and_says_so() -> None:
    tiny = SearchSettings(n_paths=200, steps_per_side=4, max_evaluations=3)
    result = search(car_wash_case(mode=300.0), [north_lot(300.0)], settings=tiny)
    assert result.search.budget_exhausted and not result.search.search_complete
    assert result.search.evaluations <= 4
    assert result.search.variants_evaluated == 3


def test_the_budget_does_not_depend_on_the_shared_cache() -> None:
    """Regression: the budget counted cache misses only, so repeating one request on a shared cache got further each
    time (the third run found paths the first two did not). Every evaluated variant now counts."""
    small = SearchSettings(n_paths=300, steps_per_side=4, max_evaluations=12)
    shared = EvaluationCache()
    runs = [search(car_wash_case(mode=300.0), [north_lot(300.0)], settings=small, cache=shared) for _ in range(3)]
    cold = search(car_wash_case(mode=300.0), [north_lot(300.0)], settings=small)
    assert runs[0].search.budget_exhausted
    for run in runs[1:] + [cold]:
        assert run.model_dump(exclude=VOLATILE) == runs[0].model_dump(exclude=VOLATILE)
    assert runs[2].search.evaluations == 0 and runs[2].search.variants_evaluated == runs[0].search.variants_evaluated


def test_a_pair_whose_screen_is_out_of_bounds_is_still_searched() -> None:
    """Regression: the pair screen tries both levers at their best single values. More owner equity and a lower
    build-out both cut the loan, so together they took it below its bound, the screen was refused and the pair was
    dropped, although smaller moves of both meet the target (a plan of the evaluation's own data had no path)."""
    case = car_wash_case(mode=360.0)
    target = custom_target(p_stressed_min=0.86, p_cash_negative_max=1.0)
    locked = ["working_capital_reserve", "price", "loan_term_months"]
    ready = prepared(case, target=target, locked=locked)
    assert ready.space.available == ("owner_equity", "build_out_cost")
    best_equity, best_build_out = max(ready.space.grids["owner_equity"]), min(ready.space.grids["build_out_cost"])
    with pytest.raises(OutOfBounds, match="loan_amount"):
        apply_changes(
            ready.space,
            [Change(lever="owner_equity", value=best_equity), Change(lever="build_out_cost", value=best_build_out)],
        )
    cache = EvaluationCache()
    result = search(case, target=target, locked=locked, cache=cache)
    reference = exhaustive_search(prepared(case, target=target, locked=locked, cache=cache)).best
    assert reference is not None and reference.n_levers == 2
    assert result.status == "paths_found"
    top = result.change_sets[0]
    assert {change.lever for change in top.changes} == {"owner_equity", "build_out_cost"}
    assert top.after.meets_target
    assert (top.n_levers, top.effort) == (reference.n_levers, pytest.approx(reference.effort, abs=1e-6))


@pytest.mark.parametrize("mode", [300.0, 420.0])
def test_heuristic_matches_brute_force_on_small_plans(mode: float) -> None:
    settings = SearchSettings(n_paths=200, steps_per_side=3, max_evaluations=5000)
    case, candidates = car_wash_case(mode=mode), [north_lot(mode)]
    cache = EvaluationCache()
    heuristic = search(case, candidates, settings=settings, cache=cache)
    reference = exhaustive_search(prepared(case, candidates, settings=settings, cache=cache))
    best = reference.best
    if best is None:
        assert heuristic.status == "no_path_within_bounds"
        return
    assert heuristic.status == "paths_found"
    top = heuristic.change_sets[0]
    assert top.n_levers == best.n_levers
    assert top.effort == pytest.approx(best.effort, abs=1e-6)


def test_real_metro_in_limited_mode_runs_and_says_so() -> None:
    """A real metro without a snapshot on disk (as in CI): limited mode, profile ranges, the founder's quoted rate."""
    fabric = SnapshotFabric(metro=load_metro("houston_tx"), snapshot=None)
    case = car_wash_case().model_copy(update={"metro_id": "houston_tx"})
    result = search(case, fabric=fabric)
    assert result.metro_mode == "limited"
    assert result.data_label == "PUBLIC"
    assert any("limited mode" in note for note in result.notes)
    assert result.status in ("paths_found", "no_path_within_bounds", "already_meets_target")


def test_a_fabric_for_another_metro_is_refused() -> None:
    fabric = SnapshotFabric(metro=load_metro("houston_tx"), snapshot=None)
    with pytest.raises(ValueError, match="data fabric is for houston_tx"):
        search(car_wash_case(), fabric=fabric)


def test_clear_caches_empties_the_shared_cache() -> None:
    find_paths(car_wash_case(), settings=FAST)
    clear_caches()
    from uwcopilot.studio.path_to_approval import SHARED_CACHE

    assert len(SHARED_CACHE) == 0


@pytest.mark.parametrize(
    "moves",
    [
        (("owner_equity", 650_000.0), ("loan_amount", 1_000_000.0)),
        (("owner_equity", 650_000.0), ("working_capital_reserve", 250_000.0)),
        (("build_out_cost", 1_400_000.0), ("loan_amount", 1_050_000.0)),
        (("loan_amount", 1_050_000.0), ("working_capital_reserve", 250_000.0)),
    ],
)
def test_two_dollar_levers_on_one_amount_show_the_real_totals(moves: tuple[tuple[str, float], ...]) -> None:
    """Regression: two dollar levers can move the same amount (more equity also lowers the loan). Each change must
    show the amount in the changed plan (what "apply this change" sends), not the value the lever had alone, plus
    its own move; the explanation's plan line shows the real totals."""
    ready = prepared(car_wash_case())
    changes = tuple(Change(lever=lever, value=value) for lever, value in moves)
    tried = Tried(changes, ready.evaluator.evaluate(changes), 0.0)
    item = change_set(ready.space, tried, ready.evaluator.evaluate(()), default_target(), 1)
    after = item.plan_after
    in_plan = {
        "owner_equity": after.owner_equity,
        "loan_amount": after.loan_amount,
        "build_out_cost": after.build_out_cost,
        "working_capital_reserve": after.cash_reserve_at_opening,
    }
    for applied, (lever, value) in zip(item.changes, moves, strict=True):
        assert applied.to_value == in_plan[lever]
        assert applied.own_change == value - float(ready.space.current[lever])
    assert after.loan_amount == item.applied_plan.loan.amount
    assert f"loan {dollars(1_100_000.0)} -> {dollars(after.loan_amount)}" in item.explanation


def test_a_loan_above_the_program_maximum_is_never_raised_further() -> None:
    """Regression: a check the plan already fails was only shown, so the search could push it further: for this plan
    (loan $1.1M, above the NSP-8.1 maximum of $500,000) the top change-set was "borrow $106,000 more", and its
    explanation said only that the targets were met. Now such a change-set is refused, and every change-set lists
    the program rules its changed plan still fails."""
    result = search(car_wash_case(), [north_lot()])
    assert not result.plan_before.checks.loan_within_limits
    assert result.search.blocked_by_program > 0
    items = result.change_sets + ([result.closest] if result.closest else [])
    assert items
    for item in items:
        assert item.plan_after.loan_amount <= result.plan_before.loan_amount
        assert "loan amount from $50,000 to $500,000 (NSP-8.1)" in item.program_rules_not_met
        assert "would still not meet these rules of the SYNTHETIC startup program" in item.explanation


def test_a_change_set_inside_every_program_rule_lists_none() -> None:
    """A plan inside every program rule: no change-set may break one, so none lists a rule it fails."""
    case = car_wash_case(
        mode=300.0,
        plan_changes={"owner_equity": 150_000.0, "capex": 500_000.0},
        loan_changes={"amount": 450_000.0},
    )
    result = search(case, target=custom_target(p_dscr_min=0.0, p_stressed_min=0.0, p_cash_negative_max=0.0))
    checks = result.plan_before.checks
    assert checks.meets_equity_minimum and checks.loan_within_limits and checks.term_within_limits
    items = result.change_sets + ([result.closest] if result.closest else [])
    assert items
    for item in items:
        assert item.program_rules_not_met == []
        assert "would still not meet these rules" not in item.explanation


def test_an_edited_stress_is_never_called_the_lenders() -> None:
    """Regression: an edited stress share (here 0%) was printed as "the lender's 0% revenue stress", and the
    lender's base-case NSP-4.2 ratio was computed with the edited share instead of the program's 20%."""
    edited = custom_target(stress_revenue_share=0.0, stressed_dscr_min=0.5)
    result = search(car_wash_case(), [north_lot()], target=edited)
    default = search(car_wash_case(), [north_lot()])
    assert result.before.stressed_dscr_base_case == default.before.stressed_dscr_base_case
    assert result.before.projected_dscr_base_case == default.before.projected_dscr_base_case
    items = result.change_sets + ([result.closest] if result.closest else [])
    assert items
    for item in items:
        assert "the lender's 0%" not in item.explanation
        assert "your edited revenue stress (a 0% revenue cut, not the lender's NSP-4.2 stress)" in item.explanation
        assert "NSP-4.2 with the program's 20% revenue stress" in item.explanation
    for item in default.change_sets:
        assert "under the lender's 20% revenue stress (NSP-4.2)" in item.explanation


def test_each_change_says_what_would_back_it_for_a_lender() -> None:
    """Regression: a search change replaced a founder's figure (a build-out quote, owner cash) with nothing saying
    the new figure is unbacked. Each change now carries evidence_needed, and the reserve lever (which spends the
    founder's own cash) carries the same caveat as the owner-equity lever."""
    ready = prepared(car_wash_case())
    base = ready.evaluator.evaluate(())
    for changes, phrase in (
        ((Change(lever="working_capital_reserve", value=285_000.0),), "bank statements that show $685,000"),
        ((Change(lever="owner_equity", value=650_000.0),), "bank statements that show $650,000"),
        ((Change(lever="build_out_cost", value=1_400_000.0),), "a written quote at or below $1,400,000"),
        ((Change(lever="loan_term_months", value=120.0),), "the lender decides the term"),
    ):
        tried = Tried(changes, ready.evaluator.evaluate(changes), 0.0)
        item = change_set(ready.space, tried, base, default_target(), 1)
        assert phrase in item.changes[0].evidence_needed
        assert item.evidence_needed == [item.changes[0].evidence_needed]
        assert "What would back the changed figures for a lender" in item.explanation
        assert "not quotes or verified figures" in item.explanation
        if changes[0].lever in ("working_capital_reserve", "owner_equity"):
            assert OWNER_CASH_CAVEAT in item.explanation


def test_mode_of_a_passed_fabric_is_computed_once() -> None:
    """Regression: the website passes its own fabric, and fabric.mode() (which reads every profile for a real metro)
    ran on every request. It is now kept per fabric object."""
    fabric = SnapshotFabric(metro=load_metro("houston_tx"), snapshot=None)
    real_mode = fabric.mode
    calls = []

    def counting_mode(profiles=None):
        calls.append(1)
        return real_mode(profiles)

    fabric.mode = counting_mode
    case = car_wash_case().model_copy(update={"metro_id": "houston_tx"})
    first = search(case, fabric=fabric)
    second = search(case, fabric=fabric)
    assert len(calls) == 1
    assert first.metro_mode == second.metro_mode == "limited"
    clear_caches()
    search(case, fabric=fabric)
    assert len(calls) == 2


def amounts_for(equity: float, loan: float, capex: float, term: int):
    """PlanAmounts with program checks, for the refusal tests (sources = uses, no other debt)."""
    reserve = equity + loan - capex
    return PlanAmounts(
        owner_equity=equity,
        loan_amount=loan,
        other_debt_amount=0.0,
        build_out_cost=capex,
        cash_reserve_at_opening=reserve,
        loan_term_months=term,
        price=None,
        days_open_per_week=None,
        site_label="current site",
        checks=program_checks(equity, capex + reserve, loan, term),
    )


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        # loan above the $500,000 maximum: may shrink, may not grow
        ((600_000.0, 1_100_000.0, 1_500_000.0, 84), (600_000.0, 1_000_000.0, 1_500_000.0, 84), []),
        ((600_000.0, 1_100_000.0, 1_500_000.0, 84), (600_000.0, 1_200_000.0, 1_500_000.0, 84), ["loan_within_limits"]),
        # loan below the $50,000 minimum: may not shrink
        ((60_000.0, 40_000.0, 80_000.0, 84), (60_000.0, 30_000.0, 80_000.0, 84), ["loan_within_limits"]),
        # equity share below 20.0%: may not fall further, may rise
        ((100_000.0, 450_000.0, 500_000.0, 84), (100_000.0, 480_000.0, 500_000.0, 84), ["meets_equity_minimum"]),
        ((100_000.0, 450_000.0, 500_000.0, 84), (110_000.0, 440_000.0, 500_000.0, 84), []),
        # term outside 60-120 months: may not move further out
        ((150_000.0, 400_000.0, 500_000.0, 180), (150_000.0, 400_000.0, 500_000.0, 240), ["term_within_limits"]),
        # a rule the plan meets may not break
        ((150_000.0, 400_000.0, 500_000.0, 84), (150_000.0, 520_000.0, 500_000.0, 84), ["loan_within_limits"]),
    ],
)
def test_program_refusals(before: tuple, after: tuple, expected: list[str]) -> None:
    assert refusals(amounts_for(*before), amounts_for(*after)) == expected
