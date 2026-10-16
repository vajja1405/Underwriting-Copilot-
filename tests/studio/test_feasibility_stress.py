"""The lender stress preset reuses the lender's own levers and formulas."""

from feasibility_helpers import basic_loan, setup_for

from uwcopilot.agent import policy_credit
from uwcopilot.agent.credit_calculations import coverage_ratio, proposed_annual_debt_service
from uwcopilot.agent.policy_startup import program_ratio
from uwcopilot.studio.feasibility import stress
from uwcopilot.studio.feasibility.result import YearBand
from uwcopilot.studio.feasibility.stress import (
    LENDER_STRESS,
    base_case_stressed_dscr,
    lender_annual_debt_service,
    projected_dscr,
)

YEAR = YearBand(
    year=2,
    revenue_p10=800_000.0,
    revenue_p50=1_000_000.0,
    revenue_p90=1_200_000.0,
    noi_p10=150_000.0,
    noi_p50=300_000.0,
    noi_p90=450_000.0,
    net_income_p10=0.0,
    net_income_p50=100_000.0,
    net_income_p90=250_000.0,
    debt_service_p50=200_000.0,
    dscr_p10=0.8,
    dscr_p50=1.5,
    dscr_p90=2.2,
)


def test_levers_and_thresholds_are_the_policy_constants() -> None:
    revenue, rate = LENDER_STRESS
    assert revenue.levers.revenue_drop_share == policy_credit.REVENUE_STRESS_SHARE
    assert revenue.min_dscr == policy_credit.REVENUE_STRESS_MIN_DSCR and not revenue.variable_rate_only
    assert rate.levers.rate_add_pct == policy_credit.RATE_STRESS_ADD_PCT
    assert rate.min_dscr == policy_credit.RATE_STRESS_MIN_DSCR and rate.variable_rate_only


def test_base_case_formulas_match_the_startup_policy() -> None:
    setup = setup_for(loan=basic_loan(annual_rate_pct=9.0))
    service = round(proposed_annual_debt_service("term_loan", 1_300_000, 9.0, 120), 2)
    assert lender_annual_debt_service(setup) == service
    assert projected_dscr(setup, YEAR) == program_ratio(300_000.0, service)
    revenue, rate = LENDER_STRESS
    stressed_noi = round(300_000.0 - policy_credit.REVENUE_STRESS_SHARE * 1_000_000.0, 2)
    assert base_case_stressed_dscr(revenue, setup, YEAR) == program_ratio(stressed_noi, service)
    stressed_service = round(proposed_annual_debt_service("term_loan", 1_300_000, 11.0, 120), 2)
    assert base_case_stressed_dscr(rate, setup, YEAR) == program_ratio(300_000.0, stressed_service)
    assert stressed_service > service


def test_base_case_rounds_half_up_like_the_startup_program(monkeypatch) -> None:
    # 225,000 / 200,000 = 1.125 exactly: the startup program rounds half up (1.13); Python's round() gives 1.12.
    setup = setup_for(loan=basic_loan(annual_rate_pct=9.0))
    monkeypatch.setattr(stress, "lender_annual_debt_service", lambda *args: 200_000.0)
    year = YEAR.model_copy(update={"noi_p50": 225_000.0})
    assert projected_dscr(setup, year) == 1.13
    assert coverage_ratio(225_000.0, 200_000.0) == 1.12
    # Revenue stress: NOI chosen so that NOI - stress share x 1,000,000 = 225,000 of stressed NOI, the same tie.
    noi = 225_000.0 + policy_credit.REVENUE_STRESS_SHARE * 1_000_000.0
    stressed_year = YEAR.model_copy(update={"noi_p50": noi, "revenue_p50": 1_000_000.0})
    assert base_case_stressed_dscr(LENDER_STRESS[0], setup, stressed_year) == 1.13


def test_description_is_built_from_the_constants() -> None:
    revenue, rate = LENDER_STRESS
    assert f"{policy_credit.REVENUE_STRESS_SHARE:.0%}" in revenue.description
    assert f"{policy_credit.RATE_STRESS_ADD_PCT:.2f}" in rate.description
