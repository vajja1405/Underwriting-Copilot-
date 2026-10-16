"""Tests for the target checks of the founder ledger: each Studio target is compared with the simulated probability
measured for exactly that target (studio/narrative/target_measures.py), and the NSP-6.1 driver check reads the right
claim. Every plan is SYNTHETIC and seeded; simulations use few paths to keep the tests fast.
"""

import pytest

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.founder_ledger import SimulationTooShort, build_founder_ledger
from uwcopilot.studio.narrative.generate import check_draft, render_narrative
from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.narrative.program_checks import ProgramChecks
from uwcopilot.studio.narrative.target_measures import target_measures
from uwcopilot.studio.path_to_approval import custom_target, default_target

N_PATHS = 150


@pytest.fixture(scope="module")
def scenario() -> GoldenScenario:
    """A SYNTHETIC laundromat plan in the SYNTHETIC metro, with SiteIQ and a path search."""
    return build_golden_scenario("synthetic_metro", "laundromat", n_paths=N_PATHS)


@pytest.fixture(scope="module")
def ledger(scenario: GoldenScenario) -> Ledger:
    """The scenario's founder ledger (default targets)."""
    return scenario.ledger()


def first_evidence(ledger: Ledger, assertion_id: str) -> str:
    """The claim a target check compared."""
    check = ledger.assertion(assertion_id)
    assert check is not None, assertion_id
    return check.evidence_ids[0]


def test_default_targets_are_checked_against_the_simulation(ledger: Ledger) -> None:
    assert first_evidence(ledger, ids.A_TARGET_DSCR) == ids.SIM_P_DSCR_Y2
    assert first_evidence(ledger, ids.A_TARGET_STRESS) == ids.SIM_P_STRESSED_Y2
    assert first_evidence(ledger, ids.A_TARGET_CASH) == ids.SIM_P_CASH
    target = default_target()
    assert ledger.claim(ids.TARGET_CASH_MONTHS).value == target.cash_months  # type: ignore[union-attr]
    assert ledger.claim(ids.TARGET_STRESS_SHARE).value == pytest.approx(target.stress_revenue_share * 100)  # type: ignore[union-attr]
    assert not [claim for claim in ledger.claims if claim.id.startswith("TARGET.measured.")]


def test_an_edited_target_uses_the_path_search_measured_for_it(scenario: GoldenScenario) -> None:
    assert scenario.path_result is not None
    edited = custom_target(cash_months=18, dscr_min=1.4)
    path_result = scenario.path_result.model_copy(update={"target": edited})
    ledger = build_founder_ledger(scenario.case, scenario.feasibility, scenario.site_score, path_result)
    assert first_evidence(ledger, ids.A_TARGET_CASH) == ids.PATH_BEFORE_P_CASH
    assert first_evidence(ledger, ids.A_TARGET_DSCR) == ids.PATH_BEFORE_P_DSCR
    assert first_evidence(ledger, ids.A_TARGET_STRESS) == ids.SIM_P_STRESSED_Y2
    assert ledger.claim(ids.TARGET_CASH_MONTHS).value == 18  # type: ignore[union-attr]
    assert ledger.claim(ids.TARGET_DSCR_MIN).value == 1.4  # type: ignore[union-attr]
    narrative = render_narrative(ledger)
    assert not check_draft(ledger, narrative.draft)
    assert ids.PATH_BEFORE_P_CASH in narrative.claims_used and ids.SIM_P_CASH not in narrative.claims_used
    assert narrative.audit == []


def test_an_edited_target_without_a_measurement_is_unknown_and_not_evaluated(scenario: GoldenScenario) -> None:
    claims = {claim.id: claim for claim in scenario.ledger().claims if not claim.id.startswith("PATH.")}
    measures = target_measures(custom_target(cash_months=12), scenario.feasibility, claims)
    assert measures.cash == "TARGET.measured.cash" and measures.dscr == ids.SIM_P_DSCR_Y2
    (unknown,) = measures.unknown
    assert unknown.status == "UNKNOWN" and unknown.value is None
    assert "24 months" in (unknown.reason or "") and "12 months" in (unknown.reason or "")


def test_the_driver_check_reads_the_claim_of_the_input_itself(scenario: GoldenScenario, ledger: Ledger) -> None:
    claims = {claim.id: claim for claim in ledger.claims}
    measures = target_measures(default_target(), scenario.feasibility, claims)
    checks = ProgramChecks(scenario.case, scenario.feasibility, claims, default_target(), measures)
    assert checks.driver_claim("mature_demand").id == ids.SITE_DEMAND_MODE  # type: ignore[union-attr]
    assert checks.driver_claim("monthly_rent").id in ("FOUNDER.monthly_rent", "ASSUME.monthly_rent")  # type: ignore[union-attr]
    assert checks.driver_claim("not_an_input_of_the_simulator") is None


def test_a_result_without_a_second_year_is_refused(scenario: GoldenScenario) -> None:
    # simulate() itself refuses fewer than 24 months; the guard protects a result built or loaded elsewhere.
    short = scenario.feasibility.model_copy(update={"yearly": scenario.feasibility.yearly[:1]})
    with pytest.raises(SimulationTooShort):
        build_founder_ledger(scenario.case, short)
