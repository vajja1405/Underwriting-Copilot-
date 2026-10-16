"""Financial statements of a SYNTHETIC loan case, worked backwards from a target decision DSCR.

The decision DSCR (NBC-4.1) is NOI / annual debt service, where NOI is P&L EBITDA reduced by any amount the P&L
revenue exceeds verified revenue. The generator draws a target DSCR from a mixture (40% within +-0.05 of the
1.10 / 1.25 / 1.35 thresholds, 40% in 1.5-3.0, 20% in 0.8-1.1), computes the annual debt service from the
as-documented debts and the requested loan, and sets EBITDA so the target is met. NOI is kept at or below 70% of
verified revenue; when the target would need more, the requested amount is reduced (as in agent/synthetic_credit).

Cases in the "comfortable" group are also made stress-safe the way agent/synthetic_credit.target_noi does it: when
the NBC-4.5 stress tests apply (a line of credit, or more than $100,000), NOI is raised to at least 1.08 x the
annual debt service plus 20% of verified revenue, and for a variable rate to at least 1.20 x the service at the
note rate + 2.00 points. Their DSCR is therefore the drawn 1.5-3.0 or higher. The other groups are not adjusted:
a DSCR near 1.25 cannot pass the revenue stress test (it would need NOI above verified revenue), so they refer.
"""

import random
from datetime import date, timedelta
from typing import Protocol

from uwcopilot.agent.credit_calculations import ADVANCE_RATES, proposed_annual_debt_service
from uwcopilot.agent.policy_credit import (
    PRODUCT_LIMITS,
    RATE_STRESS_ADD_PCT,
    REVENUE_STRESS_SHARE,
    STRESS_AMOUNT_ABOVE,
)
from uwcopilot.docintel.synthetic.common import APPRAISERS, cents, round_to, weighted_choice
from uwcopilot.docintel.synthetic.credit_case import (
    BalanceSheetData,
    CollateralData,
    Debt,
    ProfitAndLossData,
    TaxYear,
)

MAX_NOI_SHARE_OF_REVENUE = 0.70
# Safety margins of agent/synthetic_credit.target_noi, so stress-safe cases pass NBC-4.5 with room to spare.
STRESS_SAFETY_MARGIN = 1.08
RATE_STRESS_SAFETY_MARGIN = 1.20
THRESHOLDS = (1.10, 1.25, 1.35)
COLLATERAL_ABOVE = 250_000


class LoanTerms(Protocol):
    """What the DSCR solve needs from a loan request (credit_build.LoanRequest)."""

    product: str
    amount: float
    term_months: int
    rate_pct: float
    rate_type: str


PNL_NOTES: tuple[str, ...] = (
    "Prepared by management from the company's accounting records.",
    "Amounts are unaudited and stated in US dollars.",
)


def draw_dscr_target(rng: random.Random) -> tuple[float, bool]:
    """(target decision DSCR, stress_safe): the documented mixture; only the comfortable group is stress-safe."""
    group = weighted_choice(rng, {"near_threshold": 0.40, "comfortable": 0.40, "weak": 0.20})
    if group == "near_threshold":
        return round(rng.choice(THRESHOLDS) + rng.uniform(-0.05, 0.05), 3), False
    if group == "comfortable":
        return round(rng.uniform(1.5, 3.0), 3), True
    return round(rng.uniform(0.8, 1.1), 3), False


def existing_annual_service(schedule_payment: float, observed_payment: float, mca_payment: float) -> float:
    """NBC-4.1 item 1: 12 x (higher of scheduled and observed payment + MCA remittances); no payoff letters."""
    return 12.0 * (max(schedule_payment, observed_payment) + mca_payment)


def stress_applies(product: str, amount: float) -> bool:
    """NBC-4.5 scope: every line of credit and every request above $100,000."""
    return product == "line_of_credit" or amount > STRESS_AMOUNT_ABOVE


def required_noi(
    request: LoanTerms, amount: float, existing: float, verified: float, dscr: float, stress_safe: bool
) -> float:
    """NOI that gives the target DSCR, raised for a stress-safe case so it passes both NBC-4.5 stress tests."""
    service = existing + proposed_annual_debt_service(request.product, amount, request.rate_pct, request.term_months)
    noi = dscr * service
    if stress_safe and stress_applies(request.product, amount):
        noi = max(noi, STRESS_SAFETY_MARGIN * service + REVENUE_STRESS_SHARE * verified)
        if request.rate_type == "variable":
            stressed = existing + proposed_annual_debt_service(
                request.product, amount, request.rate_pct + RATE_STRESS_ADD_PCT, request.term_months
            )
            noi = max(noi, RATE_STRESS_SAFETY_MARGIN * stressed)
    return noi


def solve_amount_and_noi(
    request: LoanTerms, existing: float, verified: float, dscr: float, stress_safe: bool
) -> tuple[float, float]:
    """(requested amount, NOI) with NOI at most 70% of verified revenue.

    When the cap binds, the amount steps down in $1,000 until the required NOI fits (never below the NBC-1.1
    product minimum; then NOI is the cap and the case ends up below its target).
    """
    cap = MAX_NOI_SHARE_OF_REVENUE * verified
    amount = request.amount
    if required_noi(request, amount, existing, verified, dscr, stress_safe) > cap:
        minimum = float(PRODUCT_LIMITS[request.product][0])
        thousands = int(amount // 1_000)
        amount = minimum
        while thousands * 1_000.0 > minimum:
            if required_noi(request, thousands * 1_000.0, existing, verified, dscr, stress_safe) <= cap:
                amount = thousands * 1_000.0
                break
            thousands -= 1
    noi = min(required_noi(request, amount, existing, verified, dscr, stress_safe), cap)
    return amount, cents(noi)


def build_pnl(
    rng: random.Random, period_end: date, revenue: float, ebitda: float, loans: list[Debt], fixed_assets: float
) -> ProfitAndLossData:
    """A trailing-twelve-month P&L with the given revenue and EBITDA (COGS and operating expenses fill the rest)."""
    upper = min(0.55, 1.0 - ebitda / revenue - 0.08)
    cogs_share = rng.uniform(0.15, upper) if upper > 0.15 else max(0.0, upper)
    cogs = cents(revenue * cogs_share)
    operating = cents(revenue - cogs - ebitda)
    depreciation = cents(fixed_assets * rng.uniform(0.08, 0.15))
    interest = cents(sum(debt.balance for debt in loans) * rng.uniform(0.06, 0.10))
    return ProfitAndLossData(
        period_end=period_end,
        revenue=cents(revenue),
        cost_of_goods_sold=cogs,
        operating_expenses=operating,
        ebitda=cents(ebitda),
        depreciation_amortization=depreciation,
        interest_expense=interest,
        net_income=cents(ebitda - depreciation - interest),
        notes=list(PNL_NOTES),
    )


def build_balance_sheet(
    rng: random.Random, as_of: date, operating_cash: float, revenue: float, loans: list[Debt], fixed_assets: float
) -> BalanceSheetData:
    """A balance sheet at the P&L date: cash includes a savings balance; long-term debt is the scheduled loans."""
    cash = cents(max(0.0, operating_cash) + revenue / 12 * rng.uniform(0.05, 0.5))
    receivables = cents(revenue * rng.uniform(0.02, 0.12))
    inventory = cents(revenue * rng.uniform(0.0, 0.10))
    total_assets = cents(cash + receivables + inventory + fixed_assets)
    payables = cents(revenue * rng.uniform(0.02, 0.08))
    long_term = cents(sum(debt.balance for debt in loans))
    liabilities = cents(payables + long_term)
    return BalanceSheetData(
        as_of=as_of,
        cash=cash,
        accounts_receivable=receivables,
        inventory=inventory,
        fixed_assets=cents(fixed_assets),
        total_assets=total_assets,
        accounts_payable=payables,
        long_term_debt=long_term,
        total_liabilities=liabilities,
        equity=cents(total_assets - liabilities),
    )


def build_tax_years(rng: random.Random, latest_receipts: float, application_date: date) -> list[TaxYear]:
    """The two most recent tax years (calendar years), oldest first; the prior year grew by -5% to +12%."""
    latest_year = application_date.year - 1
    prior = latest_receipts / (1.0 + rng.uniform(-0.05, 0.12))
    return [TaxYear(latest_year - 1, float(round(prior))), TaxYear(latest_year, float(round(latest_receipts)))]


def collateral_type(rng: random.Random, product: str) -> str:
    """The collateral that goes with a product."""
    if product == "term_loan":
        return "owner_occupied_cre"
    if product == "equipment_loan":
        return "new_equipment" if rng.random() < 0.8 else "used_equipment"
    return "accounts_receivable" if rng.random() < 0.7 else "inventory"


COLLATERAL_DESCRIPTIONS = {
    "owner_occupied_cre": "Owner-occupied commercial building at the business address",
    "new_equipment": "New production equipment to be purchased with the loan",
    "used_equipment": "Used production equipment owned by the business",
    "accounts_receivable": "Eligible accounts receivable under 90 days",
    "inventory": "Finished goods inventory at the business premises",
}


def build_collateral(rng: random.Random, product: str, amount: float, application_date: date) -> list[CollateralData]:
    """Collateral sized to a coverage of 1.05-1.80 (15% of cases 0.80-0.99) when the request is above $250,000."""
    if amount <= COLLATERAL_ABOVE:
        return []
    kind = collateral_type(rng, product)
    coverage = rng.uniform(0.80, 0.99) if rng.random() < 0.15 else rng.uniform(1.05, 1.80)
    value = round_to(coverage * amount / ADVANCE_RATES[kind], 1_000)
    return [
        CollateralData(
            collateral_type=kind,
            description=COLLATERAL_DESCRIPTIONS[kind],
            value=value,
            valuation_date=application_date - timedelta(days=rng.randint(10, 120)),
            appraiser=rng.choice(APPRAISERS),
        )
    ]
