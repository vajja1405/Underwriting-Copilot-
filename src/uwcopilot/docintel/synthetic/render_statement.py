"""Render one SYNTHETIC monthly bank statement (one page) and return its cited BankStatement extraction.

Two fictional bank layouts (one bank per packet):
- layout 0: rows dated MM/DD/YYYY, columns Date | Description | Withdrawals | Deposits | Balance;
- layout 1: rows dated DD-Mon-YYYY, columns Date | Description | Deposits | Withdrawals | Balance.
The statement period is always printed as "YYYY-MM-DD to YYYY-MM-DD". Amounts in the table have no "$"; a
negative balance prints as "-1,234.56". A memo line ("Memo: ...") may follow a row; it is free text, not part of
the row's description.
"""

from uwcopilot.docintel.schemas import BankStatement, Cited, Transaction
from uwcopilot.docintel.synthetic.common import format_date, money
from uwcopilot.docintel.synthetic.credit_case import CreditCase, Statement, Txn
from uwcopilot.docintel.synthetic.render_base import LEFT_MARGIN, DocumentWriter, PageWriter, Segment
from uwcopilot.docintel.synthetic.render_helpers import Cell, cite_named, heading, note, table_header, table_row

ROW_DATE_STYLE = ("mdy", "dmy_abbrev")
DESCRIPTION_X = 122.0
DESCRIPTION_WIDTH = 220.0
FIRST_AMOUNT_X = 420.0
SECOND_AMOUNT_X = 490.0
BALANCE_X = 558.0
MEMO_SIZE = 7.5


def amount_columns(layout: int) -> tuple[str, str]:
    """(name of the first amount column, name of the second) for a layout."""
    return ("withdrawal", "deposit") if layout == 0 else ("deposit", "withdrawal")


def summary(page: PageWriter, statement: Statement) -> dict[str, Cited]:
    """Opening balance, totals and closing balance, as printed."""
    return {
        "opening_balance": page.field("Opening balance", money(statement.shown_opening), statement.shown_opening),
        "total_deposits": page.field(
            "Total deposits", money(statement.shown_total_deposits), statement.shown_total_deposits
        ),
        "total_withdrawals": page.field(
            "Total withdrawals", money(statement.shown_total_withdrawals), statement.shown_total_withdrawals
        ),
        "closing_balance": page.field("Closing balance", money(statement.closing), statement.closing),
    }


def transaction_row(page: PageWriter, row: Txn, layout: int) -> Transaction:
    """Print one transaction row (and its memo lines) and return it cited."""
    first, second = amount_columns(layout)
    amount_name = "deposit" if row.kind == "D" else "withdrawal"
    amount_text = money(row.amount, symbol=False)
    printed, positions = table_row(
        page,
        [
            Cell("date", format_date(row.posted, ROW_DATE_STYLE[layout]), LEFT_MARGIN),
            Cell("description", row.description, DESCRIPTION_X, max_width=DESCRIPTION_WIDTH),
            Cell(first, amount_text if amount_name == first else "", FIRST_AMOUNT_X, "right"),
            Cell(second, amount_text if amount_name == second else "", SECOND_AMOUNT_X, "right"),
            Cell("balance", money(row.balance, symbol=False), BALANCE_X, "right"),
        ],
    )
    transaction = Transaction(
        posted_date=cite_named(page, printed, positions, "date", row.posted),
        description=cite_named(page, printed, positions, "description", row.description),
        balance=cite_named(page, printed, positions, "balance", row.balance),
    )
    if row.kind == "D":
        transaction.deposit = cite_named(page, printed, positions, "deposit", row.amount)
    else:
        transaction.withdrawal = cite_named(page, printed, positions, "withdrawal", row.amount)
    if row.memo:
        page.wrapped(f"Memo: {row.memo}", DESCRIPTION_WIDTH + 40, x=DESCRIPTION_X + 8, size=MEMO_SIZE)
    return transaction


def render_bank_statement(writer: DocumentWriter, case: CreditCase, statement: Statement) -> BankStatement:
    """One statement page: letterhead, account details, summary, transactions."""
    page = writer.new_page()
    layout = case.bank_layout
    bank_row = page.row([Segment(case.bank, LEFT_MARGIN, size=12, font="Helvetica-Bold")])
    bank = page.cite(bank_row, 0, case.bank)
    note(page, "Fictional institution - SYNTHETIC business checking statement for demonstration only")
    heading(page, "Business Checking Statement", size=10)
    holder = page.field("Account holder", case.name_on("bank_statement"), case.name_on("bank_statement"))
    account = page.field("Account number", case.account_number, case.account_number)
    period_text = f"{statement.period_start.isoformat()} to {statement.period_end.isoformat()}"
    period_row = page.row(
        [Segment("Statement period", LEFT_MARGIN, font="Helvetica-Bold"), Segment(period_text, 230.0)]
    )
    totals = summary(page, statement)
    page.skip(6)
    first, second = amount_columns(layout)
    table_header(
        page,
        [
            ("Date", LEFT_MARGIN, "left"),
            ("Description", DESCRIPTION_X, "left"),
            (first.capitalize() + "s", FIRST_AMOUNT_X, "right"),
            (second.capitalize() + "s", SECOND_AMOUNT_X, "right"),
            ("Balance", BALANCE_X, "right"),
        ],
    )
    transactions = [transaction_row(page, row, layout) for row in statement.rows]
    page.skip(4)
    note(page, f"Number of transactions: {len(statement.rows)}")
    return BankStatement(
        doc_id=writer.doc_id,
        bank_name=bank,
        account_holder=holder,
        account_number=account,
        period_start=page.cite(period_row, 1, statement.period_start),
        period_end=page.cite(period_row, 1, statement.period_end),
        transactions=transactions,
        **totals,
    )
