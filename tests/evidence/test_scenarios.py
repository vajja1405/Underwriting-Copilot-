"""evidence/scenarios.py and scenario_levers.py: stress tests, counterfactuals and decision stability on SYNTHETIC
cases. The scenario mechanics are checked against the deterministic formulas (calculations.py) and against the
reference policy's own NBC-4.5 stress figures."""

import glob

import pytest
from evidence_case_helpers import APPLICATIONS_DIR, application_dict

from uwcopilot.agent.policy import PolicyResult
from uwcopilot.evidence import scenario_levers
from uwcopilot.evidence.calculations import rate_stressed_dscr, rate_stressed_service, stressed_dscr
from uwcopilot.evidence.scenarios import (
    NOT_EVALUATED_BAND,
    STABLE_TEXT,
    ScenarioResult,
    calc_values,
    decision_metric,
    decision_stability,
    run_scenarios,
)
from uwcopilot.evidence.state import CaseState, apply, evaluate, from_application

CREDIT_IDS = sorted(path[-14:-5] for path in glob.glob(str(APPLICATIONS_DIR / "APP-C-*.json")))


def state_of(application_id: str) -> CaseState:
    """A committed SYNTHETIC application as a case state (clean screening assumption, injection screen run)."""
    return from_application(application_dict(application_id))


def by_id(results: list[ScenarioResult]) -> dict[str, ScenarioResult]:
    """Scenario results keyed by scenario id."""
    return {item.scenario_id: item for item in results}


def scenario_policy(state: CaseState, baseline: PolicyResult, scenario_id: str) -> PolicyResult:
    """The policy result of one scenario's application (built the same way run_scenario builds it)."""
    from uwcopilot.evidence.scenarios import scenario_plan, scenario_specs

    spec = next(item for item in scenario_specs(baseline.line) if item.scenario_id == scenario_id)
    return evaluate(apply(state, scenario_plan(spec, state, baseline).substitutions))


# --------------------------------------------------------------------------------------------------------------
# Loan scenarios against the formulas
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("application_id", CREDIT_IDS)
def test_revenue_shocks_equal_the_parameterized_stress_formula(application_id: str) -> None:
    state = state_of(application_id)
    baseline = evaluate(state)
    values = calc_values(baseline)
    if values.get("CALC-verified_annual_revenue") is None:
        results = by_id(run_scenarios(state, baseline))
        assert results["revenue_minus_10"].status == "not_evaluated"
        return
    verified, noi = values["CALC-verified_annual_revenue"], values["CALC-noi"]
    service = values["CALC-annual_debt_service"]
    for scenario_id, shock in (("revenue_minus_10", 0.10), ("revenue_minus_20", 0.20), ("revenue_minus_30", 0.30)):
        stressed = calc_values(scenario_policy(state, baseline, scenario_id))
        # Verified revenue falls by shock x verified (to the cent) and NOI by the same dollars.
        assert stressed["CALC-verified_annual_revenue"] == pytest.approx(
            round(verified - shock * verified, 2), abs=0.01
        )
        assert stressed["CALC-noi"] == pytest.approx(round(noi - shock * verified, 2), abs=0.01)
        assert stressed["CALC-dscr"] == stressed_dscr(noi, verified, service, shock)
        # The stated revenue moves by the same share, so the NBC-3.4 gap percent does not change.
        assert stressed["CALC-revenue_gap_pct"] == values["CALC-revenue_gap_pct"]


def test_the_20_percent_revenue_scenario_equals_the_policys_own_stress_figure() -> None:
    matched = 0
    for application_id in CREDIT_IDS:
        state = state_of(application_id)
        baseline = evaluate(state)
        own = calc_values(baseline).get("CALC-stressed_dscr_revenue")
        if own is None:
            continue
        assert calc_values(scenario_policy(state, baseline, "revenue_minus_20"))["CALC-dscr"] == own
        matched += 1
    assert matched >= 3


def test_the_200_bps_rate_scenario_equals_the_policys_own_rate_stress() -> None:
    state = state_of("APP-C-004")  # a variable-rate line of credit: the policy runs its own +2.00 point stress
    baseline = evaluate(state)
    values = calc_values(baseline)
    application = state.application
    service = rate_stressed_service(
        values["CALC-annual_debt_service"] - values["CALC-proposed_annual_debt_service"],
        application["product"],
        application["requested_amount"],
        application["note_rate_pct"],
        application["term_months"],
        200,
    )
    scenario = calc_values(scenario_policy(state, baseline, "rate_plus_200bps"))
    assert scenario["CALC-dscr"] == values["CALC-stressed_dscr_rate"]
    assert scenario["CALC-dscr"] == rate_stressed_dscr(values["CALC-noi"], service)


def test_rate_shocks_raise_only_the_requested_loan_rate() -> None:
    plan = scenario_levers.rate_shock_plan(application_dict("APP-C-001"), 400)
    assert plan.substitutions == {"note_rate_pct": round(application_dict("APP-C-001")["note_rate_pct"] + 4.0, 4)}


def test_stated_revenue_counterfactual_closes_the_revenue_gap() -> None:
    state = state_of("APP-C-001")
    baseline = evaluate(state)
    counterfactual = calc_values(scenario_policy(state, baseline, "stated_revenue_is_verified"))
    assert counterfactual["CALC-revenue_gap_pct"] == 0.0


def test_revenue_scenarios_are_not_evaluated_without_verified_revenue() -> None:
    state = state_of("APP-C-019")  # 9 statement months: NBC-3.2 cannot verify revenue
    results = by_id(run_scenarios(state))
    for scenario_id in ("revenue_minus_10", "revenue_minus_20", "revenue_minus_30", "stated_revenue_is_verified"):
        assert results[scenario_id].status == "not_evaluated"
        assert "verified annual revenue is unknown" in str(results[scenario_id].reason)
    # The rate shock runs, but the decision DSCR is not computable before or after it and the recommendation stays:
    # it cannot show anything about stability, so it is not counted as tested (and the case is not "stable").
    rate = results["rate_plus_200bps"]
    assert rate.status == "not_evaluated" and rate.not_evaluated_code == "metric_not_computable"
    assert rate.baseline_metric is None and rate.scenario_metric is None and not rate.recommendation_changed
    stability = decision_stability(list(results.values()), evaluate(state).recommendation)
    assert stability.stable is None
    assert stability.summary.startswith("not evaluated: no stress scenario could be tested")
    assert "not tested: revenue_minus_10" in stability.summary and "rate_plus_200bps" in stability.summary


# --------------------------------------------------------------------------------------------------------------
# Scenario flips, drivers and stability
# --------------------------------------------------------------------------------------------------------------


def test_a_revenue_shock_that_flips_the_recommendation_reports_its_drivers() -> None:
    # APP-C-001: NOI 89,501.00 on verified revenue 900,000.00. A 10% shock removes 90,000.00, so NOI becomes -499.00
    # and DSCR -499.00 / 40,682.01 = -0.01: below the 1.10 floor (R-NBC-04) and leverage cannot be computed (R-NBC-05).
    state = state_of("APP-C-001")
    results = by_id(run_scenarios(state))
    flip = results["revenue_minus_10"]
    assert flip.baseline_recommendation == "approve" and flip.recommendation == "decline_for_review"
    assert flip.recommendation_changed
    assert flip.baseline_metric == 2.2 and flip.scenario_metric == -0.01
    assert "R-NBC-04" in flip.triggers_added and "dscr_below_decline_floor" in flip.rules_added
    assert any(delta.calc_id == "CALC-noi" and delta.scenario == -499.0 for delta in flip.calc_deltas)
    assert flip.risk_band == NOT_EVALUATED_BAND
    assert "SHAP" in flip.drivers_note and "not evaluated" in flip.drivers_note
    stability = decision_stability(list(results.values()), "approve")
    revenue = next(family for family in stability.families if family.family == "revenue")
    assert revenue.first_flip == "revenue_minus_10" and revenue.flips_to == "decline_for_review"
    assert stability.stable is False and "revenue -10% (approve -> decline_for_review)" in stability.summary


def result(scenario_id: str, family: str, magnitude: float, changed: bool, evaluated: bool = True) -> ScenarioResult:
    """A hand-built scenario result for the stability rules."""
    return ScenarioResult(
        scenario_id=scenario_id,
        family=family,
        label=scenario_id.replace("_", " "),
        magnitude=magnitude,
        is_shock=family != "counterfactual",
        status="evaluated" if evaluated else "not_evaluated",
        metric_name="decision DSCR",
        metric_calc_id="CALC-dscr",
        baseline_recommendation="approve",
        recommendation=("refer" if changed else "approve") if evaluated else None,
        recommendation_changed=changed,
    )


def test_stability_reports_the_smallest_flipping_shock_within_each_family_in_magnitude_order() -> None:
    results = [
        result("revenue_minus_30", "revenue", 0.30, True),
        result("revenue_minus_10", "revenue", 0.10, False),
        result("revenue_minus_20", "revenue", 0.20, True),
        result("rate_plus_400bps", "rate", 400, True),
        result("rate_plus_200bps", "rate", 200, False),
        result("stated_revenue_is_verified", "counterfactual", 0.0, True),
    ]
    stability = decision_stability(results, "approve")
    families = {family.family: family for family in stability.families}
    assert families["revenue"].first_flip == "revenue_minus_20"
    assert families["revenue"].tested == ("revenue_minus_10", "revenue_minus_20", "revenue_minus_30")
    assert families["rate"].first_flip == "rate_plus_400bps"
    assert stability.stable is False
    assert stability.counterfactual_changes == ("stated_revenue_is_verified",)


def test_stability_says_stable_when_no_tested_shock_flips_and_not_evaluated_when_none_ran() -> None:
    stable = decision_stability(
        [result("rate_plus_200bps", "rate", 200, False), result("rate_plus_400bps", "rate", 400, False)], "approve"
    )
    assert stable.stable is True and stable.summary == STABLE_TEXT
    nothing = decision_stability([result("revenue_minus_10", "revenue", 0.10, False, evaluated=False)], "approve")
    assert nothing.stable is None and nothing.summary.startswith("not evaluated")


def test_a_blocking_finding_keeps_the_case_stable_under_every_shock() -> None:
    # APP-C-001 flips at revenue -10% on its own; with a confirmed FEIN difference (blocking R-NBC-09, KYC
    # Operations) the policy's precedence keeps "refer" whatever the shock does.
    state = apply(state_of("APP-C-001"), {"check:fein_consistency": "confirmed"})
    assert [(flag.check_id, flag.status) for flag in state.document_checks] == [("fein_consistency", "confirmed")]
    baseline = evaluate(state)
    assert baseline.blocking_open and baseline.recommendation == "refer"
    stability = decision_stability(run_scenarios(state, baseline), baseline.recommendation)
    assert stability.stable is True


# --------------------------------------------------------------------------------------------------------------
# Insurance scenarios
# --------------------------------------------------------------------------------------------------------------


def two_claim_application() -> dict:
    """APP-I-001 (property and GL, two experience-period claims) as JSON."""
    application = application_dict("APP-I-001")
    claims = [claim for year in application["loss_runs"]["years"] for claim in year["claims"]]
    assert len(claims) == 2
    return application


def test_severity_scales_every_experience_claim_and_the_stated_history_with_it() -> None:
    application = two_claim_application()
    plan = scenario_levers.severity_plan(application, 0.25)
    changed = scenario_levers.experience_claims(apply(from_application(application), plan.substitutions).application)
    before = scenario_levers.experience_claims(application)
    for (_, old), (_, new) in zip(before, changed, strict=True):
        # incurred x 1.25 to the cent; a balanced line stays balanced (reserve = incurred - paid).
        assert new["incurred"] == round(old["incurred"] * 1.25, 2)
        assert new["paid"] == round(old["paid"] * 1.25, 2)
        assert round(new["paid"] + new["reserve"], 2) == new["incurred"]
    total_before = scenario_levers.loss_run_total(application)
    total_after = round(sum(new["incurred"] for _, new in changed), 2) + round(
        sum(claim["incurred"] for claim in application["loss_runs"]["current_term_claims"]), 2
    )
    expected = round(application["stated_total_incurred"] * total_after / total_before, 2)
    assert plan.substitutions["stated_total_incurred"] == pytest.approx(expected, abs=0.01)
    assert plan.substitutions["stated_claim_count"] == application["stated_claim_count"]


def test_severity_raises_the_loss_ratio_by_the_multiplier_and_keeps_the_reconciliation() -> None:
    state = from_application(two_claim_application())
    baseline = evaluate(state)
    results = by_id(run_scenarios(state, baseline))
    severity = results["severity_plus_25"]
    # Both claims are below the $250,000 cap, so the 5-year ratio moves by x1.25 (up to the 1-decimal rounding).
    assert severity.scenario_metric == pytest.approx(baseline_metric(baseline) * 1.25, abs=0.1)
    assert "loss_run_reconciliation" not in severity.rules_added


def baseline_metric(result_: PolicyResult) -> float:
    """The baseline decision metric (it must exist)."""
    value = decision_metric(result_).value
    assert value is not None
    return value


def test_frequency_adds_whole_claims_rounded_half_up_and_says_when_it_adds_none() -> None:
    application = two_claim_application()
    none_added = scenario_levers.frequency_plan(application, 0.10)  # 0.10 x 2 = 0.2 -> 0 claims
    assert none_added.substitutions == {} and "no claim added" in none_added.notes[0]
    one_added = scenario_levers.frequency_plan(application, 0.25)  # 0.25 x 2 = 0.5 -> 1 claim (half up)
    assert one_added.substitutions["stated_claim_count"] == application["stated_claim_count"] + 1
    assert "effective frequency shock 50%" in one_added.notes[0]
    changed = apply(from_application(application), one_added.substitutions).application
    assert len(scenario_levers.experience_claims(changed)) == 3
    assert scenario_levers.added_claim_count(10, 0.25) == 3  # 2.5 rounds half up to 3


def test_insurance_decision_metric_is_the_highest_quoted_line_loss_ratio() -> None:
    baseline = evaluate(from_application(two_claim_application()))
    values = calc_values(baseline)
    ratios = [values[key] for key in values if key.startswith("CALC-loss_ratio_5yr_")]
    metric = decision_metric(baseline)
    assert metric.value == max(ratios) and metric.unit == "pct" and metric.name == "5-year loss ratio"


def test_the_tiv_counterfactual_sets_the_stated_tiv_to_the_schedule() -> None:
    state = from_application(two_claim_application())
    baseline = evaluate(state)
    counterfactual = calc_values(scenario_policy(state, baseline, "stated_tiv_is_schedule"))
    assert counterfactual["CALC-tiv_difference_pct"] == 0.0


def test_a_startup_loan_gets_the_rate_scenarios_only() -> None:
    state = from_application(application_dict("APP-S-001"))
    results = run_scenarios(state)
    assert [item.scenario_id for item in results] == ["rate_plus_200bps", "rate_plus_400bps"]
    assert all(item.status == "evaluated" and item.metric_calc_id == "CALC-startup_projected_dscr" for item in results)
    assert results[0].scenario_metric is not None and results[0].baseline_metric is not None
    assert results[0].scenario_metric < results[0].baseline_metric


# --------------------------------------------------------------------------------------------------------------
# Review finding: a shock that cannot show anything is not counted as tested
# --------------------------------------------------------------------------------------------------------------


def test_a_frequency_shock_that_adds_no_claim_is_not_tested() -> None:
    results = by_id(run_scenarios(from_application(two_claim_application())))
    frequency = results["frequency_plus_10"]  # 0.10 x 2 claims = 0.2 -> 0 claims added
    assert frequency.status == "not_evaluated" and frequency.not_evaluated_code == "no_change"
    assert "equals the baseline" in str(frequency.reason) and "no claim added" in str(frequency.reason)
    assert results["frequency_plus_25"].status == "evaluated"  # 0.25 x 2 = 0.5 -> 1 claim
    stability = decision_stability(list(results.values()), results["frequency_plus_25"].baseline_recommendation)
    family = next(item for item in stability.families if item.family == "frequency")
    assert family.tested == ("frequency_plus_25",) and family.not_evaluated == ("frequency_plus_10",)


def test_a_case_with_no_testable_shock_is_undetermined_not_stable() -> None:
    every = [
        result("frequency_plus_10", "frequency", 0.10, False, evaluated=False),
        result("severity_plus_10", "severity", 0.10, False, evaluated=False),
    ]
    stability = decision_stability(every, "approve")
    assert stability.stable is None and "no stress scenario could be tested" in stability.summary
