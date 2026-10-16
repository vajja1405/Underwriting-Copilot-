"""Northwind (credit) bank statement checks over a LoanPacket extraction. Thresholds come from agent/policy_credit.py.

- statement_months_complete (NBC-3.1, 3.2, 3.5): the 12 most recent consecutive months must be present and the
  most recent statement must end no more than 60 days before the application date. The window ends at the most
  recent statement month, or at the month before the application month when that window holds at least as many
  of the statements (so a missing latest month is named as missing, as NBC-3.1 asks for the MOST RECENT months).
- balance_continuity (NBC-9.1): opening + deposits - withdrawals = closing within $1.00 on every statement; the
  printed totals equal the sum of the rows within $1.00; each month opens at the previous month's closing balance
  within $1.00; one account number on every statement.
- account_conduct (NBC-3.5, informational): NSF events, negative months and average daily balance computed from
  the transactions (docintel/deposits.py) against the NBC-3.5 triggers. The reference policy raises the referral
  from the application fields; this check shows the computed values with their citations. It never creates a
  contradiction.
"""

from datetime import date

from uwcopilot.agent.credit_calculations import month_index, proposed_annual_debt_service
from uwcopilot.agent.policy_credit import MAX_NEGATIVE_MONTHS, MAX_NSF_EVENTS, MIN_BALANCE_SHARE_OF_DEBT_SERVICE
from uwcopilot.docintel.check_models import (
    CREDIT_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    document_evidence,
    found_only,
    make_result,
)
from uwcopilot.docintel.checks_identity import group_by_key, majority_key
from uwcopilot.docintel.deposits import (
    ADB_WINDOW_MONTHS,
    NEGATIVE_WINDOW_MONTHS,
    NSF_WINDOW_MONTHS,
    average_daily_balance,
    classify_rows,
    month_of,
    negative_balance_months,
    nsf_events,
    period_bounds,
    sorted_by_month,
    statements_in_window,
)
from uwcopilot.docintel.normalize import canonical_name, digits_only
from uwcopilot.docintel.schemas import BankStatement, LoanPacket, transaction_key

MONTHS_REQUIRED = 12  # NBC-3.1 / NBC-3.2
MAX_STATEMENT_AGE_DAYS = 60  # NBC-3.1: the most recent statement ends no more than 60 days before the application
BALANCE_TOLERANCE_USD = 1.00  # NBC-9.1: opening + credits - debits vs closing, more than $1.00 is an indicator


def month_label(index: int) -> str:
    """Inverse of credit_calculations.month_index: a running month number back to "YYYY-MM"."""
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def statement_label(statement: BankStatement) -> str:
    """A statement label for messages, for example D03 (2026-03)."""
    return f"{statement.doc_id} ({month_of(statement) or 'month unknown'})"


def consecutive_months(packet: LoanPacket) -> bool:
    """True when the 12 most recent statement months present are consecutive."""
    indices = sorted({month_index(month_of(statement) or "") for statement in sorted_by_month(packet.statements)})
    recent = indices[-MONTHS_REQUIRED:]
    return len(recent) == MONTHS_REQUIRED and recent[-1] - recent[0] == MONTHS_REQUIRED - 1


def window_end(present: list[int], application_date: date | None) -> int:
    """The last month of the required 12-month window (see the module docstring)."""
    latest = present[-1]
    if application_date is None:
        return latest
    before_application = month_index(application_date.strftime("%Y-%m")) - 1
    if before_application <= latest:
        return latest
    held_latest = sum(1 for index in present if latest - MONTHS_REQUIRED < index <= latest)
    held_before = sum(1 for index in present if before_application - MONTHS_REQUIRED < index <= before_application)
    if held_before >= held_latest:
        return before_application
    return latest


def check_statement_months(packet: LoanPacket) -> CheckResult:
    """NBC-3.1 / 3.2 / 3.5: 12 consecutive recent months, the most recent within 60 days of the application."""
    refs = CREDIT_REFS["statement_months_complete"]
    application = packet.application
    application_date = None if application is None else application.application_date.value
    evidence: list[Evidence] = []
    if application is not None:
        date_evidence = cite(application.application_date, "application.application_date", None, application.doc_id)
        evidence.extend(found_only([date_evidence]))
    ordered = sorted_by_month(packet.statements)
    for statement in ordered:
        item = cite(statement.period_start, "statement.period_start", month_of(statement), statement.doc_id)
        evidence.append(item or document_evidence(statement, "document", "bank_statement", month_of(statement)))
    if not ordered:
        message = "No bank statements with a known month were found; 12 months are required."
        return make_result("statement_months_complete", refs, "fail", message, {"months_present": []}, evidence)
    present = sorted({month_index(month_of(statement) or "") for statement in ordered})
    end = window_end(present, application_date)
    window = list(range(end - MONTHS_REQUIRED + 1, end + 1))
    missing = [month_label(index) for index in window if index not in present]
    values: dict = {
        "months_present": [month_label(index) for index in present],
        "window": [month_label(window[0]), month_label(window[-1])],
        "missing_months": missing,
        "max_days_before_application": MAX_STATEMENT_AGE_DAYS,
    }
    problems = []
    if missing:
        problems.append(f"missing statement month(s) {', '.join(missing)}")
    bounds = period_bounds(ordered[-1])
    if bounds is not None and application_date is not None:
        age = (application_date - bounds[1]).days
        values["most_recent_period_end"] = bounds[1].isoformat()
        values["days_before_application"] = age
        if age > MAX_STATEMENT_AGE_DAYS:
            problems.append(f"the most recent statement ends {age} days before the application (more than 60)")
    if problems:
        message = "Statements are incomplete: " + "; ".join(problems) + "."
        return make_result("statement_months_complete", refs, "fail", message, values, evidence)
    message = f"12 consecutive statement months from {values['window'][0]} to {values['window'][1]} are present."
    return make_result("statement_months_complete", refs, "pass", message, values, evidence)


def arithmetic_issue(statement: BankStatement) -> CheckIssue | None:
    """Opening + deposits - withdrawals vs closing on one statement (None when it adds up or a value is missing)."""
    parts = (statement.opening_balance, statement.total_deposits, statement.total_withdrawals)
    if any(not part.found for part in parts) or not statement.closing_balance.found:
        return None
    expected = round(parts[0].value + parts[1].value - parts[2].value, 2)
    printed = statement.closing_balance.value
    difference = round(printed - expected, 2)
    if abs(difference) <= BALANCE_TOLERANCE_USD:
        return None
    month = month_of(statement)
    names = ("statement.opening_balance", "statement.total_deposits", "statement.total_withdrawals")
    side_a = [cite(part, name, month, statement.doc_id) for part, name in zip(parts, names, strict=True)]
    side_b = [cite(statement.closing_balance, "statement.closing_balance", month, statement.doc_id)]
    return CheckIssue(
        issue="arithmetic",
        entity="business",
        fields=[*names, "statement.closing_balance"],
        side_a=found_only(side_a),
        side_b=found_only(side_b),
        data={"month": month, "expected_closing": expected, "printed_closing": printed, "difference": difference},
        message=f"On {statement_label(statement)} opening + deposits - withdrawals = ${expected:,.2f} but the "
        f"closing balance is ${printed:,.2f}.",
    )


def row_total_issue(statement: BankStatement, kind: str) -> CheckIssue | None:
    """Printed total deposits (or withdrawals) vs the sum of the rows (None when they agree or cannot be compared)."""
    total = statement.total_deposits if kind == "deposit" else statement.total_withdrawals
    rows = [row for row in classify_rows(statement) if row.kind == kind]
    if not total.found or not rows:
        return None
    row_sum = round(sum(row.amount for row in rows), 2)
    difference = round(total.value - row_sum, 2)
    if abs(difference) <= BALANCE_TOLERANCE_USD:
        return None
    month = month_of(statement)
    total_field = "statement.total_deposits" if kind == "deposit" else "statement.total_withdrawals"
    row_field = f"statement.transactions.{kind}"
    side_b = []
    for row in rows:
        amount = row.transaction.deposit if kind == "deposit" else row.transaction.withdrawal
        side_b.append(cite(amount, row_field, transaction_key(row.transaction), statement.doc_id))
    return CheckIssue(
        issue=f"{kind}_total",
        entity="business",
        fields=[total_field, row_field],
        side_a=found_only([cite(total, total_field, month, statement.doc_id)]),
        side_b=found_only(side_b),
        data={"month": month, "printed_total": total.value, "row_sum": row_sum, "difference": difference},
        message=f"On {statement_label(statement)} the printed total {kind}s are ${total.value:,.2f} but the rows add "
        f"up to ${row_sum:,.2f}.",
    )


def continuity_issue(previous: BankStatement, current: BankStatement) -> CheckIssue | None:
    """A month that does not open at the previous month's closing balance (consecutive months only)."""
    if month_index(month_of(current) or "") - month_index(month_of(previous) or "") != 1:
        return None
    if not previous.closing_balance.found or not current.opening_balance.found:
        return None
    closing = previous.closing_balance.value
    opening = current.opening_balance.value
    difference = round(opening - closing, 2)
    if abs(difference) <= BALANCE_TOLERANCE_USD:
        return None
    return CheckIssue(
        issue="discontinuity",
        entity="business",
        fields=["statement.closing_balance", "statement.opening_balance"],
        side_a=found_only(
            [cite(previous.closing_balance, "statement.closing_balance", month_of(previous), previous.doc_id)]
        ),
        side_b=found_only(
            [cite(current.opening_balance, "statement.opening_balance", month_of(current), current.doc_id)]
        ),
        data={"month": month_of(current), "previous_closing": closing, "opening": opening, "difference": difference},
        message=f"{statement_label(current)} opens at ${opening:,.2f} but {statement_label(previous)} closed at "
        f"${closing:,.2f}.",
    )


def account_number_issues(statements: list[BankStatement]) -> list[CheckIssue]:
    """Statements that show a different account number from the majority (NBC-9.1)."""
    sources = []
    for statement in statements:
        sources.append(
            cite(statement.account_number, "statement.account_number", month_of(statement), statement.doc_id)
        )
    groups = group_by_key(found_only(sources), digits_only)
    if len(groups) < 2:
        return []
    main = majority_key(groups)
    issues = []
    for key, items in groups.items():
        if key == main:
            continue
        issues.append(
            CheckIssue(
                issue="account_number_change",
                entity="business",
                fields=["statement.account_number"],
                side_a=groups[main],
                side_b=items,
                data={"majority_account": main, "odd_account": key},
                message=f"{len(items)} statement(s) show a different account number from the other statements.",
            )
        )
    return issues


def statement_issues(ordered: list[BankStatement]) -> list[CheckIssue]:
    """Every balance and account-number issue across the statements, oldest month first."""
    issues: list[CheckIssue] = []
    for statement in ordered:
        candidates = (
            arithmetic_issue(statement),
            row_total_issue(statement, "deposit"),
            row_total_issue(statement, "withdrawal"),
        )
        issues.extend(issue for issue in candidates if issue is not None)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        issue = continuity_issue(previous, current)
        if issue is not None:
            issues.append(issue)
    issues.extend(account_number_issues(ordered))
    return issues


def check_balance_continuity(packet: LoanPacket) -> CheckResult:
    """NBC-9.1 statement arithmetic, row totals, month-to-month continuity and account number."""
    refs = CREDIT_REFS["balance_continuity"]
    ordered = sorted_by_month(packet.statements)
    if not ordered:
        return make_result("balance_continuity", refs, "not_evaluated", "No bank statements were found.", {}, [])
    issues = statement_issues(ordered)
    evidence = []
    for statement in ordered:
        for name in ("opening_balance", "total_deposits", "total_withdrawals", "closing_balance"):
            evidence.append(cite(getattr(statement, name), f"statement.{name}", month_of(statement), statement.doc_id))
    evidence = found_only(evidence)
    values = {
        "statements": len(ordered),
        "issue_codes": [issue.issue for issue in issues],
        "tolerance_usd": BALANCE_TOLERANCE_USD,
    }
    if issues:
        message = "Statement integrity indicators: " + " ".join(issue.message for issue in issues)
        return make_result("balance_continuity", refs, "fail", message, values, evidence, issues)
    if not evidence:
        message = "No statement balances were found to check."
        return make_result("balance_continuity", refs, "not_evaluated", message, values, evidence)
    message = f"Balances add up and continue across all {len(ordered)} statements."
    return make_result("balance_continuity", refs, "pass", message, values, evidence)


def conduct_evidence(statements: list[BankStatement]) -> list[Evidence]:
    """NSF fee rows (last 6 months) and the closing balances of the last 12 months."""
    evidence = []
    for statement in statements_in_window(statements, NSF_WINDOW_MONTHS):
        for row in classify_rows(statement):
            if row.category == "nsf_fee":
                key = transaction_key(row.transaction)
                evidence.append(
                    cite(row.transaction.withdrawal, "statement.transactions.withdrawal", key, statement.doc_id)
                )
    for statement in statements_in_window(statements, NEGATIVE_WINDOW_MONTHS):
        evidence.append(
            cite(statement.closing_balance, "statement.closing_balance", month_of(statement), statement.doc_id)
        )
    return found_only(evidence)


def product_code(text: str | None) -> str | None:
    """The agent's product code from the application's product text (None when it is not recognized)."""
    key = canonical_name(text)
    if "line" in key and "credit" in key:
        return "line_of_credit"
    if "equipment" in key:
        return "equipment_loan"
    if "term" in key:
        return "term_loan"
    return None


def proposed_service(packet: LoanPacket) -> tuple[float | None, list[Evidence]]:
    """NBC-4.1 proposed annual debt service from the application (None when an input is missing)."""
    application = packet.application
    if application is None:
        return None, []
    product = product_code(application.product.value)
    inputs = (application.requested_amount, application.note_rate_pct, application.term_months)
    names = ("requested_amount", "note_rate_pct", "term_months")
    evidence = [
        cite(cited, f"application.{name}", None, application.doc_id) for cited, name in zip(inputs, names, strict=True)
    ]
    evidence.append(cite(application.product, "application.product", None, application.doc_id))
    if product is None or any(not cited.found for cited in inputs):
        return None, found_only(evidence)
    amount, rate, term = (cited.value for cited in inputs)
    return proposed_annual_debt_service(product, float(amount), float(rate), int(term)), found_only(evidence)


def check_account_conduct(packet: LoanPacket) -> CheckResult:
    """NBC-3.5 conduct figures from the transactions (informational; see the module docstring)."""
    refs = CREDIT_REFS["account_conduct"]
    if not packet.statements:
        return make_result("account_conduct", refs, "not_evaluated", "No bank statements were found.", {}, [])
    nsf = nsf_events(packet.statements)
    negative = negative_balance_months(packet.statements)
    adb = average_daily_balance(packet.statements)
    service, service_evidence = proposed_service(packet)
    floor = None if service is None else round(MIN_BALANCE_SHARE_OF_DEBT_SERVICE * service, 2)
    values = {
        "nsf_events_last_6_months": nsf,
        "negative_balance_months_last_12": negative,
        "average_daily_balance_last_3_months": adb,
        "adb_window_months": ADB_WINDOW_MONTHS,
        "average_balance_floor": floor,
    }
    triggers = []
    if nsf > MAX_NSF_EVENTS:
        triggers.append(f"{nsf} NSF or overdraft events in the last 6 months (more than {MAX_NSF_EVENTS})")
    if negative > MAX_NEGATIVE_MONTHS:
        triggers.append(f"{negative} of the last 12 months closed negative (3 or more)")
    if adb is not None and floor is not None and adb < floor:
        triggers.append(
            f"average daily balance ${adb:,.2f} is below 5% of proposed annual debt service (${floor:,.2f})"
        )
    evidence = conduct_evidence(packet.statements) + service_evidence
    summary = f"{nsf} NSF events, {negative} negative months"
    if adb is not None:
        summary += f", average daily balance ${adb:,.2f}"
    if triggers:
        message = f"Account conduct ({summary}) meets NBC-3.5 referral triggers: " + "; ".join(triggers) + "."
        return make_result("account_conduct", refs, "fail", message, values, evidence)
    message = f"Account conduct ({summary}) meets no NBC-3.5 trigger."
    return make_result("account_conduct", refs, "pass", message, values, evidence)
