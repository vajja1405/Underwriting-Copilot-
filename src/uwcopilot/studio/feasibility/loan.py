"""Loan schedule for the simulator, built on the lender side's own formulas (agent/credit_calculations.py).

The level monthly payment and the annual debt service come from amortizing_monthly_payment and
proposed_annual_debt_service, the functions the lender copilot uses for NBC-4.1 and NSP-1.2, so the founder and the
lender compute debt service the same way. This module adds only what the lender side does not need: the remaining
balance after some payments, and a payment schedule that changes when the rate of a variable-rate loan rises
(the rest of the loan is re-amortized over the remaining term at the new rate).
"""

import numpy as np

from uwcopilot.agent.credit_calculations import amortizing_monthly_payment, proposed_annual_debt_service


def monthly_payment(principal: float, annual_rate_pct: float, term_months: int) -> float:
    """The level payment of a fully amortizing term loan (credit_calculations.amortizing_monthly_payment)."""
    return amortizing_monthly_payment(principal, annual_rate_pct, term_months)


def annual_debt_service(principal: float, annual_rate_pct: float, term_months: int) -> float:
    """12 level payments: the NBC-4.1 / NSP-1.2 projected annual debt service of a term loan."""
    return proposed_annual_debt_service("term_loan", principal, annual_rate_pct, term_months)


def remaining_balance(principal: float, annual_rate_pct: float, term_months: int, payments_made: int) -> float:
    """Principal still owed after payments_made level payments (0 once the loan is repaid).

    Balance after k payments = P x (1 + r)^k - payment x ((1 + r)^k - 1) / r, with r the monthly rate.
    """
    if payments_made >= term_months:
        return 0.0
    payment = monthly_payment(principal, annual_rate_pct, term_months)
    rate = annual_rate_pct / 100.0 / 12.0
    if rate == 0:
        return max(principal - payment * payments_made, 0.0)
    growth = (1.0 + rate) ** payments_made
    return max(principal * growth - payment * (growth - 1.0) / rate, 0.0)


def payment_schedule(
    principal: float,
    annual_rate_pct: float,
    term_months: int,
    months: int,
    shock_month: int = -1,
    shock_add_pct: float = 0.0,
) -> np.ndarray:
    """Monthly payments for the first `months` months (0 after the last payment).

    shock_month >= 0: from that month on, the remaining balance is re-amortized over the remaining term at
    annual_rate_pct + shock_add_pct (a rate rise on a variable-rate loan).
    """
    schedule = np.zeros(months)
    paid_months = min(months, term_months)
    schedule[:paid_months] = monthly_payment(principal, annual_rate_pct, term_months)
    if 0 <= shock_month < paid_months and shock_add_pct != 0:
        balance = remaining_balance(principal, annual_rate_pct, term_months, shock_month)
        new_payment = monthly_payment(balance, annual_rate_pct + shock_add_pct, term_months - shock_month)
        schedule[shock_month:paid_months] = new_payment
    return schedule


def payment_matrix(
    principal: float,
    annual_rate_pct: float,
    term_months: int,
    months: int,
    shock_months: np.ndarray,
    shock_add_pct: np.ndarray,
) -> np.ndarray:
    """(paths x months) payments: the base schedule, re-amortized from each path's shock month (-1 = no shock)."""
    base = payment_schedule(principal, annual_rate_pct, term_months, months)
    matrix = np.tile(base, (len(shock_months), 1))
    for path in np.flatnonzero(shock_months >= 0):
        matrix[path] = payment_schedule(
            principal, annual_rate_pct, term_months, months, int(shock_months[path]), float(shock_add_pct[path])
        )
    return matrix
