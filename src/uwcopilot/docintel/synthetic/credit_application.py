"""application_as_documented for a SYNTHETIC loan case: what a perfect reader of its documents would conclude.

The derivation of every field is written down in credit_case.py. This module computes it from the generator's own
model (documented row categories, printed values, the documents actually in the packet), independently of the
extraction pipeline; a test cross-checks the account-conduct figures with docintel/deposits.py on the oracle
extraction.
"""

from datetime import date, timedelta

from uwcopilot.agent.credit_calculations import month_index
from uwcopilot.docintel.synthetic.credit_case import PERSON, CreditCase, Statement

NON_REVENUE_FIELDS: dict[str, str] = {
    "transfer": "transfers_between_accounts",
    "refund": "refunds_and_reversals",
    "loan_proceeds": "loan_proceeds",
    "owner_contribution": "owner_contributions",
    "one_time": "one_time_receipts",
    "unidentified": "unidentified_deposits",
}
NSF_WINDOW = 6
NEGATIVE_WINDOW = 12
ADB_WINDOW = 3
NEW_LOAN_DAYS = 90


def month_text(day: date) -> str:
    """'YYYY-MM'."""
    return f"{day.year:04d}-{day.month:02d}"


def monthly_deposits(statement: Statement, use_true_categories: bool = False) -> dict:
    """The MonthlyDeposits dict of one statement, by documented (or true) row categories."""
    totals = {name: 0.0 for name in NON_REVENUE_FIELDS.values()}
    cash = 0.0
    total = 0.0
    for row in statement.rows:
        if row.kind != "D":
            continue
        category = row.true_category if use_true_categories else row.category
        total += row.amount
        if category in NON_REVENUE_FIELDS:
            totals[NON_REVENUE_FIELDS[category]] += row.amount
        elif category == "cash_revenue":
            cash += row.amount
    result: dict = {"month": month_text(statement.month), "total_deposits": round(total, 2)}
    for name, amount in totals.items():
        result[name] = round(amount, 2)
    result["cash_deposits"] = round(cash, 2)
    return result


def in_window(statements: list[Statement], months: int) -> list[Statement]:
    """Statements whose month is within `months` of the latest statement month present."""
    if not statements:
        return []
    latest = max(month_index(month_text(item.month)) for item in statements)
    return [item for item in statements if month_index(month_text(item.month)) > latest - months]


def nsf_events(statements: list[Statement]) -> int:
    """NSF / overdraft fee rows in the last 6 statement months."""
    return sum(1 for item in in_window(statements, NSF_WINDOW) for row in item.rows if row.category == "nsf_fee")


def negative_months(statements: list[Statement]) -> int:
    """Statements in the last 12 months whose printed closing balance is below zero."""
    return sum(1 for item in in_window(statements, NEGATIVE_WINDOW) if item.closing < 0)


def daily_balances(statement: Statement) -> list[float]:
    """End-of-day balances for every day of the period: printed opening before the first row, then row balances."""
    balances = []
    current = statement.shown_opening
    rows = sorted(statement.rows, key=lambda row: (row.posted, row.order))
    position = 0
    day = statement.period_start
    while day <= statement.period_end:
        while position < len(rows) and rows[position].posted <= day:
            current = rows[position].balance
            position += 1
        balances.append(current)
        day += timedelta(days=1)
    return balances


def average_daily_balance(statements: list[Statement]) -> float:
    """Mean end-of-day balance over the days of the last 3 statement months."""
    days = [value for item in in_window(statements, ADB_WINDOW) for value in daily_balances(item)]
    return round(sum(days) / len(days), 2) if days else 0.0


def average_monthly(statements: list[Statement], category: str) -> float:
    """Sum of one withdrawal category, divided by the number of statement months with at least one such row
    (NBC-4.1 "observed monthly total"; 0.0 when no month has one).

    The sum is accumulated statement by statement, in the same order as docintel/deposits.py, so the rounded
    result is identical to the cent.
    """
    total = 0.0
    months = 0
    for item in statements:
        amount = sum(row.amount for row in item.rows if row.kind == "W" and row.category == category)
        if amount:
            total += amount
            months += 1
    return round(total / months, 2) if months else 0.0


def last_period_end(statements: list[Statement]) -> date:
    """The end of the most recent statement period present (the as-of date of the NBC-4.4 stacking window)."""
    return max(item.period_end for item in statements)


def new_loans(statements: list[Statement], as_of: date) -> int:
    """Loan-proceeds deposits posted in the 90 days ending on as_of (as_of - 89 days through as_of)."""
    earliest = as_of - timedelta(days=NEW_LOAN_DAYS - 1)
    return sum(
        1
        for item in statements
        for row in item.rows
        if row.kind == "D" and row.category == "loan_proceeds" and earliest <= row.posted <= as_of
    )


def schedule_totals(case: CreditCase) -> tuple[float, float, float]:
    """(monthly payment of every row, refinanced monthly payment, total balance) printed on the debt schedule.

    The first figure includes the rows marked to be refinanced: agent.credit_calculations.existing_annual_debt_service
    subtracts refinanced_monthly_payment itself, and only when a payoff letter is in the file (NBC-4.4).
    """
    if "debt_schedule" in case.missing_documents:
        return 0.0, 0.0, 0.0
    loans = case.loans()
    every_row = round(sum(debt.scheduled_payment for debt in loans), 2)
    refinanced = round(sum(debt.scheduled_payment for debt in loans if debt.to_be_refinanced), 2)
    balance = round(sum(debt.balance for debt in loans), 2)
    return every_row, refinanced, balance


def documents_provided(case: CreditCase) -> list[str]:
    """Checklist codes the packet's documents satisfy (see credit_case.py)."""
    provided = ["signed_application"]
    present = case.present_statements()
    if len(present) == 12:
        provided.append("bank_statements_12_months")
    if "tax_return_summary" not in case.missing_documents and len(case.tax_years) >= 2:
        provided.append("business_tax_returns_2_years")
    provided.append("ytd_pnl_and_balance_sheet")
    if "debt_schedule" not in case.missing_documents:
        provided.append("debt_schedule")
    if case.missing_photo_id_owner is None:
        provided.append("principal_photo_ids")
    if "formation_document" not in case.missing_documents:
        provided.append("formation_documents")
        if case.formation_members:
            provided.append("beneficial_ownership_certification")
    if case.collateral:
        provided.append("collateral_documents")
    return provided


def owners(case: CreditCase) -> list[dict]:
    """The application's owners table with credit report values for persons who have a report row."""
    report = {owner.name.casefold(): owner for owner in case.principals()}
    rows = []
    for index, owner in enumerate(case.owners, start=1):
        reported = report.get(owner.name.casefold()) if owner.entity_type == PERSON else None
        rows.append(
            {
                "owner_id": f"O{index}",
                "name": owner.name,
                "entity_type": owner.entity_type,
                "ownership_pct": owner.ownership_pct,
                "country": owner.country,
                "is_control_person": owner.is_control_person,
                "credit_score": None if reported is None else reported.credit_score,
                "bankruptcy_within_7_years": False if reported is None else reported.bankruptcy_within_7_years,
            }
        )
    return rows


def statement_summary(case: CreditCase) -> dict:
    """bank_statements: account number, months present, NSF events, negative months and ADB."""
    present = case.present_statements()
    return {
        "account_number": case.account_number,
        "months": [monthly_deposits(item) for item in present],
        "nsf_events_last_6_months": nsf_events(present),
        "negative_balance_months_last_12": negative_months(present),
        "average_daily_balance_last_3_months": average_daily_balance(present),
        "cash_intensive_approved": False,
    }


def debt_inputs(case: CreditCase) -> dict:
    """debt: P&L figures, schedule totals and observed payments."""
    present = case.present_statements()
    kept, refinanced, balance = schedule_totals(case)
    return {
        "ttm_ebitda": round(case.pnl.ebitda, 2),
        "ttm_pnl_revenue": round(case.pnl.revenue, 2),
        "existing_monthly_payment_schedule": kept,
        "existing_monthly_payment_observed": average_monthly(present, "loan_payment"),
        "mca_monthly_remittance": average_monthly(present, "mca_remittance"),
        "refinanced_monthly_payment": refinanced,
        "existing_total_debt_balance": balance,
    }


def application_as_documented(case: CreditCase) -> dict:
    """The CreditApplication JSON a perfect reader of this packet would build (validated by the caller)."""
    present = case.present_statements()
    return {
        "application_id": case.application_id,
        "data_label": "SYNTHETIC",
        "line": "credit",
        "institution": "northwind_business_credit",
        "application_date": case.application_date.isoformat(),
        "applicant": {
            "legal_name": case.legal_name,
            "fein": case.fein,
            "business_structure": case.structure,
            "state": case.state,
            "formation_date": case.formation_date.isoformat(),
            "industry": case.industry,
        },
        "product": case.product,
        "requested_amount": round(case.amount, 2),
        "term_months": case.term_months,
        "note_rate_pct": case.rate_pct,
        "rate_type": case.rate_type,
        "purpose": case.purpose,
        "stated_annual_revenue": round(case.stated_revenue, 2),
        "bank_statements": statement_summary(case),
        "debt": debt_inputs(case),
        "new_loans_last_90_days": new_loans(present, last_period_end(present)),
        "tax_lien_unresolved": case.tax_lien,
        "largest_customer_revenue_pct": case.largest_customer_pct,
        "collateral": [
            {"collateral_type": item.collateral_type, "value": round(item.value, 2)} for item in case.collateral
        ],
        "owners": owners(case),
        "documents_provided": documents_provided(case),
        "notes": case.remarks,
        "ground_truth": None,
    }
