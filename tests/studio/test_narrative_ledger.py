"""Tests for the founder ledger (Studio S4): every founder-facing number is a claim with the right status.

Every plan is SYNTHETIC (studio/narrative/golden.py, seeded); the metro is the generated SYNTHETIC city, so nothing
depends on downloaded data. Simulations use few paths to keep the tests fast.
"""

import pytest

from uwcopilot.agent.policy_credit import REVENUE_STRESS_MIN_DSCR, REVENUE_STRESS_SHARE
from uwcopilot.agent.policy_startup import (
    MIN_EQUITY_INJECTION_PCT,
    STARTUP_DSCR_MINIMUM,
    STARTUP_MAX_AMOUNT,
    STARTUP_MIN_AMOUNT,
    program_pct,
    program_ratio,
    revenue_stressed_noi,
)
from uwcopilot.evidence.ledger_models import Claim, Ledger
from uwcopilot.studio.feasibility import simulate
from uwcopilot.studio.feasibility.inputs import RangeOverride
from uwcopilot.studio.narrative import (
    FounderDocumentValue,
    PlanMismatch,
    build_founder_ledger,
    ids,
    ledger_from_plan,
    plan_sha256,
)
from uwcopilot.studio.narrative.derive import derived_claim, policy_claim, weakest_status
from uwcopilot.studio.narrative.founder_claims import typed_claim
from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.narrative.units import format_for
from uwcopilot.studio.path_to_approval import FounderFacts
from uwcopilot.studio.path_to_approval.search import fabric_for

N_PATHS = 200


@pytest.fixture(scope="module")
def scenario() -> GoldenScenario:
    """A SYNTHETIC car-wash plan in the SYNTHETIC metro, with SiteIQ and a path search."""
    return build_golden_scenario("synthetic_metro", "car_wash", n_paths=N_PATHS)


@pytest.fixture(scope="module")
def ledger(scenario: GoldenScenario) -> Ledger:
    """The scenario's founder ledger."""
    return scenario.ledger()


def claim(ledger: Ledger, claim_id: str) -> Claim:
    """A claim that must exist."""
    found = ledger.claim(claim_id)
    assert found is not None, claim_id
    return found


def test_the_ledger_is_the_shared_evidence_ledger_with_the_synthetic_label(ledger: Ledger) -> None:
    assert isinstance(ledger, Ledger)
    assert ledger.line == "startup_credit" and ledger.data_label == "SYNTHETIC"
    assert ledger.subject_id == "GOLDEN-synthetic_metro-car_wash"
    counts = ledger.status_counts()
    assert counts["USER_STATED"] and counts["ASSUMPTION"] and counts["MODEL_ESTIMATE"] and counts["VERIFIED"]


def test_founder_inputs_are_user_stated_with_their_field(scenario: GoldenScenario, ledger: Ledger) -> None:
    equity = claim(ledger, ids.FOUNDER_EQUITY)
    assert equity.status == "USER_STATED" and equity.value == scenario.case.plan.owner_equity
    assert equity.provenance.kind == "user_input" and equity.provenance.ref == "plan.owner_equity"
    assert equity.inputs == {"field": "plan.owner_equity", "value": scenario.case.plan.owner_equity}
    loan = claim(ledger, ids.FOUNDER_LOAN)
    assert loan.status == "USER_STATED" and loan.value == scenario.case.loan.amount
    assert claim(ledger, ids.FOUNDER_TERM).value == scenario.case.loan.term_months
    assert claim(ledger, ids.FOUNDER_CREDIT_SCORE).value == scenario.case.facts.credit_score


def test_simulation_outputs_are_model_estimates_with_the_run_identity(scenario: GoldenScenario, ledger: Ledger) -> None:
    result = scenario.feasibility
    p_dscr = claim(ledger, ids.SIM_P_DSCR_Y2)
    assert p_dscr.status == "MODEL_ESTIMATE"
    assert p_dscr.value == pytest.approx(result.p_dscr_at_least_target["year_2"] * 100)
    assert p_dscr.display == "pct1" and p_dscr.unit == "PERCENT"
    assert p_dscr.provenance.model_name == "studio-feasibility"
    assert p_dscr.provenance.model_version == result.simulator_version
    details = p_dscr.provenance.details
    assert details["seed"] == result.seed and details["n_paths"] == N_PATHS
    assert details["plan_sha256"] == plan_sha256(scenario.case)
    assert p_dscr.inputs["plan_sha256"] == plan_sha256(scenario.case)
    assert "simulated" in (p_dscr.uncertainty or "")
    noi = claim(ledger, ids.year_id(2, "noi_p50"))
    assert noi.status == "MODEL_ESTIMATE" and noi.value == result.yearly[1].noi_p50
    assert claim(ledger, ids.month_id(12, "cash_p10")).value == result.monthly[11].cash_p10
    assert ledger.claim(ids.month_id(13, "cash_p10")) is None


def test_assumptions_carry_their_source_and_a_range_that_holds_the_value(ledger: Ledger) -> None:
    assumptions = ledger.claims_with_status("ASSUMPTION")
    assert assumptions
    for item in assumptions:
        assert item.provenance.source, item.id
        if item.value_range is not None and isinstance(item.value, float | int):
            assert item.value_range.low <= item.value <= item.value_range.high, item.id
    # A profile range without a publication is labeled "unsourced", never shown as data.
    assert claim(ledger, "ASSUME.cost_ratio_supplies").provenance.source == "unsourced"
    # Shares are stored as percents so the renderer can print them (0.05 would read 0.1%).
    supplies = claim(ledger, "ASSUME.cost_ratio_supplies")
    assert supplies.unit == "PERCENT" and supplies.display == "pct1"


def test_the_site_demand_given_to_the_simulator_is_an_assumption_not_data(ledger: Ledger) -> None:
    demand = claim(ledger, ids.SITE_DEMAND_MODE)
    assert demand.status == "ASSUMPTION"
    assert demand.provenance.source and demand.provenance.source.startswith("SYNTHETIC")
    assert "city data" not in demand.provenance.source  # a generated plan's range, not data of the SYNTHETIC city
    assert demand.value_range is not None


def test_siteiq_rows_are_verified_with_their_locator_or_labeled(scenario: GoldenScenario, ledger: Ledger) -> None:
    assert scenario.site_score is not None
    for factor in scenario.site_score.factors:
        item = claim(ledger, ids.site_factor_id(factor.factor))
        if factor.status == "SYNTHETIC":
            assert item.status == "VERIFIED" and item.provenance.kind == "public_data"
            assert "SYNTHETIC" in item.label and (item.provenance.source or "").startswith("SYNTHETIC")
            assert item.provenance.details["sha256"] == factor.sources[0].sha256
        elif factor.status == "ASSUMPTION":
            assert item.status == "ASSUMPTION"
        elif factor.status == "UNKNOWN":
            assert item.status == "UNKNOWN" and item.reason
    assert claim(ledger, ids.SITE_OVERALL).status == "MODEL_ESTIMATE"
    assert claim(ledger, ids.SITE_COVERAGE).value == scenario.site_score.coverage_score


def test_derived_numbers_are_never_more_certain_than_their_inputs(ledger: Ledger) -> None:
    assert claim(ledger, ids.CALC_EQUITY_PCT).status == "USER_STATED"
    assert claim(ledger, ids.CALC_PROJECTED_DSCR).status == "MODEL_ESTIMATE"
    reserve = claim(ledger, ids.CALC_REQUIRED_RESERVE)
    assert reserve.status == "ASSUMPTION" and reserve.value_range is not None
    assert set(reserve.depends_on_assumption) <= {item.id for item in ledger.claims_with_status("ASSUMPTION")}
    for item in ledger.claims:
        if item.id.startswith("CALC.") and item.status == "VERIFIED":
            pytest.fail(f"{item.id} is VERIFIED although no input of a founder plan is verified")


def test_program_arithmetic_matches_the_lender_side(scenario: GoldenScenario, ledger: Ledger) -> None:
    year = scenario.feasibility.yearly[1]
    service = float(claim(ledger, ids.CALC_ANNUAL_DEBT_SERVICE).value)
    assert claim(ledger, ids.CALC_PROJECTED_DSCR).value == program_ratio(year.noi_p50, service)
    stressed = revenue_stressed_noi(year.noi_p50, year.revenue_p50)
    assert claim(ledger, ids.CALC_REVENUE_STRESSED_DSCR).value == program_ratio(stressed, service)
    cost = float(claim(ledger, ids.CALC_PROJECT_COST).value)
    assert claim(ledger, ids.CALC_EQUITY_PCT).value == program_pct(scenario.case.plan.owner_equity, cost)
    other = sum(item.amount for item in scenario.case.facts.other_debt)
    assert cost == pytest.approx(scenario.case.plan.owner_equity + scenario.case.loan.amount + other, abs=0.01)


def test_thresholds_are_the_program_constants_and_checks_agree_with_them(ledger: Ledger) -> None:
    assert claim(ledger, ids.PROGRAM_MIN_DSCR).value == STARTUP_DSCR_MINIMUM
    assert claim(ledger, ids.PROGRAM_MIN_EQUITY).value == MIN_EQUITY_INJECTION_PCT
    assert claim(ledger, ids.PROGRAM_REVENUE_STRESS_MIN).value == REVENUE_STRESS_MIN_DSCR
    assert claim(ledger, ids.PROGRAM_REVENUE_STRESS_SHARE).value == pytest.approx(REVENUE_STRESS_SHARE * 100)
    threshold = claim(ledger, ids.PROGRAM_MIN_DSCR)
    assert threshold.status == "VERIFIED" and threshold.provenance.kind == "policy"
    assert threshold.provenance.ref == "NSP-4.1" and "SYNTHETIC" in (threshold.provenance.source or "")
    projected = claim(ledger, ids.CALC_PROJECTED_DSCR)
    check = ledger.assertion(ids.A_PROJECTED_DSCR)
    assert check is not None and check.result == (float(projected.value) >= STARTUP_DSCR_MINIMUM)
    assert check.section_ids == ("NSP-4.1",)
    amount = float(claim(ledger, ids.FOUNDER_LOAN).value)
    limits = ledger.assertion(ids.A_AMOUNT)
    assert limits is not None and limits.result == (STARTUP_MIN_AMOUNT <= amount <= STARTUP_MAX_AMOUNT)
    assert limits.trigger_ids == (() if limits.result else ("R-NSP-10",))


def test_assertion_phrases_hold_no_digits_and_no_decision_words(ledger: Ledger) -> None:
    for item in ledger.assertions:
        for phrase in (item.phrase_true, item.phrase_false, item.phrase_unknown):
            assert not any(character.isdigit() for character in phrase), (item.id, phrase)
            assert "approv" not in phrase.lower() and "declin" not in phrase.lower(), (item.id, phrase)


def test_matching_bank_statements_verify_the_equity(scenario: GoldenScenario) -> None:
    statement = FounderDocumentValue(
        field="plan.owner_equity",
        value=scenario.case.plan.owner_equity + 0.5,
        doc_id="U-BANK-1",
        document_type="equity_bank_statements",
        page=2,
        line=7,
    )
    ledger = build_founder_ledger(scenario.case, scenario.feasibility, documents=[statement])
    equity = claim(ledger, ids.FOUNDER_EQUITY)
    assert equity.status == "VERIFIED" and equity.provenance.kind == "document"
    assert equity.provenance.citations[0].text() == "U-BANK-1 p2 L7"
    check = ledger.assertion(ids.A_EQUITY_DOCS)
    assert check is not None and check.result is True


def test_a_disagreeing_document_makes_both_values_conflicting(scenario: GoldenScenario) -> None:
    quote = FounderDocumentValue(
        field="plan.owner_equity",
        value=scenario.case.plan.owner_equity - 5_000,
        doc_id="U-BANK-2",
        document_type="equity_bank_statements",
        page=1,
    )
    ledger = build_founder_ledger(scenario.case, scenario.feasibility, documents=[quote])
    founder = claim(ledger, ids.FOUNDER_EQUITY)
    document = claim(ledger, "DOC.plan.owner_equity.1")
    assert founder.status == document.status == "CONFLICTING"
    assert founder.conflict_ids == ("DOC.plan.owner_equity.1",) and document.conflict_ids == (ids.FOUNDER_EQUITY,)
    assert claim(ledger, ids.CALC_EQUITY_PCT).status == "CONFLICTING"
    assert {item.id for item in ledger.open_claims()} >= {ids.FOUNDER_EQUITY, "DOC.plan.owner_equity.1"}


def test_marked_verified_without_a_document_stays_user_stated(scenario: GoldenScenario) -> None:
    facts = scenario.case.facts.model_copy(update={"verified_fields": ["plan.owner_equity"]})
    case = scenario.case.model_copy(update={"facts": facts})
    equity = claim(build_founder_ledger(case, scenario.feasibility), ids.FOUNDER_EQUITY)
    assert equity.status == "USER_STATED" and "stays USER_STATED" in str(equity.provenance.details["note"])


def test_missing_owner_facts_are_unknown_and_their_checks_not_evaluated(scenario: GoldenScenario) -> None:
    case = scenario.case.model_copy(update={"facts": FounderFacts()})
    ledger = build_founder_ledger(case, scenario.feasibility)
    score = claim(ledger, ids.FOUNDER_CREDIT_SCORE)
    assert score.status == "UNKNOWN" and score.value is None and "NSP-2.2" in (score.reason or "")
    check = ledger.assertion(ids.A_CREDIT)
    assert check is not None and check.result is None and check.depends_on_unknown == (ids.FOUNDER_CREDIT_SCORE,)


def test_a_simulation_of_another_plan_is_refused(scenario: GoldenScenario) -> None:
    other = scenario.case.model_copy(
        update={"plan": scenario.case.plan.model_copy(update={"owner_equity": scenario.case.plan.owner_equity + 1})}
    )
    with pytest.raises(PlanMismatch):
        build_founder_ledger(other, scenario.feasibility)


def test_ledger_from_plan_is_the_same_ledger(scenario: GoldenScenario, ledger: Ledger) -> None:
    again = ledger_from_plan(
        scenario.case, scenario.site_score, scenario.feasibility, scenario.path_result, subject_id=scenario.scenario_id
    )
    assert again == ledger


def test_path_claims_are_model_estimates_of_the_search(scenario: GoldenScenario, ledger: Ledger) -> None:
    assert scenario.path_result is not None
    status = claim(ledger, ids.PATH_STATUS)
    assert status.value == scenario.path_result.status and status.status == "MODEL_ESTIMATE"
    assert status.provenance.model_version == scenario.path_result.engine_version
    for item in ledger.claims:
        if item.id.startswith("PATH."):
            assert item.status == "MODEL_ESTIMATE", item.id


def test_weakest_status_order_and_the_derived_claim_rule() -> None:
    founder = typed_claim("plan.owner_equity", "F.a", "A", "USD", "usd0", 100.0, False)
    threshold = policy_claim("P.b", "B", 20.0, "PERCENT", "pct1", "NSP-3.1", "test source", "at least")
    assert weakest_status([founder, threshold]) == "USER_STATED"
    assert weakest_status([threshold]) == "VERIFIED"
    verified = derived_claim("C.v", "V", 20.0, "PERCENT", "pct1", "copy", [threshold])
    assert verified.status == "VERIFIED" and verified.provenance.kind == "calculation"
    stated = derived_claim("C.s", "S", 100.0, "USD", "usd0", "copy", [founder, threshold])
    assert stated.status == "USER_STATED" and stated.inputs["F.a"] == 100.0 and stated.inputs["formula"] == "copy"
    with pytest.raises(ValueError, match="range"):
        assumed_input = Claim.model_validate(
            {
                "id": "A.x",
                "label": "X",
                "value": 1.0,
                "unit": "RATIO",
                "display": "ratio2",
                "status": "ASSUMPTION",
                "provenance": {"kind": "assumption", "ref": "x", "source": "unsourced"},
                "value_range": {"low": 0.5, "high": 2.0},
            }
        )
        derived_claim("C.a", "A", 2.0, "RATIO", "ratio2", "double", [assumed_input])


def test_display_formats_for_studio_units() -> None:
    assert format_for("share of revenue", 0.05).factor == 100.0
    assert format_for("probability per year that a competitor opens", 0.1).display == "pct1"
    assert format_for("payroll taxes and benefits as a share of wages", 0.12).unit == "PERCENT"
    assert format_for("USD/hour", 18.5).display == "usd2"
    assert format_for("USD", 250_000).display == "usd0"
    assert format_for("months", 12).display == "months"
    assert format_for("count", 3.0).display == "int"
    assert format_for("cars/day", 4.5).display == "ratio2"
    assert format_for("cars/day", 155.7).display == "int"


def test_a_range_the_founder_edited_is_user_stated_and_feeds_the_arithmetic(scenario: GoldenScenario) -> None:
    case = scenario.case
    edits = {"payroll_burden_share": RangeOverride(low=0.12, mode=0.15, high=0.2, note="SYNTHETIC test edit")}
    edited = case.model_copy(update={"plan": case.plan.model_copy(update={"assumption_overrides": edits})})
    result = simulate(
        edited.profile_id, fabric_for(edited.metro_id), edited.site, edited.plan, edited.loan, n=N_PATHS, seed=1
    )
    ledger = build_founder_ledger(edited, result)
    burden = claim(ledger, ids.assume_id("payroll_burden_share"))
    assert burden.status == "USER_STATED" and burden.provenance.kind == "user_input"
    assert burden.value == pytest.approx(15.0) and burden.value_range is not None
    assert (burden.value_range.low, burden.value_range.high) == pytest.approx((12.0, 20.0))
    assert "SYNTHETIC test edit" not in burden.label
    fixed = claim(ledger, ids.CALC_FIXED_COSTS)
    assert burden.id in fixed.inputs and fixed.status != "VERIFIED"


def test_simulated_figures_list_a_document_conflict_they_rest_on(scenario: GoldenScenario) -> None:
    """The simulator ran on the stated equity, so a contradicting document must show on every simulated figure."""
    quote = FounderDocumentValue(
        field="plan.owner_equity",
        value=scenario.case.plan.owner_equity - 5_000,
        doc_id="U-BANK-2",
        document_type="equity_bank_statements",
        page=1,
    )
    ledger = build_founder_ledger(scenario.case, scenario.feasibility, documents=[quote])
    simulated = [item for item in ledger.claims if item.id.startswith("SIM.") and item.status == "MODEL_ESTIMATE"]
    assert simulated
    assert all(ids.FOUNDER_EQUITY in item.depends_on_conflicting for item in simulated)


def test_simulated_figures_have_no_conflict_without_a_document(ledger: Ledger) -> None:
    assert all(not item.depends_on_conflicting for item in ledger.claims if item.id.startswith("SIM."))


def test_a_target_the_founder_edited_is_user_stated() -> None:
    """An edited target is the founder's own figure, so it must not read as a verified Studio value."""
    from uwcopilot.studio.narrative.program_thresholds import target_claims
    from uwcopilot.studio.path_to_approval.targets import custom_target, default_target

    edited = {item.id: item for item in target_claims(custom_target(p_dscr_min=0.5))}
    default = {item.id: item for item in target_claims(default_target())}
    assert edited[ids.TARGET_P_DSCR].status == "USER_STATED"
    assert edited[ids.TARGET_P_DSCR].provenance.kind == "user_input"
    assert edited[ids.TARGET_DSCR_MIN].status == "VERIFIED"
    assert default[ids.TARGET_P_DSCR].status == "VERIFIED"
