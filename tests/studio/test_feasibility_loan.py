"""Loan schedule: hand-worked payments and balances, and the same debt service as the lender side."""

import numpy as np
import pytest

from uwcopilot.agent.credit_calculations import proposed_annual_debt_service
from uwcopilot.studio.feasibility.loan import (
    annual_debt_service,
    monthly_payment,
    payment_matrix,
    payment_schedule,
    remaining_balance,
)


def test_monthly_payment_hand_worked() -> None:
    # 100,000 at 12% a year (1% a month) over 12 months: 1,000 / (1 - 1.01^-12) = 8,884.88.
    assert monthly_payment(100_000, 12.0, 12) == pytest.approx(8884.88, abs=0.01)
    assert monthly_payment(120_000, 0.0, 24) == pytest.approx(5000.0)


def test_annual_debt_service_is_the_lender_formula() -> None:
    assert annual_debt_service(250_000, 9.25, 120) == pytest.approx(
        proposed_annual_debt_service("term_loan", 250_000, 9.25, 120)
    )
    assert annual_debt_service(100_000, 12.0, 12) == pytest.approx(12 * 8884.88, abs=0.12)


def test_remaining_balance_two_ways() -> None:
    payment = monthly_payment(100_000, 12.0, 12)
    # After 6 payments the balance is the present value of the 6 payments still due.
    present_value = payment * (1 - 1.01**-6) / 0.01
    assert remaining_balance(100_000, 12.0, 12, 6) == pytest.approx(present_value, abs=0.01)
    assert remaining_balance(100_000, 12.0, 12, 0) == pytest.approx(100_000)
    assert remaining_balance(100_000, 12.0, 12, 12) == 0.0
    assert remaining_balance(120_000, 0.0, 24, 6) == pytest.approx(90_000)


def test_schedule_stops_after_the_term() -> None:
    schedule = payment_schedule(50_000, 8.0, 24, 36)
    assert np.all(schedule[:24] == pytest.approx(monthly_payment(50_000, 8.0, 24)))
    assert np.all(schedule[24:] == 0)


def repaid_balance(principal: float, rates_pct: np.ndarray, schedule: np.ndarray) -> float:
    """Roll a balance forward month by month with each month's rate and payment."""
    balance = principal
    for rate, payment in zip(rates_pct, schedule, strict=True):
        balance = balance * (1 + rate / 1200.0) - payment
    return balance


def test_rate_shock_reamortizes_and_still_repays_the_loan() -> None:
    schedule = payment_schedule(100_000, 6.0, 36, 36, shock_month=12, shock_add_pct=2.0)
    base = monthly_payment(100_000, 6.0, 36)
    assert schedule[:12] == pytest.approx([base] * 12)
    assert schedule[12] > base and schedule[12:] == pytest.approx([schedule[12]] * 24)
    rates = np.array([6.0] * 12 + [8.0] * 24)
    assert repaid_balance(100_000, rates, schedule) == pytest.approx(0.0, abs=0.01)


def test_payment_matrix_changes_only_shocked_paths() -> None:
    matrix = payment_matrix(100_000, 6.0, 60, 36, np.array([-1, 5, -1]), np.array([1.0, 1.5, 3.0]))
    base = payment_schedule(100_000, 6.0, 60, 36)
    assert matrix.shape == (3, 36)
    assert matrix[0] == pytest.approx(base) and matrix[2] == pytest.approx(base)
    assert matrix[1][:5] == pytest.approx(base[:5]) and matrix[1][5] > base[5]
