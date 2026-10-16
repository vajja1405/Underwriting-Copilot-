"""evidence/calculations.py against hand-worked examples (the arithmetic is written in the comments) and against
the reference policy's own calculations (stressed DSCR at 20% and +200 bps must be numerically identical)."""

from datetime import date

import pytest
from evidence_case_helpers import CASE_NUMBERS, application_dict, generated_truth
from evidence_snapshot_helpers import committed_applications

from uwcopilot.agent import credit_calculations, insurance_calculations
from uwcopilot.agent.applications import (
    BankStatementSummary,
    Claim,
    CreditApplication,
    DebtServiceInputs,
    LossRuns,
    LossRunYear,
    MonthlyDeposits,
    parse_application,
    screened_parties,
)
from uwcopilot.agent.policy import PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.policy_credit import RATE_STRESS_ADD_PCT, REVENUE_STRESS_SHARE
from uwcopilot.docintel import deposits
from uwcopilot.docintel.levers import set_path
from uwcopilot.evidence import calculations as calc


def statements(*months: MonthlyDeposits) -> BankStatementSummary:
    """A bank statement summary holding the given months (the other fields do not matter here)."""
    return BankStatementSummary(
        account_number="1234567890",
        months=list(months),
        nsf_events_last_6_months=0,
        negative_balance_months_last_12=0,
        average_daily_balance_last_3_months=10_000.0,
    )


def debt(ebitda: float, pnl_revenue: float) -> DebtServiceInputs:
    """Debt inputs with only EBITDA and P&L revenue set."""
    return DebtServiceInputs(
        ttm_ebitda=ebitda,
        ttm_pnl_revenue=pnl_revenue,
        existing_monthly_payment_schedule=0.0,
        existing_monthly_payment_observed=0.0,
        existing_total_debt_balance=0.0,
    )


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 1: revenue stress at 10%, 20% and 30%
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shock", "expected_noi", "expected_dscr"),
    [
        # NOI 200,000; verified revenue 600,000; annual debt service 100,000.
        # 10%: 200,000 - 0.10 x 600,000 = 200,000 - 60,000 = 140,000;  140,000 / 100,000 = 1.40
        (0.10, 140_000.0, 1.40),
        # 20%: 200,000 - 0.20 x 600,000 = 200,000 - 120,000 = 80,000;  80,000 / 100,000 = 0.80
        (0.20, 80_000.0, 0.80),
        # 30%: 200,000 - 0.30 x 600,000 = 200,000 - 180,000 = 20,000;  20,000 / 100,000 = 0.20
        (0.30, 20_000.0, 0.20),
    ],
)
def test_revenue_stress_hand_worked(shock: float, expected_noi: float, expected_dscr: float) -> None:
    assert calc.stressed_noi(200_000.0, 600_000.0, shock) == expected_noi
    assert calc.stressed_dscr(200_000.0, 600_000.0, 100_000.0, shock) == expected_dscr


def test_revenue_stress_with_no_debt_service_is_not_computable() -> None:
    # Debt service 0: a coverage ratio has no denominator, so the result is None (never a made-up number).
    assert calc.stressed_dscr(200_000.0, 600_000.0, 0.0, 0.20) is None


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 2: rate stress on a line of credit (+200 and +400 bps)
# --------------------------------------------------------------------------------------------------------------


def test_rate_stress_on_a_line_of_credit_hand_worked() -> None:
    # Line of credit $100,000 at 8.00%, existing annual debt service $12,000, NOI $48,000.
    # NBC-4.1: a line's annual service is one year of interest plus 10% of the commitment.
    # +200 bps -> 10.00%: interest 100,000 x 10% = 10,000; principal 10% x 100,000 = 10,000;
    #   service = 12,000 + 10,000 + 10,000 = 32,000;  DSCR = 48,000 / 32,000 = 1.50
    service_200 = calc.rate_stressed_service(12_000.0, "line_of_credit", 100_000.0, 8.0, 12, 200)
    assert service_200 == 32_000.0
    assert calc.rate_stressed_dscr(48_000.0, service_200) == 1.50
    # +400 bps -> 12.00%: interest 12,000; service = 12,000 + 12,000 + 10,000 = 34,000;
    #   DSCR = 48,000 / 34,000 = 1.41176... -> 1.41
    service_400 = calc.rate_stressed_service(12_000.0, "line_of_credit", 100_000.0, 8.0, 12, 400)
    assert service_400 == 34_000.0
    assert calc.rate_stressed_dscr(48_000.0, service_400) == 1.41


def test_rate_stress_on_a_zero_rate_term_loan_hand_worked() -> None:
    # Term loan $120,000 over 60 months at 0.00% and +0 bps: 120,000 / 60 = 2,000 a month, 24,000 a year;
    #   plus existing service 6,000 = 30,000.
    assert calc.rate_stressed_service(6_000.0, "term_loan", 120_000.0, 0.0, 60, 0) == 30_000.0


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 3: verified monthly revenue (NBC-3.3) and the annualized estimate
# --------------------------------------------------------------------------------------------------------------


def test_verified_monthly_revenue_hand_worked() -> None:
    # 2026-01: total 50,000; transfers 5,000 + refunds 1,000 = 6,000 non-revenue; revenue deposits 44,000;
    #   cash 12,000, non-cash 44,000 - 12,000 = 32,000; cash cap 25% x 32,000 = 8,000;
    #   cash excluded 12,000 - 8,000 = 4,000; eligible 32,000 + 8,000 = 40,000.
    # 2026-02: total 30,000, nothing excluded: eligible 30,000.
    summary = statements(
        MonthlyDeposits(month="2026-02", total_deposits=30_000.0, cash_deposits=0.0),
        MonthlyDeposits(
            month="2026-01",
            total_deposits=50_000.0,
            transfers_between_accounts=5_000.0,
            refunds_and_reversals=1_000.0,
            cash_deposits=12_000.0,
        ),
    )
    rows = calc.verified_monthly_revenue(summary)
    assert [row.month for row in rows] == ["2026-01", "2026-02"]  # oldest first
    assert rows[0] == calc.MonthlyRevenue("2026-01", 50_000.0, 6_000.0, 4_000.0, 40_000.0)
    assert rows[1] == calc.MonthlyRevenue("2026-02", 30_000.0, 0.0, 0.0, 30_000.0)
    # Annualized ESTIMATE: (40,000 + 30,000) x 12 / 2 months = 70,000 x 6 = 420,000.
    assert calc.annualized_eligible_revenue(summary) == 420_000.0
    # NBC-3.2 verified revenue needs 12 consecutive months, so the policy formula gives None here.
    assert calc.verify_revenue(summary).verified_annual_revenue is None


def test_annualized_revenue_without_months_is_none() -> None:
    assert calc.annualized_eligible_revenue(statements()) is None


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 4: DSCR with stated versus verified revenue
# --------------------------------------------------------------------------------------------------------------


def test_dscr_stated_vs_verified_hand_worked() -> None:
    # EBITDA 150,000; P&L revenue 1,000,000; stated 1,000,000; verified 900,000; annual debt service 100,000.
    # NBC-4.1 NOI = EBITDA - max(0, P&L revenue - revenue used):
    #   stated:   150,000 - max(0, 1,000,000 - 1,000,000) = 150,000 -> DSCR 150,000 / 100,000 = 1.50
    #   verified: 150,000 - max(0, 1,000,000 -   900,000) =  50,000 -> DSCR  50,000 / 100,000 = 0.50
    #   difference 1.50 - 0.50 = 1.00
    result = calc.dscr_stated_vs_verified(debt(150_000.0, 1_000_000.0), 1_000_000.0, 900_000.0, 100_000.0)
    assert result == calc.DscrComparison(
        noi_stated=150_000.0, noi_verified=50_000.0, dscr_stated=1.50, dscr_verified=0.50, difference=1.00
    )


def test_dscr_comparison_without_debt_service_has_no_ratio() -> None:
    result = calc.dscr_stated_vs_verified(debt(150_000.0, 1_000_000.0), 1_000_000.0, 900_000.0, 0.0)
    assert result.dscr_stated is None and result.dscr_verified is None and result.difference is None


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 5: loss ratio, frequency and severity by policy year
# --------------------------------------------------------------------------------------------------------------


def three_year_loss_runs() -> LossRuns:
    """Three policy years, property premium 10,000 each; claims: 4,000 (year 1); none; 3,000 and 300,000."""
    return LossRuns(
        carrier_produced=True,
        valued_as_of=date(2026, 9, 1),
        years=[
            LossRunYear(
                policy_year_start=date(2023, 11, 1),
                property_earned_premium=10_000.0,
                gl_earned_premium=None,
                claims=[Claim(date_of_loss=date(2024, 2, 1), line="property", paid=4_000, reserve=0, incurred=4_000)],
            ),
            LossRunYear(policy_year_start=date(2024, 11, 1), property_earned_premium=10_000.0, gl_earned_premium=None),
            LossRunYear(
                policy_year_start=date(2025, 11, 1),
                property_earned_premium=10_000.0,
                gl_earned_premium=None,
                claims=[
                    Claim(date_of_loss=date(2026, 1, 5), line="property", paid=3_000, reserve=0, incurred=3_000),
                    Claim(date_of_loss=date(2026, 3, 9), line="property", paid=0, reserve=300_000, incurred=300_000),
                ],
            ),
        ],
    )


def test_loss_ratio_by_year_hand_worked() -> None:
    # HM-4.1 caps each claim at 250,000.
    # 2023: 4,000 / 10,000 = 40.0%;  2024: 0 / 10,000 = 0.0%;
    # 2025: (3,000 + min(300,000, 250,000)) / 10,000 = 253,000 / 10,000 = 2530.0%
    assert calc.loss_ratio_by_year(three_year_loss_runs(), "property") == {
        "2023-11-01": 40.0,
        "2024-11-01": 0.0,
        "2025-11-01": 2530.0,
    }
    # No GL premium in any year: every GL year is None (never a made-up ratio).
    assert set(calc.loss_ratio_by_year(three_year_loss_runs(), "general_liability").values()) == {None}


def test_frequency_and_severity_trend_hand_worked() -> None:
    rows = calc.experience_by_year(three_year_loss_runs())
    # Counts 1, 0, 2. Mean severity: 4,000 / 1 = 4,000; none; (3,000 + 300,000) / 2 = 151,500.
    assert [row.claim_count for row in rows] == [1, 0, 2]
    assert [row.mean_severity for row in rows] == [4_000.0, None, 151_500.0]
    assert [row.capped_incurred for row in rows] == [4_000.0, 0.0, 253_000.0]
    # Frequency slope: x = 0, 1, 2; y = 1, 0, 2; mean x 1, mean y 1;
    #   covariance (-1)(0) + (0)(-1) + (1)(1) = 1; spread 1 + 0 + 1 = 2; slope 1 / 2 = 0.5 claims per year.
    assert calc.linear_trend([row.claim_count for row in rows]) == 0.5
    # Severity slope over the years with claims: points (0, 4,000) and (2, 151,500); mean x 1, mean y 77,750;
    #   covariance (-1)(-73,750) + (1)(73,750) = 147,500; spread 2; slope 73,750 per year.
    assert calc.linear_trend([row.mean_severity for row in rows]) == 73_750.0


def test_linear_trend_needs_two_points() -> None:
    assert calc.linear_trend([None, 5.0, None]) is None
    assert calc.linear_trend([]) is None


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 6: TIV reconciliation
# --------------------------------------------------------------------------------------------------------------


def test_tiv_reconciliation_hand_worked() -> None:
    data = application_dict("APP-I-001")
    first = dict(data["locations"][0])
    second = {**first, "location_id": "L2"}
    data["locations"] = [first, second]
    for path, value in {
        "locations[0].building_value": 1_000_000.0,
        "locations[0].contents_value": 200_000.0,
        "locations[0].business_income_value": 300_000.0,
        "locations[1].building_value": 500_000.0,
        "locations[1].contents_value": 100_000.0,
        "locations[1].business_income_value": 0.0,
        "stated_tiv": 2_310_000.0,
    }.items():
        set_path(data, path, value)
    application = parse_application(data)
    # L1: 1,000,000 + 200,000 + 300,000 = 1,500,000;  L2: 500,000 + 100,000 + 0 = 600,000;
    # schedule 2,100,000; stated 2,310,000; difference 210,000; 210,000 / 2,100,000 = 10.0%.
    result = calc.tiv_reconciliation(application)
    assert result.schedule_tiv == 2_100_000.0
    assert result.difference_usd == 210_000.0
    assert result.difference_pct == 10.0
    assert result.by_location == {"L1": 1_500_000.0, "L2": 600_000.0}


# --------------------------------------------------------------------------------------------------------------
# Hand-worked example 7: the labeled stress multiplier
# --------------------------------------------------------------------------------------------------------------


def test_stress_multiplier_hand_worked() -> None:
    # Frequency +10% and severity +25%: 1.10 x 1.25 = 1.375; a 60.0% loss ratio becomes 60.0 x 1.375 = 82.5%.
    assert calc.stress_multiplier(0.10, 0.25) == pytest.approx(1.375)
    assert calc.apply_stress_multiplier(60.0, calc.stress_multiplier(0.10, 0.25)) == 82.5
    # Both +25%: 1.25 x 1.25 = 1.5625; 40.0% x 1.5625 = 62.5%.
    assert calc.apply_stress_multiplier(40.0, calc.stress_multiplier(0.25, 0.25)) == 62.5
    # No shock is the identity, and a missing loss ratio stays missing.
    assert calc.stress_multiplier(0.0, 0.0) == 1.0
    assert calc.apply_stress_multiplier(None, 1.5) is None


# --------------------------------------------------------------------------------------------------------------
# Re-exports and equality with the reference policy
# --------------------------------------------------------------------------------------------------------------


def test_existing_formulas_are_re_exported_not_reimplemented() -> None:
    assert calc.verify_revenue is credit_calculations.verify_revenue
    assert calc.net_operating_income is credit_calculations.net_operating_income
    assert calc.coverage_ratio is credit_calculations.coverage_ratio
    assert calc.proposed_annual_debt_service is credit_calculations.proposed_annual_debt_service
    assert calc.loss_ratios is insurance_calculations.loss_ratios
    assert calc.schedule_tiv is insurance_calculations.schedule_tiv
    assert calc.average_daily_balance is deposits.average_daily_balance
    assert calc.negative_balance_months is deposits.negative_balance_months
    assert all(hasattr(calc, name) for name in calc.__all__)


def credit_applications() -> list[CreditApplication]:
    """The committed credit applications plus the generated packets' as-documented applications."""
    found = [item for item in committed_applications() if isinstance(item, CreditApplication)]
    for number in CASE_NUMBERS:
        truth = generated_truth(f"PKT-C-{number}", "credit")
        found.append(parse_application(truth.application_as_documented))
    return found


def test_stressed_dscr_equals_the_policy_at_20_percent_and_200_bps() -> None:
    revenue_cases = 0
    rate_cases = 0
    for application in credit_applications():
        clear = [ScreeningOutcome(party_id=party.party_id, status="clear") for party in screened_parties(application)]
        result = evaluate_policy(application, PolicyInputs(screening=clear))
        calcs = {item.calc_id: item for item in result.calculations}
        if "CALC-stressed_dscr_revenue" in calcs:
            revenue_cases += 1
            noi = calcs["CALC-noi"].value
            verified = calcs["CALC-verified_annual_revenue"].value
            service = calcs["CALC-annual_debt_service"].value
            policy_calc = calcs["CALC-stressed_dscr_revenue"]
            assert calc.stressed_noi(noi, verified, REVENUE_STRESS_SHARE) == policy_calc.inputs["stressed_noi"]
            assert calc.stressed_dscr(noi, verified, service, REVENUE_STRESS_SHARE) == policy_calc.value
        if "CALC-stressed_dscr_rate" in calcs:
            rate_cases += 1
            existing = calcs["CALC-annual_debt_service"].inputs["existing_annual_debt_service"]
            bps = RATE_STRESS_ADD_PCT * 100
            service = calc.rate_stressed_service(
                existing,
                application.product,
                application.requested_amount,
                application.note_rate_pct,
                application.term_months,
                bps,
            )
            policy_calc = calcs["CALC-stressed_dscr_rate"]
            assert service == policy_calc.inputs["annual_debt_service_stressed"]
            assert calc.rate_stressed_dscr(calcs["CALC-noi"].value, service) == policy_calc.value
    assert revenue_cases >= 5
    assert rate_cases >= 1
