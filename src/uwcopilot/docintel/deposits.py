"""Bank statement transactions: deposit and withdrawal categories (NBC-3.3) and account-conduct figures (NBC-3.5).

Deposit categories come from documented keyword rules over the transaction description (DEPOSIT_RULES below),
checked in order; the first rule that matches wins:

1. owner_contribution - "OWNER CAPITAL", "CAPITAL CONTRIBUTION", "LOAN FROM OWNER", "SHAREHOLDER LOAN", ...
2. one_time           - "TAX REFUND", "INSURANCE CLAIM", "LEGAL SETTLEMENT", "SALE OF ASSET", "GOVERNMENT GRANT", ...
3. loan_proceeds      - "LOAN PROCEEDS", "MCA FUNDING", "CASH ADVANCE", "LINE OF CREDIT DRAW", ...
4. transfer           - the word TRANSFER or XFER ("ONLINE TRANSFER FROM SAVINGS ...1234")
5. refund             - REFUND, REVERSAL, CHARGEBACK, RETURN / RETURNED
6. cash_revenue       - "CASH DEPOSIT", "CASH DEP"
7. revenue            - "CARD SETTLEMENT", "ACH CREDIT", "CHECK DEPOSIT", "INCOMING WIRE", "INVOICE"/"INV", ...
8. unidentified       - everything else, for example "DEPOSIT REF 88213"

The non-revenue rules come first on purpose: NBC-3.3 excludes a deposit whose description shows it is a transfer,
refund, loan, owner contribution or one-time receipt, and "When the nature of a deposit cannot be determined from
the statement, it is treated as non-revenue until the applicant documents otherwise" (rule 8). Phrases are used
instead of single words where a single word is also a common name (for example GRANT, CAPITAL, SETTLEMENT).

Withdrawal categories (WITHDRAWAL_RULES): nsf_fee ("NSF", "OVERDRAFT", "INSUFFICIENT FUNDS"), mca_remittance
("MCA", "MERCHANT CASH ADVANCE"), loan_payment (the word LOAN, "NOTE PAYMENT", "LINE OF CREDIT PAYMENT"), other.

Figures computed here, with the exact rules (the packet generator computes the as-documented application with
the same rules, so they are written down precisely):
- monthly_deposits: one agent MonthlyDeposits dict per statement. total_deposits is the sum of the deposit rows
  (the printed "Total deposits" when the statement has no rows; then the whole amount is unidentified, because
  no deposit can be classified). Each category total is the sum of its rows; cash_deposits is the cash_revenue
  sum. All amounts are rounded to cents.
- statement windows: "the last N months" means the statements whose month is within N months of the most recent
  statement month present (month index > latest - N), so a missing month shortens the window.
- nsf_events: number of nsf_fee withdrawal rows in the last 6 months.
- negative_balance_months: statements in the last 12 months whose closing balance is below zero (the printed
  closing balance, or the last row's balance when the closing balance is missing).
- average_daily_balance: mean of the end-of-day balance over every calendar day (period start to period end) of
  the statements in the last 3 months. The end-of-day balance is the running balance after the last row posted
  on or before that day (the printed balance column when present, otherwise the previous balance plus the deposit
  minus the withdrawal), or the opening balance before the first row. Rounded to cents.
- average_monthly_loan_payments / average_monthly_mca_remittance: NBC-4.1 counts these "at their observed monthly
  total", so the sum of loan_payment (mca_remittance) rows is divided by the number of statement months in which
  at least one such row appears (months with a payment), not by every statement month: an advance funded in the
  last three months is not diluted by the nine months before it existed. (The debt_payment_consistency check
  averages each debt item the same way.) 0.0 when no month has a payment.
- new_loans_last_90_days: the number of NEW loans or advances (NBC-4.4 "new loans or merchant cash advance
  fundings"): loan_proceeds deposit rows whose description also matches NEW_LOAN_PATTERN (loan proceeds or
  disbursements, MCA fundings and advances; not line-of-credit draws or card cash advances, which draw on credit
  that already exists), posted in the 90 calendar days that end on the as-of date (as_of - 89 days through as_of,
  both included). NBC-4.4 counts "the most recent 90 days of statements", so the as-of date is the end of the most
  recent statement period (last_period_end); the generator uses the same date.
"""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from uwcopilot.agent.credit_calculations import month_index
from uwcopilot.docintel.normalize import normalize_text
from uwcopilot.docintel.schemas import BankStatement, Transaction

DepositCategory = Literal[
    "revenue", "cash_revenue", "transfer", "refund", "loan_proceeds", "owner_contribution", "one_time", "unidentified"
]
WithdrawalCategory = Literal["loan_payment", "mca_remittance", "nsf_fee", "other"]
RowKind = Literal["deposit", "withdrawal"]

DEPOSIT_RULES: tuple[tuple[DepositCategory, re.Pattern[str]], ...] = (
    (
        "owner_contribution",
        re.compile(
            r"\b(?:OWNER (?:CAPITAL|CONTRIBUTION|INVESTMENT|LOAN|DEPOSIT)|CAPITAL CONTRIBUTION|MEMBER CONTRIBUTION|"
            r"LOAN FROM (?:OWNER|MEMBER|SHAREHOLDER|AFFILIATE)|SHAREHOLDER LOAN)\b"
        ),
    ),
    (
        "one_time",
        re.compile(
            r"\b(?:TAX REFUND|TAX REF|IRS TREAS|INSURANCE CLAIM|INS CLAIM|LEGAL SETTLEMENT|LAWSUIT SETTLEMENT|"
            r"SALE OF (?:ASSET|ASSETS|EQUIPMENT|VEHICLE)|ASSET SALE|GOVERNMENT GRANT|GRANT PROCEEDS|RELIEF FUND)\b"
        ),
    ),
    (
        "loan_proceeds",
        re.compile(
            r"\b(?:LOAN PROCEEDS|LOAN DISBURSEMENT|LOAN DEPOSIT|MCA FUNDING|MCA ADVANCE|MERCHANT CASH ADVANCE|"
            r"CASH ADVANCE|ADVANCE FUNDING|LINE OF CREDIT DRAW|LOC DRAW|CREDIT LINE DRAW)\b"
        ),
    ),
    ("transfer", re.compile(r"\b(?:TRANSFER|XFER)\b")),
    ("refund", re.compile(r"\b(?:REFUND|REVERSAL|CHARGEBACK|RETURN|RETURNED)\b")),
    ("cash_revenue", re.compile(r"\b(?:CASH DEPOSIT|CASH DEP)\b")),
    (
        "revenue",
        re.compile(
            r"\b(?:CARD SETTLEMENT|MERCHANT SETTLEMENT|POS SETTLEMENT|BATCH SETTLEMENT|ACH CREDIT|"
            r"MOBILE CHECK DEPOSIT|CHECK DEPOSIT|REMOTE DEPOSIT|INCOMING WIRE|WIRE FROM|INVOICE|INV)\b"
        ),
    ),
)
WITHDRAWAL_RULES: tuple[tuple[WithdrawalCategory, re.Pattern[str]], ...] = (
    ("nsf_fee", re.compile(r"\b(?:NSF|OVERDRAFT|INSUFFICIENT FUNDS|OD FEE)\b")),
    ("mca_remittance", re.compile(r"\b(?:MCA|MERCHANT CASH ADVANCE)\b")),
    ("loan_payment", re.compile(r"\b(?:LOAN|NOTE PAYMENT|LINE OF CREDIT PAYMENT|LOC PMT)\b")),
)

# The MonthlyDeposits field (uwcopilot.agent.applications) that holds each excluded deposit category.
NON_REVENUE_FIELDS: dict[str, str] = {
    "transfer": "transfers_between_accounts",
    "refund": "refunds_and_reversals",
    "loan_proceeds": "loan_proceeds",
    "owner_contribution": "owner_contributions",
    "one_time": "one_time_receipts",
    "unidentified": "unidentified_deposits",
}
IDENTIFIED_NON_REVENUE: tuple[str, ...] = ("transfer", "refund", "loan_proceeds", "owner_contribution", "one_time")
# NBC-4.4 stacking counts new loans and MCA fundings, not draws on existing credit (see the module docstring).
NEW_LOAN_PATTERN = re.compile(
    r"\b(?:LOAN PROCEEDS|LOAN DISBURSEMENT|LOAN DEPOSIT|MCA FUNDING|MCA ADVANCE|MERCHANT CASH ADVANCE|"
    r"ADVANCE FUNDING)\b"
)
NSF_WINDOW_MONTHS = 6  # NBC-3.5: NSF events in the most recent 6 months
NEGATIVE_WINDOW_MONTHS = 12  # NBC-3.5: negative months within the most recent 12
ADB_WINDOW_MONTHS = 3  # NBC-3.5: average daily balance over the most recent 3 months
NEW_LOAN_WINDOW_DAYS = 90  # NBC-4.4: new loans in the last 90 days (stacking)


def description_text(description: str | None) -> str:
    """The description, normalized and upper-cased, ready for the keyword rules."""
    return normalize_text(description or "").upper()


def classify_deposit(description: str | None) -> DepositCategory:
    """The NBC-3.3 category of a deposit, from its description (see DEPOSIT_RULES)."""
    text = description_text(description)
    for category, pattern in DEPOSIT_RULES:
        if pattern.search(text):
            return category
    return "unidentified"


def classify_withdrawal(description: str | None) -> WithdrawalCategory:
    """The category of a withdrawal, from its description (see WITHDRAWAL_RULES)."""
    text = description_text(description)
    for category, pattern in WITHDRAWAL_RULES:
        if pattern.search(text):
            return category
    return "other"


@dataclass(frozen=True)
class ClassifiedRow:
    """One transaction row with its direction, amount and category."""

    transaction: Transaction
    kind: RowKind
    amount: float
    category: str


def classify_row(transaction: Transaction) -> ClassifiedRow | None:
    """Classify one row as a deposit or a withdrawal (None when the row has neither amount)."""
    description = transaction.description.value
    if transaction.deposit.value is not None:
        amount = float(transaction.deposit.value)
        return ClassifiedRow(transaction, "deposit", amount, classify_deposit(description))
    if transaction.withdrawal.value is not None:
        amount = float(transaction.withdrawal.value)
        return ClassifiedRow(transaction, "withdrawal", amount, classify_withdrawal(description))
    return None


def classify_rows(statement: BankStatement) -> list[ClassifiedRow]:
    """Every row of the statement that has an amount, classified, in printed order."""
    rows = []
    for transaction in statement.transactions:
        row = classify_row(transaction)
        if row is not None:
            rows.append(row)
    return rows


def month_of(statement: BankStatement) -> str | None:
    """The statement month "YYYY-MM": from the period start, else the period end, else the first dated row."""
    if statement.period_start.value is not None:
        return statement.period_start.value.strftime("%Y-%m")
    if statement.period_end.value is not None:
        return statement.period_end.value.strftime("%Y-%m")
    for transaction in statement.transactions:
        if transaction.posted_date.value is not None:
            return transaction.posted_date.value.strftime("%Y-%m")
    return None


def monthly_deposits(statement: BankStatement) -> dict[str, str | float] | None:
    """The agent's MonthlyDeposits for one statement as a dict (None when the month is unknown).

    See the module docstring for the rule. The dict validates as uwcopilot.agent.applications.MonthlyDeposits.
    """
    month = month_of(statement)
    if month is None:
        return None
    totals = {field: 0.0 for field in NON_REVENUE_FIELDS.values()}
    rows = [row for row in classify_rows(statement) if row.kind == "deposit"]
    cash = 0.0
    if rows:
        total = sum(row.amount for row in rows)
        for row in rows:
            if row.category in NON_REVENUE_FIELDS:
                totals[NON_REVENUE_FIELDS[row.category]] += row.amount
            elif row.category == "cash_revenue":
                cash += row.amount
    else:
        total = float(statement.total_deposits.value or 0.0)
        totals["unidentified_deposits"] = total
    result: dict[str, str | float] = {"month": month, "total_deposits": round(total, 2)}
    for field, amount in totals.items():
        result[field] = round(amount, 2)
    result["cash_deposits"] = round(cash, 2)
    return result


def sorted_by_month(statements: list[BankStatement]) -> list[BankStatement]:
    """Statements with a known month, oldest first (statements without a month are dropped)."""
    dated = [statement for statement in statements if month_of(statement) is not None]
    return sorted(dated, key=lambda statement: month_index(month_of(statement) or ""))


def statements_in_window(statements: list[BankStatement], months: int) -> list[BankStatement]:
    """Statements whose month is within `months` months of the most recent statement month present."""
    ordered = sorted_by_month(statements)
    if not ordered:
        return []
    latest = month_index(month_of(ordered[-1]) or "")
    return [statement for statement in ordered if month_index(month_of(statement) or "") > latest - months]


def nsf_events(statements: list[BankStatement], months: int = NSF_WINDOW_MONTHS) -> int:
    """Number of NSF / overdraft fee rows in the statements of the last `months` months."""
    count = 0
    for statement in statements_in_window(statements, months):
        count += sum(1 for row in classify_rows(statement) if row.category == "nsf_fee")
    return count


def closing_balance(statement: BankStatement) -> float | None:
    """The printed closing balance, or the last row's printed balance when it is missing."""
    if statement.closing_balance.value is not None:
        return float(statement.closing_balance.value)
    for transaction in reversed(statement.transactions):
        if transaction.balance.value is not None:
            return float(transaction.balance.value)
    return None


def negative_balance_months(statements: list[BankStatement], months: int = NEGATIVE_WINDOW_MONTHS) -> int:
    """Number of statements in the last `months` months that close below zero."""
    count = 0
    for statement in statements_in_window(statements, months):
        balance = closing_balance(statement)
        if balance is not None and balance < 0:
            count += 1
    return count


def period_bounds(statement: BankStatement) -> tuple[date, date] | None:
    """The statement period; a missing start or end is taken from the calendar month."""
    month = month_of(statement)
    if month is None:
        return None
    year, month_number = (int(part) for part in month.split("-"))
    start = statement.period_start.value or date(year, month_number, 1)
    end = statement.period_end.value or date(year, month_number, monthrange(year, month_number)[1])
    if end < start:
        return None
    return start, end


def opening_balance(statement: BankStatement) -> float | None:
    """The printed opening balance, or the balance before the first row worked back from that row."""
    if statement.opening_balance.value is not None:
        return float(statement.opening_balance.value)
    for transaction in statement.transactions:
        if transaction.balance.value is None:
            continue
        deposit = float(transaction.deposit.value or 0.0)
        withdrawal = float(transaction.withdrawal.value or 0.0)
        return float(transaction.balance.value) - deposit + withdrawal
    return None


def end_of_day_balances(statement: BankStatement) -> list[float] | None:
    """The end-of-day balance for every calendar day of the statement period (None when it cannot be built)."""
    bounds = period_bounds(statement)
    current = opening_balance(statement)
    if bounds is None or current is None:
        return None
    start, end = bounds
    dated = [row for row in statement.transactions if row.posted_date.value is not None]
    dated.sort(key=lambda row: row.posted_date.value)  # stable: printed order is kept within a day
    balances = []
    position = 0
    day = start
    while day <= end:
        while position < len(dated) and dated[position].posted_date.value <= day:
            row = dated[position]
            if row.balance.value is not None:
                current = float(row.balance.value)
            else:
                current += float(row.deposit.value or 0.0) - float(row.withdrawal.value or 0.0)
            position += 1
        balances.append(current)
        day += timedelta(days=1)
    return balances


def average_daily_balance(statements: list[BankStatement], months: int = ADB_WINDOW_MONTHS) -> float | None:
    """Mean end-of-day balance over the days of the last `months` statement months (None if none can be built)."""
    days: list[float] = []
    for statement in statements_in_window(statements, months):
        balances = end_of_day_balances(statement)
        if balances is not None:
            days.extend(balances)
    if not days:
        return None
    return round(sum(days) / len(days), 2)


def average_monthly_withdrawals(statements: list[BankStatement], category: str) -> float:
    """Sum of one withdrawal category, divided by the number of months with such a row (module docstring)."""
    by_month: dict[str, float] = {}
    for statement in sorted_by_month(statements):
        amount = sum(row.amount for row in classify_rows(statement) if row.category == category)
        if amount:
            month = month_of(statement) or ""
            by_month[month] = by_month.get(month, 0.0) + amount
    if not by_month:
        return 0.0
    return round(sum(by_month.values()) / len(by_month), 2)


def average_monthly_loan_payments(statements: list[BankStatement]) -> float:
    """Observed monthly loan payments (NBC-4.1 "the payment seen in the bank statements")."""
    return average_monthly_withdrawals(statements, "loan_payment")


def average_monthly_mca_remittance(statements: list[BankStatement]) -> float:
    """Observed monthly merchant cash advance remittances (NBC-4.1)."""
    return average_monthly_withdrawals(statements, "mca_remittance")


def is_new_loan(row: ClassifiedRow) -> bool:
    """True for a loan-proceeds deposit that is a new loan or MCA funding (NEW_LOAN_PATTERN)."""
    if row.kind != "deposit" or row.category != "loan_proceeds":
        return False
    return NEW_LOAN_PATTERN.search(description_text(row.transaction.description.value)) is not None


def last_period_end(statements: list[BankStatement]) -> date | None:
    """The end of the most recent statement period (period_bounds of the latest statement month), or None."""
    ordered = sorted_by_month(statements)
    if not ordered:
        return None
    bounds = period_bounds(ordered[-1])
    return None if bounds is None else bounds[1]


def new_loans_last_90_days(statements: list[BankStatement], as_of: date) -> int:
    """Number of new loans and MCA fundings posted in the 90 days ending on as_of, both ends included (NBC-4.4)."""
    earliest = as_of - timedelta(days=NEW_LOAN_WINDOW_DAYS - 1)
    count = 0
    for statement in statements:
        for row in classify_rows(statement):
            posted = row.transaction.posted_date.value
            if is_new_loan(row) and posted is not None and earliest <= posted <= as_of:
                count += 1
    return count
