"""synthetic_plans.py: seeded SYNTHETIC plans for the S5 evaluation, anchored on the one-forecast stressed DSCR."""

import pytest

from uwcopilot.studio.data.fabric import get_fabric
from uwcopilot.studio.feasibility import load_assumptions
from uwcopilot.studio.feasibility.setup import build_setup
from uwcopilot.studio.path_to_approval import synthetic_plans
from uwcopilot.studio.path_to_approval.synthetic_plans import (
    STRESSED_DSCR_TARGET,
    band_label,
    generate_plan,
    plan_seed,
    stressed_single_point_dscr,
)
from uwcopilot.studio.profiles import load_profile

METRO = "synthetic_metro"


def make(profile_id: str, index: int) -> synthetic_plans.SyntheticPlan:
    fabric = get_fabric(METRO)
    profile = load_profile(profile_id, metro_id=METRO)
    seed = plan_seed(42, 0, 0, index)
    return generate_plan(profile, METRO, fabric.data_label, fabric.metro_inputs(profile), load_assumptions(), seed)


def test_same_seed_same_plan() -> None:
    assert make("car_wash", 3) == make("car_wash", 3)
    assert make("car_wash", 3).case != make("car_wash", 4).case
    assert plan_seed(42, 0, 1, 2) != plan_seed(42, 0, 2, 1)


@pytest.mark.parametrize("index", range(4))
def test_demand_anchored_plans_hit_their_stressed_target(index: int) -> None:
    plan = make("car_wash", index)
    assert STRESSED_DSCR_TARGET[0] <= plan.target_stressed_dscr <= STRESSED_DSCR_TARGET[1]
    if plan.anchored_by != "demand":
        pytest.skip("this seed was anchored by another lever")
    fabric = get_fabric(METRO)
    profile = load_profile("car_wash", metro_id=METRO)
    case = plan.case
    setup = build_setup(
        profile, METRO, "SYNTHETIC", fabric.metro_inputs(profile), case.site, case.plan, case.loan, load_assumptions()
    )
    assert stressed_single_point_dscr(setup, case.site.mode) == pytest.approx(plan.target_stressed_dscr, abs=1e-6)


def test_plans_are_synthetic_with_same_metro_candidates() -> None:
    plan = make("laundromat", 1)
    assert plan.case.site.status == "SYNTHETIC" and "SYNTHETIC" in plan.case.site.source
    assert len(plan.candidates) == synthetic_plans.N_CANDIDATES
    labels = {plan.case.site_label} | {item.label for item in plan.candidates}
    assert len(labels) == synthetic_plans.N_CANDIDATES + 1
    for candidate in plan.candidates:
        assert candidate.metro_id == METRO and candidate.site.status == "SYNTHETIC"
        assert candidate.site.unit == plan.case.site.unit


def test_band_labels_cover_the_target_range() -> None:
    assert band_label(STRESSED_DSCR_TARGET[0]) == "[0.6, 1.1)"
    assert band_label(STRESSED_DSCR_TARGET[1]) == "[2.1, 2.6]"
    with pytest.raises(ValueError):
        band_label(3.0)


def test_synthetic_facts_are_seeded_and_only_verify_typed_figures() -> None:
    plan = make("car_wash", 2)
    first = synthetic_plans.with_synthetic_facts(plan.case, 11)
    assert first == synthetic_plans.with_synthetic_facts(plan.case, 11)
    assert first.plan == plan.case.plan and first.loan == plan.case.loan
    facts = first.facts
    assert synthetic_plans.FACTS_CREDIT_SCORE[0] <= facts.credit_score <= synthetic_plans.FACTS_CREDIT_SCORE[1]
    assert set(facts.verified_fields) <= set(synthetic_plans.typed_fields(plan.case))
    seeds_with_debt = [
        seed for seed in range(20) if synthetic_plans.with_synthetic_facts(plan.case, seed).facts.other_debt
    ]
    assert 0 < len(seeds_with_debt) < 20


def test_a_quoted_rate_is_set_before_the_plan_is_simulated() -> None:
    """generate_plan uses quoted_rate_pct when the drawn loan has no rate, and leaves the draws unchanged otherwise."""
    fabric = get_fabric(METRO)
    profile = load_profile("car_wash", metro_id=METRO)
    inputs = fabric.metro_inputs(profile)
    plain = generate_plan(profile, METRO, fabric.data_label, inputs, load_assumptions(), 7)
    quoted = generate_plan(profile, METRO, fabric.data_label, inputs, load_assumptions(), 7, quoted_rate_pct=9.25)
    assert plain.case.loan.annual_rate_pct is None
    assert quoted.case.loan.annual_rate_pct == 9.25
    assert quoted.case.loan.amount == plain.case.loan.amount
    assert quoted.case.plan == plain.case.plan


def test_the_synthetic_metro_needs_no_quoted_rate() -> None:
    """The SYNTHETIC city has a prime rate, so the golden builder adds no SYNTHETIC quoted rate there."""
    from uwcopilot.studio.narrative.golden import synthetic_quoted_rate

    assert synthetic_quoted_rate("car_wash", METRO) is None
