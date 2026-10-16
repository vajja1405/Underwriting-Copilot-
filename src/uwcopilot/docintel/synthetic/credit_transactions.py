"""Twelve months of SYNTHETIC operating-account transactions, with the true category of every row.

build_statements() works from targets:
- eligible revenue per month (as documented: the sum of revenue rows, card settlements, ACH credits, check deposits
  and cash under the NBC-3.3 cap), with up to +-15% seasonality, adding up exactly to the target verified revenue;
- extra deposits (non-revenue rows that are correctly described, unidentified "DEPOSIT REF" rows, loan and MCA
  proceeds), each with the category its description documents and its true category;
- debt payments ("ACH PMT <lender> LOAN"), MCA remittances, NSF fees, and ordinary expenses sized so the month
  closes near a target balance (below zero in the months planned to close negative).

Descriptions are chosen so docintel/deposits.py classifies each row into the documented category; a test checks
this on every generated row. Dates are business days inside the month.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from uwcopilot.docintel.synthetic.common import cents, month_end
from uwcopilot.docintel.synthetic.credit_case import Debt, Statement, Txn

MAX_ROWS = 24
NSF_FEE = 35.00


@dataclass
class ExtraDeposit:
    """A planned non-revenue (or unidentified) deposit in one statement month."""

    month_index: int
    description: str
    amount: float
    category: str
    true_category: str
    posted: date | None = None


@dataclass
class StatementPlan:
    """Targets for the statements of one case."""

    months: list[date]
    eligible: list[float]
    cash_share: float
    card_business: bool
    card_processor: str
    customers: list[str]
    suppliers: list[str]
    landlord: str
    payroll_provider: str
    utility: str
    debts: list[Debt]
    nsf_by_month: list[int]
    negative_months: list[bool]
    balance_level: float
    rent: float
    extra_deposits: list[ExtraDeposit] = field(default_factory=list)


def business_days(start: date, end: date) -> list[date]:
    """Monday-to-Friday dates from start to end inclusive."""
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def nearest_business_day(month_start: date, day_of_month: int) -> date:
    """The first business day on or after the given day of the month (or the last one of the month)."""
    end = month_end(month_start)
    day = month_start.replace(day=min(day_of_month, end.day))
    candidates = business_days(day, end) or business_days(month_start, end)
    return candidates[0] if candidates[0] >= day else candidates[-1]


def split_amount(rng: random.Random, total: float, parts: int) -> list[float]:
    """Split a dollar total into `parts` positive cent amounts that add up exactly."""
    if parts <= 1:
        return [cents(total)]
    weights = [rng.uniform(0.5, 1.5) for _ in range(parts)]
    amounts = [cents(total * weight / sum(weights)) for weight in weights]
    amounts[-1] = cents(total - sum(amounts[:-1]))
    return amounts


def seasonal_split(rng: random.Random, total: float, months: int) -> list[float]:
    """Monthly amounts with up to +-15% seasonality that add up exactly to total."""
    phase = rng.uniform(0, 12)
    strength = rng.uniform(0.05, 0.15)
    weights = []
    for index in range(months):
        wave = 1.0 + strength * (2.0 * abs(((index + phase) % 12) / 6.0 - 1.0) - 1.0)
        weights.append(wave * rng.uniform(0.97, 1.03))
    amounts = [cents(total * weight / sum(weights)) for weight in weights]
    amounts[-1] = cents(total - sum(amounts[:-1]))
    return amounts


class RowMaker:
    """Collects the rows of one month with increasing order numbers (the print order within a day)."""

    def __init__(self) -> None:
        self.rows: list[Txn] = []

    def add(self, posted: date, description: str, kind: str, amount: float, category: str, true: str = "") -> Txn:
        """Add one row; true defaults to the documented category."""
        row = Txn(posted, description, kind, cents(amount), category, true or category, order=len(self.rows))
        self.rows.append(row)
        return row


def revenue_rows(rng: random.Random, plan: StatementPlan, index: int, maker: RowMaker, slots: int) -> None:
    """Revenue deposits for one month: cash first, then card settlements / ACH credits / check deposits."""
    start = plan.months[index]
    days = business_days(start, month_end(start))
    total = plan.eligible[index]
    cash = cents(total * plan.cash_share) if plan.cash_share > 0 else 0.0
    cash_rows = 0 if cash <= 0 else min(2, max(1, slots // 6))
    for amount in split_amount(rng, cash, cash_rows) if cash_rows else []:
        maker.add(rng.choice(days), "CASH DEPOSIT", "D", amount, "cash_revenue")
    non_cash = cents(total - (cash if cash_rows else 0.0))
    count = max(2, slots - cash_rows)
    for amount in split_amount(rng, non_cash, count):
        draw = rng.random()
        if plan.card_business and draw < 0.7:
            description = f"CARD SETTLEMENT {plan.card_processor}"
        elif draw < 0.85:
            description = f"ACH CREDIT {rng.choice(plan.customers)} INV {rng.randint(10000, 99999)}"
        elif draw < 0.95:
            description = "MOBILE CHECK DEPOSIT"
        else:
            description = f"INCOMING WIRE {rng.choice(plan.customers)}"
        maker.add(rng.choice(days), description, "D", amount, "revenue")


def extra_rows(rng: random.Random, plan: StatementPlan, index: int, maker: RowMaker) -> None:
    """The planned extra deposits of the month (transfers, refunds, DEPOSIT REF, proceeds, ...)."""
    start = plan.months[index]
    days = business_days(start, month_end(start))
    for item in plan.extra_deposits:
        if item.month_index == index:
            posted = item.posted or rng.choice(days)
            maker.add(posted, item.description, "D", item.amount, item.category, item.true_category)


def debt_rows(plan: StatementPlan, index: int, maker: RowMaker) -> None:
    """Loan payments, MCA proceeds and remittances, and loan proceeds for the month."""
    start = plan.months[index]
    for debt in plan.debts:
        lender = debt.lender.upper()
        if debt.funded_on is not None and debt.funded_on.year == start.year and debt.funded_on.month == start.month:
            if debt.kind == "mca":
                maker.add(debt.funded_on, f"MCA FUNDING {lender}", "D", debt.funded_amount, "loan_proceeds")
            else:
                maker.add(debt.funded_on, f"LOAN PROCEEDS {lender}", "D", debt.funded_amount, "loan_proceeds")
        if index < debt.first_payment_index:
            continue
        if debt.kind == "mca":
            half = cents(debt.observed_payment / 2)
            maker.add(nearest_business_day(start, 1), f"MCA REMIT {lender}", "W", half, "mca_remittance")
            second = cents(debt.observed_payment - half)
            maker.add(nearest_business_day(start, 15), f"MCA REMIT {lender}", "W", second, "mca_remittance")
            continue
        posted = nearest_business_day(start, debt.payment_day)
        maker.add(posted, f"ACH PMT {lender} LOAN", "W", debt.observed_payment, "loan_payment")
        if debt.extra_payment_index == index:
            extra_day = nearest_business_day(start, min(debt.payment_day + 7, 27))
            maker.add(extra_day, f"ACH PMT {lender} LOAN", "W", debt.extra_payment, "loan_payment")


def fixed_rows(rng: random.Random, plan: StatementPlan, index: int, maker: RowMaker) -> None:
    """Rent, card processing fees and NSF / overdraft fees."""
    start = plan.months[index]
    maker.add(nearest_business_day(start, 1), f"ACH DEBIT RENT {plan.landlord}", "W", plan.rent, "other")
    if plan.card_business:
        settled = sum(row.amount for row in maker.rows if row.description.startswith("CARD SETTLEMENT"))
        if settled > 0:
            fee = cents(settled * rng.uniform(0.018, 0.029))
            maker.add(
                month_end(start) - timedelta(days=2), f"CARD PROCESSING FEE {plan.card_processor}", "W", fee, "other"
            )
    for number in range(plan.nsf_by_month[index]):
        posted = nearest_business_day(start, rng.randint(5, 26))
        label = "NSF FEE" if number % 2 == 0 else "OVERDRAFT FEE"
        maker.add(posted, label, "W", NSF_FEE, "nsf_fee")


def expense_rows(
    rng: random.Random, plan: StatementPlan, index: int, maker: RowMaker, total: float, slots: int
) -> None:
    """Payroll, suppliers and utilities that add up to `total` (the rest of the month's spending)."""
    start = plan.months[index]
    days = business_days(start, month_end(start))
    payroll = cents(total * rng.uniform(0.40, 0.55))
    utilities = cents(total * rng.uniform(0.04, 0.09))
    suppliers = cents(total - payroll - utilities)
    for day, amount in zip((1, 15), split_amount(rng, payroll, 2), strict=True):
        maker.add(nearest_business_day(start, day), f"ACH DEBIT {plan.payroll_provider}", "W", amount, "other")
    maker.add(nearest_business_day(start, rng.randint(8, 22)), f"{plan.utility} AUTOPAY", "W", utilities, "other")
    supplier_rows = max(1, min(3, slots - 3))
    for amount in split_amount(rng, suppliers, supplier_rows):
        if rng.random() < 0.25:
            description = f"CHECK {rng.randint(1001, 4999)}"
        else:
            description = f"ACH DEBIT {rng.choice(plan.suppliers)}"
        maker.add(rng.choice(days), description, "W", amount, "other")


def closing_target(rng: random.Random, plan: StatementPlan, index: int) -> float:
    """Where the month should close: below zero when planned negative, otherwise near the balance level."""
    if plan.negative_months[index]:
        return -cents(rng.uniform(200.0, 3_000.0))
    return cents(plan.balance_level * rng.uniform(0.7, 1.3))


def build_month(rng: random.Random, plan: StatementPlan, index: int, opening: float) -> Statement:
    """One statement: deposits, debt rows, fixed costs, then expenses sized toward the closing target."""
    start = plan.months[index]
    maker = RowMaker()
    debt_rows(plan, index, maker)
    extra_rows(rng, plan, index, maker)
    planned_fixed = 1 + plan.nsf_by_month[index] + (1 if plan.card_business else 0)
    used = len(maker.rows) + planned_fixed + 5
    revenue_slots = max(3, min(rng.randint(6, 11), MAX_ROWS - used))
    revenue_rows(rng, plan, index, maker, revenue_slots)
    fixed_rows(rng, plan, index, maker)
    deposits = sum(row.amount for row in maker.rows if row.kind == "D")
    withdrawals = sum(row.amount for row in maker.rows if row.kind == "W")
    variable = opening + deposits - withdrawals - closing_target(rng, plan, index)
    floor = 0.3 * plan.eligible[index]
    if variable < floor:
        variable = floor
    expense_slots = max(4, min(6, MAX_ROWS - len(maker.rows)))
    expense_rows(rng, plan, index, maker, cents(variable), expense_slots)
    rows = sorted(maker.rows, key=lambda row: (row.posted, 0 if row.kind == "D" else 1, row.order))
    for position, row in enumerate(rows):
        row.order = position
    statement = Statement(
        month=start, period_start=start, period_end=month_end(start), opening=cents(opening), rows=rows
    )
    running = statement.opening
    for row in rows:
        running = cents(running + row.amount if row.kind == "D" else running - row.amount)
        row.balance = running
    return statement


def build_statements(rng: random.Random, plan: StatementPlan) -> list[Statement]:
    """The 12 statements, oldest first; each opens at the previous month's closing balance."""
    opening = cents(plan.balance_level * rng.uniform(0.6, 1.4))
    statements = []
    for index in range(len(plan.months)):
        statement = build_month(rng, plan, index, opening)
        statements.append(statement)
        opening = statement.closing
    return statements
