"""Rules extraction of one SYNTHETIC bank statement (credit packets) into a cited BankStatement.

Header fields are label/value lines (rules_common.find_value): Account holder, Account number, Statement period
("YYYY-MM-DD to YYYY-MM-DD"; both dates cite the same words), Opening balance, Total deposits, Total withdrawals,
Closing balance. The bank name is the letterhead: the first line of the statement.

Transactions: the table header is the first line holding at least three of Date / Description / Withdrawals /
Deposits / Balance (or two adjacent lines holding them together: OCR may split the header row). Every later line
(every line, when no header can be read) that starts with a date within its first two words is a transaction row;
lines that do not (memo lines and their continuations, "Number of transactions") are skipped. In a row:
- the table amounts (printed with two decimals) are found by pattern; the LAST one is the running balance and the
  one before it is the transaction amount; a row with a single amount is a balance only when that amount sits
  nearer the Balance column than the amount columns;
- the description is the words between the date and the first amount (trailing OCR noise trimmed);
- deposit and withdrawal amounts are printed without a sign, so a stray minus read by OCR is dropped;
- a row date whose month OCR misread ("44/03/2025") is repaired from the statement period: every row of a monthly
  statement is dated inside its period, so when the period covers one calendar month, the row's own day and year
  are read and the period's month is used (repair_row_date). The day is never invented: a row whose day cannot be
  read is skipped;
- deposit or withdrawal: the running balance decides first, because it is exact to the cent: previous balance +
  amount = balance means a deposit, previous balance - amount = balance a withdrawal (previous balance = the row
  above, or the opening balance for the first row). When the balances are not both known or the arithmetic does
  not decide, the amount goes to whichever of the Withdrawals / Deposits columns has its right edge (amounts are
  right-aligned) nearest the amount's right edge. (An OCR word box widened by speckle can sit nearer the wrong
  column; the balance is stronger evidence than a box edge.) If neither decides, the amount is left out rather
  than guessed;
- a lost minus sign: OCR often drops the thin minus of a negative balance ("-42,645.01" read as "42,645.01"). When
  the previous balance and the row amount give exactly the read balance with the opposite sign, the sign is
  restored (fix_balance_sign).
- the header Opening / Closing balance can lose its minus the same way (OCR may read "-$1,295.52" as
  '"$1,295.52', and the stray mark is stripped). Its sign is restored only when the arithmetic proves it
  (repair_header_sign): the closing balance with the opposite sign equals the last row's balance, or opening +
  total deposits - total withdrawals, and the value as read equals neither; the opening balance with the opposite
  sign equals the balance before the first row (that row's balance - deposit + withdrawal) and the value as read
  does not. When a stray mark sits right before the "$" (parsing.unclear_sign) and the arithmetic cannot decide,
  the value is left out (the sign is unknown) rather than guessed.
No other value is ever changed by arithmetic.
"""

from datetime import date

from uwcopilot.docintel.classify import Segment
from uwcopilot.docintel.layout import Word
from uwcopilot.docintel.parsing import fix_ocr_digits, parse_amount, parse_date, parse_text, strip_edges, unclear_sign
from uwcopilot.docintel.rules_common import (
    LineRef,
    amount_words,
    cite_words,
    document_lines,
    find_value,
    header_lines,
    title_box,
    trim_noise,
    value_after_label,
    words_text,
)
from uwcopilot.docintel.schemas import BankStatement, Cited, Transaction

TABLE_TITLES = ("Date", "Description", "Withdrawals", "Deposits", "Balance")
BALANCE_TOLERANCE = 0.01
ACCOUNT_DIGITS = (10, 12)


def parse_account_number(text: str) -> str | None:
    """The account number when the text is one run of 10-12 digits (after the OCR digit fix), else None."""
    fixed = fix_ocr_digits(strip_edges(text))
    low, high = ACCOUNT_DIGITS
    if fixed.isdigit() and low <= len(fixed) <= high:
        return fixed
    return None


def period_values(refs: list[LineRef], doc_id: str) -> tuple[Cited, Cited]:
    """(period_start, period_end) from the "Statement period A to B" line."""
    for ref in refs:
        words = value_after_label(ref.line, "Statement period", ref.fuzzy)
        if not words:
            continue
        dates = [(word, parse_date(word.text)) for word in words]
        found = [(word, value) for word, value in dates if value is not None]
        if len(found) >= 2:
            return cite_words(ref, words, found[0][1], doc_id), cite_words(ref, words, found[-1][1], doc_id)
    return Cited(), Cited()


def letterhead(refs: list[LineRef], doc_id: str) -> Cited:
    """The bank name: the first line of the statement."""
    if not refs:
        return Cited()
    words = trim_noise(refs[0].words)
    return cite_words(refs[0], words, parse_text(words_text(words)), doc_id)


def amount_columns(headers: list[LineRef]) -> dict[str, float]:
    """Right edge of the Withdrawals, Deposits and Balance titles on the header line(s) (the titles found)."""
    columns: dict[str, float] = {}
    for header in headers:
        for name, title in (("withdrawal", "Withdrawals"), ("deposit", "Deposits"), ("balance", "Balance")):
            box = title_box(header, title)
            if box is not None and name not in columns:
                columns[name] = box[1]
    return columns


def repair_row_date(text: str, period: tuple[date, date] | None) -> date | None:
    """A row date with an unreadable month, rebuilt from its day and year and the period's month, or None.

    Only for a period inside one calendar month and a date printed as MM/DD/YYYY or DD-Mon-YYYY whose year is the
    period's year; the result must fall inside the period.
    """
    if period is None or period[0].month != period[1].month or period[0].year != period[1].year:
        return None
    cleaned = strip_edges(text).rstrip(".,")
    if "/" in cleaned:
        parts = cleaned.split("/")
        day_text, year_text = (parts[1], parts[2]) if len(parts) == 3 else ("", "")
    else:
        parts = cleaned.split("-")
        day_text, year_text = (parts[0], parts[2]) if len(parts) == 3 else ("", "")
    day_text, year_text = fix_ocr_digits(day_text), fix_ocr_digits(year_text)
    if not day_text.isdigit() or year_text != str(period[0].year):
        return None
    try:
        repaired = date(period[0].year, period[0].month, int(day_text))
    except ValueError:
        return None
    return repaired if period[0] <= repaired <= period[1] else None


def row_date(ref: LineRef, period: tuple[date, date] | None) -> tuple[Word | None, date | None, int]:
    """(date word, date, index) of a transaction row (its first two words), or (None, None, -1)."""
    for index, word in enumerate(ref.words[:2]):
        value = parse_date(word.text)
        if value is not None:
            return word, value, index
    for index, word in enumerate(ref.words[:1]):
        value = repair_row_date(word.text, period)
        if value is not None:
            return word, value, index
    return None, None, -1


def side_by_column(amount: Word, columns: dict[str, float]) -> str | None:
    """ "deposit" or "withdrawal" by the nearest amount column, or None when either column is unknown."""
    if "deposit" not in columns or "withdrawal" not in columns:
        return None
    to_deposit = abs(columns["deposit"] - amount.x1)
    to_withdrawal = abs(columns["withdrawal"] - amount.x1)
    return "deposit" if to_deposit < to_withdrawal else "withdrawal"


def side_by_balance(amount: float, balance: float | None, previous: float | None) -> str | None:
    """ "deposit" or "withdrawal" from the running balance arithmetic, or None when it does not decide.

    The read balance is tried as printed first, then with the opposite sign (OCR may have lost a minus sign).
    """
    if balance is None or previous is None:
        return None
    for read in (balance, -balance):
        if abs(previous + amount - read) <= BALANCE_TOLERANCE:
            return "deposit"
        if abs(previous - amount - read) <= BALANCE_TOLERANCE:
            return "withdrawal"
    return None


def fix_balance_sign(side: str | None, amount: float, balance: float | None, previous: float | None) -> float | None:
    """The balance with its sign restored when the running balance proves OCR lost it, else None (no change)."""
    if side is None or balance is None or previous is None or balance == 0:
        return None
    expected = previous + amount if side == "deposit" else previous - amount
    if abs(expected - balance) <= BALANCE_TOLERANCE:
        return None
    if abs(expected + balance) <= BALANCE_TOLERANCE:
        return -balance
    return None


def split_amounts(amounts: list[Word], columns: dict[str, float]) -> tuple[Word | None, Word | None]:
    """(transaction amount word, balance word) of a row (see the module docstring)."""
    if len(amounts) >= 2:
        return amounts[-2], amounts[-1]
    if len(amounts) == 1:
        only = amounts[0]
        balance_x = columns.get("balance")
        others = [columns[name] for name in ("deposit", "withdrawal") if name in columns]
        if balance_x is not None and others and abs(balance_x - only.x1) < min(abs(x - only.x1) for x in others):
            return None, only
        return only, None
    return None, None


def parse_transaction(
    ref: LineRef, columns: dict[str, float], previous: float | None, period: tuple[date, date] | None, doc_id: str
) -> Transaction | None:
    """One transaction row, or None when the line is not a row (see the module docstring)."""
    date_word, posted, date_index = row_date(ref, period)
    if date_word is None:
        return None
    rest = ref.words[date_index + 1 :]
    amounts = amount_words(rest)
    amount_word, balance_word = split_amounts(amounts, columns)
    first_amount = amounts[0] if amounts else None
    description = trim_noise([word for word in rest if first_amount is None or word.x1 < first_amount.x0])
    transaction = Transaction(posted_date=cite_words(ref, [date_word], posted, doc_id))
    if description:
        transaction.description = cite_words(ref, description, parse_text(words_text(description)), doc_id)
    balance = None if balance_word is None else parse_amount(balance_word.text)
    if balance_word is not None and balance is not None:
        transaction.balance = cite_words(ref, [balance_word], balance, doc_id)
    if amount_word is None:
        return transaction
    amount = parse_amount(amount_word.text)
    if amount is None:
        return transaction
    amount = abs(amount)
    side = side_by_balance(amount, balance, previous) or side_by_column(amount_word, columns)
    if side == "deposit":
        transaction.deposit = cite_words(ref, [amount_word], amount, doc_id)
    elif side == "withdrawal":
        transaction.withdrawal = cite_words(ref, [amount_word], amount, doc_id)
    fixed = fix_balance_sign(side, amount, balance, previous)
    if fixed is not None and balance_word is not None:
        transaction.balance = cite_words(ref, [balance_word], fixed, doc_id)
    return transaction


def transactions(
    refs: list[LineRef], opening: float | None, period: tuple[date, date] | None, doc_id: str
) -> list[Transaction]:
    """Every transaction row after the table header (see the module docstring)."""
    header = header_lines(refs, TABLE_TITLES, 3)
    columns = amount_columns([refs[index] for index in header])
    first_row = header[-1] + 1 if header else 0
    rows = []
    previous = opening
    for ref in refs[first_row:]:
        row = parse_transaction(ref, columns, previous, period, doc_id)
        if row is None:
            continue
        rows.append(row)
        previous = row.balance.value if row.balance.found else None
    return rows


def balance_before_first_row(rows: list[Transaction]) -> float | None:
    """The balance before the first row (its balance - deposit + withdrawal), or None when it cannot be worked out."""
    if not rows or rows[0].balance.value is None:
        return None
    first = rows[0]
    if first.deposit.value is None and first.withdrawal.value is None:
        return None
    return float(first.balance.value) - float(first.deposit.value or 0.0) + float(first.withdrawal.value or 0.0)


def repair_header_sign(cited: Cited, expected: list[float | None]) -> Cited:
    """A header balance with its sign restored, kept, or left out (see the module docstring).

    expected are the balances the arithmetic gives (None when a figure is unknown).
    """
    if not cited.found or cited.value == 0:
        return cited
    value = float(cited.value)
    known = [item for item in expected if item is not None]
    as_read = any(abs(item - value) <= BALANCE_TOLERANCE for item in known)
    flipped = any(abs(item + value) <= BALANCE_TOLERANCE for item in known)
    if flipped and not as_read:
        return cited.model_copy(update={"value": -value})
    if not as_read and unclear_sign(cited.source_text or ""):
        return Cited()
    return cited


def closing_expectations(rows: list[Transaction], opening: Cited, deposits: Cited, withdrawals: Cited) -> list:
    """What the closing balance should be: the last row's balance, and opening + deposits - withdrawals."""
    expected: list[float | None] = []
    last = next((row.balance.value for row in reversed(rows) if row.balance.value is not None), None)
    expected.append(None if last is None else float(last))
    if opening.found and deposits.found and withdrawals.found:
        expected.append(float(opening.value) + float(deposits.value) - float(withdrawals.value))
    return expected


def extract_statement(segment: Segment) -> BankStatement:
    """One bank statement document."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    start, end = period_values(refs, doc_id)
    period = (start.value, end.value) if start.found and end.found else None
    opening = find_value(refs, ("Opening balance",), parse_amount, doc_id)
    rows = transactions(refs, opening.value, period, doc_id)
    repaired = repair_header_sign(opening, [balance_before_first_row(rows)])
    if repaired.value != opening.value:
        opening = repaired
        rows = transactions(refs, opening.value, period, doc_id)
    deposits = find_value(refs, ("Total deposits",), parse_amount, doc_id)
    withdrawals = find_value(refs, ("Total withdrawals",), parse_amount, doc_id)
    closing = find_value(refs, ("Closing balance",), parse_amount, doc_id)
    closing = repair_header_sign(closing, closing_expectations(rows, opening, deposits, withdrawals))
    return BankStatement(
        doc_id=doc_id,
        bank_name=letterhead(refs, doc_id),
        account_holder=find_value(refs, ("Account holder",), parse_text, doc_id),
        account_number=find_value(refs, ("Account number",), parse_account_number, doc_id),
        period_start=start,
        period_end=end,
        opening_balance=opening,
        total_deposits=deposits,
        total_withdrawals=withdrawals,
        closing_balance=closing,
        transactions=rows,
    )
