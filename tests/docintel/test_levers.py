"""Tests for docintel/levers.py: applying resolution substitutions to SYNTHETIC agent applications."""

import copy

import pytest

from uwcopilot.agent.applications import parse_application
from uwcopilot.agent.credit_calculations import verify_revenue
from uwcopilot.agent.insurance_calculations import schedule_tiv
from uwcopilot.docintel.levers import LeverError, apply_substitutions, check_overrides, parse_path


def month(name: str, total: float, unidentified: float = 0.0, transfers: float = 0.0, cash: float = 0.0) -> dict:
    """A MonthlyDeposits dict."""
    return {
        "month": name,
        "total_deposits": total,
        "transfers_between_accounts": transfers,
        "refunds_and_reversals": 0.0,
        "loan_proceeds": 0.0,
        "owner_contributions": 0.0,
        "one_time_receipts": 0.0,
        "unidentified_deposits": unidentified,
        "cash_deposits": cash,
    }


@pytest.fixture
def credit() -> dict:
    """A small valid SYNTHETIC credit application with 12 statement months."""
    months = [
        month(f"2025-{index:02d}", 50_000.0 + 1_000 * index, unidentified=2_000.0, cash=4_000.0)
        for index in range(1, 13)
    ]
    return {
        "application_id": "APP-C-001",
        "line": "credit",
        "application_date": "2026-01-20",
        "applicant": {
            "legal_name": "Granite Dental LLC",
            "fein": "00-1234567",
            "business_structure": "llc",
            "state": "TX",
            "formation_date": "2015-03-01",
            "industry": "dental_practice",
        },
        "product": "term_loan",
        "requested_amount": 100_000.0,
        "term_months": 60,
        "note_rate_pct": 9.5,
        "rate_type": "fixed",
        "purpose": "expansion",
        "stated_annual_revenue": 700_000.0,
        "bank_statements": {
            "account_number": "9912345678",
            "months": months,
            "nsf_events_last_6_months": 0,
            "negative_balance_months_last_12": 0,
            "average_daily_balance_last_3_months": 20_000.0,
        },
        "debt": {
            "ttm_ebitda": 150_000.0,
            "ttm_pnl_revenue": 650_000.0,
            "existing_monthly_payment_schedule": 1_000.0,
            "existing_monthly_payment_observed": 1_000.0,
            "existing_total_debt_balance": 30_000.0,
        },
        "new_loans_last_90_days": 0,
        "largest_customer_revenue_pct": 10.0,
        "owners": [{"owner_id": "O1", "name": "Ana Diaz", "entity_type": "person", "ownership_pct": 100.0}],
        "documents_provided": ["signed_application"],
    }


@pytest.fixture
def insurance() -> dict:
    """A small valid SYNTHETIC insurance application with two locations and two loss-run years."""
    location = {
        "construction_class": "frame",
        "year_built": 2000,
        "roof_year": 2015,
        "stories": 2,
        "square_feet": 10_000,
        "sprinklered": True,
        "protection_class": 4,
    }
    return {
        "application_id": "APP-I-001",
        "line": "insurance",
        "application_date": "2026-09-01",
        "proposed_effective_date": "2026-11-01",
        "applicant": {
            "legal_name": "Cedar Bistro LLC",
            "fein": "00-7654321",
            "years_in_business": 8,
            "class_code": "office",
        },
        "coverage": "property",
        "stated_tiv": 3_000_000.0,
        "locations": [
            {
                **location,
                "location_id": "L1",
                "building_value": 1_500_000.0,
                "contents_value": 300_000.0,
                "business_income_value": 200_000.0,
            },
            {
                **location,
                "location_id": "L2",
                "building_value": 700_000.0,
                "contents_value": 200_000.0,
                "business_income_value": 100_000.0,
            },
        ],  # fmt: skip
        "loss_runs": {
            "carrier_produced": True,
            "valued_as_of": "2026-08-15",
            "years": [
                {"policy_year_start": "2022-11-01", "property_earned_premium": 9000.0, "claims": []},
                {
                    "policy_year_start": "2024-11-01",
                    "property_earned_premium": 9500.0,
                    "claims": [
                        {
                            "date_of_loss": "2025-02-01",
                            "line": "property",
                            "paid": 900.0,
                            "reserve": 100.0,
                            "incurred": 1000.0,
                        }
                    ],
                },
            ],
        },
        "stated_claim_count": 1,
        "stated_total_incurred": 1000.0,
        "annual_revenue": 1_000_000.0,
        "annual_payroll": 300_000.0,
        "owners": [{"owner_id": "O1", "name": "Bo Lee", "entity_type": "person", "ownership_pct": 100.0}],
        "documents_provided": ["signed_application"],
    }


def test_parse_path_handles_indexes() -> None:
    assert parse_path("loss_runs.years[2].claims[0].reserve") == ["loss_runs", "years", 2, "claims", 0, "reserve"]


def test_set_plain_and_nested_paths(credit: dict) -> None:
    result = apply_substitutions(
        credit, {"stated_annual_revenue": 640_000.0, "debt.existing_monthly_payment_schedule": 1_250.0}
    )
    assert result["stated_annual_revenue"] == 640_000.0
    assert result["debt"]["existing_monthly_payment_schedule"] == 1_250.0
    parse_application(result)


def test_input_is_never_changed(credit: dict) -> None:
    before = copy.deepcopy(credit)
    apply_substitutions(credit, {"stated_annual_revenue": 1.0, "documents_provided+": "debt_schedule"})
    assert credit == before


def test_replace_whole_owner_list(credit: dict) -> None:
    owners = [
        {"owner_id": "O1", "name": "Ana Diaz", "entity_type": "person", "ownership_pct": 60.0},
        {"owner_id": "O2", "name": "Raj Patel", "entity_type": "person", "ownership_pct": 40.0},
    ]
    result = apply_substitutions(credit, {"owners": owners})
    assert [owner["owner_id"] for owner in result["owners"]] == ["O1", "O2"]


def test_add_documents_string_or_list_without_duplicates(credit: dict) -> None:
    result = apply_substitutions(credit, {"documents_provided+": ["formation_documents", "signed_application"]})
    assert result["documents_provided"] == ["signed_application", "formation_documents"]
    result = apply_substitutions(result, {"documents_provided+": "debt_schedule"})
    assert result["documents_provided"][-1] == "debt_schedule"


def test_append_month_keeps_months_sorted(credit: dict) -> None:
    credit["bank_statements"]["months"] = [
        item for item in credit["bank_statements"]["months"] if item["month"] != "2025-05"
    ]
    assert verify_revenue(parse_application(credit).bank_statements).verified_annual_revenue is None
    result = apply_substitutions(credit, {"+bank_statements.months": month("2025-05", 40_000.0)})
    names = [item["month"] for item in result["bank_statements"]["months"]]
    assert names == sorted(names) and len(names) == 12
    assert verify_revenue(parse_application(result).bank_statements).verified_annual_revenue is not None


@pytest.mark.parametrize("target", [600_000.0, 640_123.45, 700_000.0, 400_000.0])
def test_verified_revenue_lever_hits_the_target_to_the_cent(credit: dict, target: float) -> None:
    result = apply_substitutions(credit, {"@verified_annual_revenue": target})
    verified = verify_revenue(parse_application(result).bank_statements).verified_annual_revenue
    assert verified == pytest.approx(target, abs=0.01)


def test_verified_revenue_lever_uses_unidentified_deposits_first(credit: dict) -> None:
    current = verify_revenue(parse_application(credit).bank_statements).verified_annual_revenue
    result = apply_substitutions(credit, {"@verified_annual_revenue": current + 6_000.0})
    totals = [item["total_deposits"] for item in result["bank_statements"]["months"]]
    original = [item["total_deposits"] for item in credit["bank_statements"]["months"]]
    unidentified = sum(item["unidentified_deposits"] for item in result["bank_statements"]["months"])
    assert unidentified == pytest.approx(24_000.0 - 6_000.0, abs=0.05)
    assert sum(totals) == pytest.approx(sum(original), abs=0.05)


def test_verified_revenue_rejects_negative_target(credit: dict) -> None:
    with pytest.raises(LeverError):
        apply_substitutions(credit, {"@verified_annual_revenue": -5.0})


def test_schedule_tiv_lever_scales_exactly(insurance: dict) -> None:
    result = apply_substitutions(insurance, {"@schedule_tiv": 3_456_789.01})
    assert schedule_tiv(parse_application(result)) == pytest.approx(3_456_789.01, abs=0.001)


def test_claim_paths_and_appends(insurance: dict) -> None:
    claim = {"date_of_loss": "2023-03-01", "line": "property", "paid": 500.0, "reserve": 0.0, "incurred": 500.0}
    result = apply_substitutions(
        insurance,
        {
            "loss_runs.years[1].claims[0].reserve": 50.0,
            "+loss_runs.years[0].claims": claim,
            "+loss_runs.years": [{"policy_year_start": "2023-11-01", "property_earned_premium": 9200.0, "claims": []}],
        },
    )
    years = result["loss_runs"]["years"]
    assert [year["policy_year_start"] for year in years] == ["2022-11-01", "2023-11-01", "2024-11-01"]
    assert years[0]["claims"] == [claim]
    assert years[2]["claims"][0]["reserve"] == 50.0
    parse_application(result)


def test_check_levers_do_not_change_the_application(credit: dict) -> None:
    substitutions = {"check:name_consistency": "resolved", "check:balance_continuity": "confirmed"}
    assert apply_substitutions(credit, substitutions) == credit
    assert check_overrides(substitutions) == {"name_consistency": "resolved", "balance_continuity": "confirmed"}


def test_unknown_path_is_an_error(credit: dict) -> None:
    with pytest.raises(LeverError):
        apply_substitutions(credit, {"loss_runs.years[0].claims": []})
    with pytest.raises(LeverError):
        apply_substitutions(credit, {"+owners[3].name": "x"})
