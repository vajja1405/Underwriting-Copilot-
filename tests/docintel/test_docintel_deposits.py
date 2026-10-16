"""Tests for docintel/deposits.py: NBC-3.3 deposit categories, withdrawal categories and NBC-3.5 conduct figures."""

from datetime import date

import pytest
from docintel_check_fixtures import MONTHS, credit_packet_data, loan

from uwcopilot.agent.applications import MonthlyDeposits
from uwcopilot.docintel.deposits import (
    average_daily_balance,
    average_monthly_loan_payments,
    average_monthly_mca_remittance,
    classify_deposit,
    classify_withdrawal,
    monthly_deposits,
    negative_balance_months,
    new_loans_last_90_days,
    nsf_events,
)
from uwcopilot.docintel.schemas import BankStatement


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("CARD SETTLEMENT TERNWICK", "revenue"),
        ("ACH CREDIT ACORN FOODS INV 1001", "revenue"),
        ("MOBILE CHECK DEPOSIT", "revenue"),
        ("CASH DEPOSIT", "cash_revenue"),
        ("ONLINE TRANSFER FROM SAVINGS ...4512", "transfer"),
        ("VENDOR REFUND MAPLEWOOD SUPPLY", "refund"),
        ("CHARGEBACK REVERSAL", "refund"),
        ("LOAN PROCEEDS BLUESTEM COMMERCIAL LENDING", "loan_proceeds"),
        ("MCA FUNDING RAPIDLEAF MERCHANT FUNDING", "loan_proceeds"),
        ("OWNER CAPITAL CONTRIBUTION", "owner_contribution"),
        ("IRS TAX REFUND", "one_time"),
        ("INSURANCE CLAIM PAYMENT", "one_time"),
        ("DEPOSIT REF 88213", "unidentified"),
        ("", "unidentified"),
        ("cаsh depоsit", "cash_revenue"),  # homoglyphs are folded first
    ],
)
def test_classify_deposit(description: str, category: str) -> None:
    assert classify_deposit(description) == category


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("NSF FEE", "nsf_fee"),
        ("OVERDRAFT FEE", "nsf_fee"),
        ("MCA REMIT RAPIDLEAF MERCHANT FUNDING", "mca_remittance"),
        ("ACH PMT OLD WEIR CREDIT UNION LOAN", "loan_payment"),
        ("PAYROLL GRIDWORKS", "other"),
    ],
)
def test_classify_withdrawal(description: str, category: str) -> None:
    assert classify_withdrawal(description) == category


def test_monthly_deposits_split_matches_the_agent_model() -> None:
    extra = ((18, "ONLINE TRANSFER FROM SAVINGS ...4512", 5_000.0, None), (19, "DEPOSIT REF 7", 700.0, None))
    packet = loan(credit_packet_data(extra_rows=extra))
    months = monthly_deposits(packet.statements[0])
    assert months is not None
    MonthlyDeposits.model_validate(months)
    assert months["month"] == "2025-08"
    assert months["total_deposits"] == 47_700.0
    assert months["transfers_between_accounts"] == 5_000.0
    assert months["unidentified_deposits"] == 700.0
    assert months["cash_deposits"] == 2_000.0


def test_statement_without_rows_counts_its_printed_total_as_unidentified() -> None:
    statement = BankStatement.model_validate(
        {
            "doc_id": "D02",
            "period_start": {"value": date(2026, 7, 1), "page": 3, "line": 4},
            "total_deposits": {"value": 9_000.0, "page": 3, "line": 6},
        }
    )
    months = monthly_deposits(statement)
    assert months is not None
    assert months["total_deposits"] == 9_000.0
    assert months["unidentified_deposits"] == 9_000.0


def tiny_statement(rows: list[dict], opening: float = 100.0) -> BankStatement:
    """A four-day statement (2026-07-01 to 2026-07-04) for the average daily balance tests."""
    return BankStatement.model_validate(
        {
            "doc_id": "D02",
            "period_start": {"value": date(2026, 7, 1)},
            "period_end": {"value": date(2026, 7, 4)},
            "opening_balance": {"value": opening},
            "transactions": rows,
        }
    )


def test_average_daily_balance_uses_end_of_day_balances_over_calendar_days() -> None:
    rows = [
        {"posted_date": {"value": date(2026, 7, 2)}, "description": {"value": "CARD SETTLEMENT X"},
         "deposit": {"value": 50.0}, "balance": {"value": 150.0}},
        {"posted_date": {"value": date(2026, 7, 4)}, "description": {"value": "RENT"},
         "withdrawal": {"value": 30.0}, "balance": {"value": 120.0}},
    ]  # fmt: skip
    # End-of-day balances: 100, 150, 150, 120 -> mean 130.
    assert average_daily_balance([tiny_statement(rows)]) == 130.0


def test_average_daily_balance_runs_the_balance_when_the_column_is_missing() -> None:
    rows = [
        {"posted_date": {"value": date(2026, 7, 2)}, "description": {"value": "CARD SETTLEMENT X"},
         "deposit": {"value": 50.0}},
        {"posted_date": {"value": date(2026, 7, 4)}, "description": {"value": "RENT"}, "withdrawal": {"value": 30.0}},
    ]  # fmt: skip
    assert average_daily_balance([tiny_statement(rows)]) == 130.0


def test_nsf_events_count_only_the_last_six_months() -> None:
    data = credit_packet_data()
    for index in (0, 6, 8, 11):  # 2025-08 is outside the window; 2026-02, 2026-04 and 2026-07 are inside
        year, month = (int(part) for part in MONTHS[index].split("-"))
        fee = {"posted_date": {"value": date(year, month, 28)}, "description": {"value": "NSF FEE"},
               "withdrawal": {"value": 35.0}}  # fmt: skip
        data["statements"][index]["transactions"].append(fee)
    assert nsf_events(loan(data).statements) == 3


def test_negative_months_use_the_closing_balance() -> None:
    data = credit_packet_data()
    for index in (3, 9):
        data["statements"][index]["closing_balance"]["value"] = -50.0
    assert negative_balance_months(loan(data).statements) == 2


def test_observed_loan_payments_and_mca_remittances_are_monthly_averages() -> None:
    extra = ((16, "MCA REMIT RAPIDLEAF MERCHANT FUNDING", None, 600.0),)
    statements = loan(credit_packet_data(extra_rows=extra)).statements
    assert average_monthly_loan_payments(statements) == 1_250.0
    assert average_monthly_mca_remittance(statements) == 600.0


def test_new_loans_count_new_loans_and_mca_fundings_but_not_credit_line_draws() -> None:
    data = credit_packet_data()
    july = data["statements"][-1]["transactions"]
    for day, description in ((5, "LOAN PROCEEDS BLUESTEM COMMERCIAL LENDING"), (6, "MCA FUNDING RAPIDLEAF"),
                             (7, "LINE OF CREDIT DRAW")):  # fmt: skip
        july.append({"posted_date": {"value": date(2026, 7, day)}, "description": {"value": description},
                     "deposit": {"value": 10_000.0}})  # fmt: skip
    data["statements"][0]["transactions"].append(
        {"posted_date": {"value": date(2025, 8, 5)}, "description": {"value": "LOAN PROCEEDS OLD"},
         "deposit": {"value": 10_000.0}}
    )  # fmt: skip
    assert new_loans_last_90_days(loan(data).statements, date(2026, 9, 1)) == 2
