"""Northwind revenue and debt-payment checks over a LoanPacket extraction.

- revenue_vs_deposits (NBC-3.2, 3.3, 3.4): each statement becomes an agent MonthlyDeposits (deposits.py keyword
  rules), and verified annual revenue is computed with uwcopilot.agent.credit_calculations.verify_revenue, exactly
  as the reference policy does (12 most recent consecutive months, non-revenue removed, cash capped at 25% of
  non-cash). The revenue gap uses revenue_gap_pct. A gap of 10.0% or less passes (NBC-3.4 "benign"); more than
  10.0% fails (medium up to 25.0%, high above). Fewer than 12 consecutive months: not evaluated (NBC-3.2).
  The issue's data carries everything the explanation rules in contradictions.py need: gross deposits, identified
  non-revenue and unidentified deposits over the same 12 months, the P&L revenue and period, and the tax return
  receipts. Its supporting evidence lists the largest non-revenue and unidentified deposit rows, the P&L revenue
  and the tax returns (data["supporting_categories"] gives each supporting item's category, by position).
- debt_payment_consistency (NBC-3.1, 4.1): for each debt schedule item, the loan and MCA withdrawals whose
  description contains every significant word of the lender's name are the observed payments. The observed
  monthly payment is the average of the monthly totals over the months in which a payment appears. A difference
  from the scheduled payment of more than $1.00 is an issue. NBC-4.1 already uses the higher of the two, so the
  check's severity is low. Payments to lenders that are not on the schedule are listed in values only.
"""

from pydantic import ValidationError

from uwcopilot.agent.applications import BankStatementSummary, MonthlyDeposits
from uwcopilot.agent.credit_calculations import month_index, revenue_gap_pct, verify_revenue
from uwcopilot.agent.policy_credit import BENIGN_GAP_PCT, EXPLAINABLE_GAP_PCT
from uwcopilot.docintel.check_models import (
    CREDIT_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    document_evidence,
    make_result,
)
from uwcopilot.docintel.deposits import IDENTIFIED_NON_REVENUE, ClassifiedRow, classify_rows, month_of, monthly_deposits
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import BankStatement, DebtItem, LoanPacket, casefold_key, transaction_key

# verify_revenue reads only the months (and the cash-intensive flag) of a BankStatementSummary; the other required
# fields are filled with neutral placeholders.
PLACEHOLDER_ACCOUNT = "0000000000"
MONTHS_REQUIRED = 12
SUPPORTING_ROWS_PER_KIND = 5
PAYMENT_TOLERANCE_USD = 1.00
# MonthlyDeposits fields of the deposits NBC-3.3 excludes because their description identifies them.
IDENTIFIED_FIELDS = (
    "transfers_between_accounts",
    "refunds_and_reversals",
    "loan_proceeds",
    "owner_contributions",
    "one_time_receipts",
)
# Words dropped from a lender's name before it is looked for in a payment description.
LENDER_NOISE_WORDS = {"llc", "inc", "corp", "co", "ltd", "na", "the", "of"}


def statements_with_months(packet: LoanPacket) -> list[tuple[BankStatement, dict]]:
    """(statement, MonthlyDeposits dict) for every statement with a known month, oldest first."""
    pairs = []
    for statement in packet.statements:
        months = monthly_deposits(statement)
        if months is not None:
            pairs.append((statement, months))
    pairs.sort(key=lambda pair: month_index(str(pair[1]["month"])))
    return pairs


def verify(months: list[dict]) -> tuple[float | None, float]:
    """(verified annual revenue or None, excluded cash) using the agent's verify_revenue.

    A month the agent model rejects (for example a negative total read from a damaged page) means revenue cannot
    be verified, so the result is None rather than an exception.
    """
    try:
        summary = BankStatementSummary(
            account_number=PLACEHOLDER_ACCOUNT,
            months=[MonthlyDeposits(**month) for month in months],
            nsf_events_last_6_months=0,
            negative_balance_months_last_12=0,
            average_daily_balance_last_3_months=0.0,
        )
    except ValidationError:
        return None, 0.0
    result = verify_revenue(summary)
    return result.verified_annual_revenue, result.excluded_cash


def deposit_evidence(row: ClassifiedRow, doc_id: str) -> Evidence | None:
    """Evidence for one deposit row's amount."""
    return cite(row.transaction.deposit, "statement.transactions.deposit", transaction_key(row.transaction), doc_id)


def supporting_rows(statements: list[BankStatement]) -> tuple[list[Evidence], list[str]]:
    """The largest identified non-revenue and unidentified deposit rows, and their categories."""
    identified: list[tuple[float, Evidence, str]] = []
    unidentified: list[tuple[float, Evidence, str]] = []
    for statement in statements:
        for row in classify_rows(statement):
            item = deposit_evidence(row, statement.doc_id) if row.kind == "deposit" else None
            if item is None:
                continue
            if row.category in IDENTIFIED_NON_REVENUE:
                identified.append((row.amount, item, row.category))
            elif row.category == "unidentified":
                unidentified.append((row.amount, item, row.category))
    chosen = []
    for rows in (identified, unidentified):
        rows.sort(key=lambda entry: -entry[0])
        chosen.extend(rows[:SUPPORTING_ROWS_PER_KIND])
    return [entry[1] for entry in chosen], [entry[2] for entry in chosen]


def revenue_sources(packet: LoanPacket) -> tuple[dict, list[Evidence], list[str]]:
    """P&L revenue, its period end and the tax return receipts, with evidence and categories."""
    data: dict = {"pnl_revenue": None, "pnl_period_end": None, "tax_receipts": {}}
    evidence: list[Evidence] = []
    categories: list[str] = []
    pnl = packet.profit_and_loss
    if pnl is not None and pnl.revenue.found:
        data["pnl_revenue"] = float(pnl.revenue.value)
        data["pnl_period_end"] = None if pnl.period_end.value is None else pnl.period_end.value.isoformat()
        evidence.append(cite(pnl.revenue, "profit_and_loss.revenue", None, pnl.doc_id))
        categories.append("profit_and_loss")
    for item in packet.tax_returns:
        if item.tax_year.found and item.gross_receipts.found:
            data["tax_receipts"][str(item.tax_year.value)] = float(item.gross_receipts.value)
            evidence.append(
                cite(item.gross_receipts, "tax_return.gross_receipts", str(item.tax_year.value), item.doc_id)
            )
            categories.append("tax_return")
    return data, evidence, categories


def check_revenue_vs_deposits(packet: LoanPacket) -> CheckResult:
    """NBC-3.4 revenue gap between the stated revenue and revenue verified from deposits."""
    refs = CREDIT_REFS["revenue_vs_deposits"]
    application = packet.application
    stated_cited = None if application is None else application.stated_annual_revenue
    pairs = statements_with_months(packet)
    recent = pairs[-MONTHS_REQUIRED:]
    totals_evidence = []
    for statement, months in recent:
        item = cite(statement.total_deposits, "statement.total_deposits", str(months["month"]), statement.doc_id)
        totals_evidence.extend([item] if item else [])
    if stated_cited is None or not stated_cited.found:
        message = "The application's stated annual revenue was not found."
        return make_result("revenue_vs_deposits", refs, "not_evaluated", message, {}, totals_evidence)
    stated = float(stated_cited.value)
    stated_evidence = cite(stated_cited, "application.stated_annual_revenue", None, application.doc_id)
    verified, excluded_cash = verify([months for _, months in pairs])
    gross = 0.0
    identified = 0.0
    unidentified = 0.0
    for _, months in recent:
        gross += float(months["total_deposits"])
        unidentified += float(months["unidentified_deposits"])
        for field in IDENTIFIED_FIELDS:
            identified += float(months[field])
    gross, identified, unidentified = round(gross, 2), round(identified, 2), round(unidentified, 2)
    values: dict = {
        "stated_annual_revenue": stated,
        "verified_annual_revenue": verified,
        "months_used": len(recent),
        "gross_deposits": gross,
        "identified_non_revenue": identified,
        "unidentified_deposits": unidentified,
        "excluded_cash": excluded_cash,
    }
    if verified is None:
        message = f"Revenue cannot be verified: {len(pairs)} statement month(s), 12 consecutive needed (NBC-3.2)."
        return make_result(
            "revenue_vs_deposits", refs, "not_evaluated", message, values, [stated_evidence, *totals_evidence]
        )
    gap = revenue_gap_pct(stated, verified)
    values["gap_pct"] = gap
    if gap is None:
        message = "Stated revenue is zero, so the revenue gap is not defined."
        return make_result("revenue_vs_deposits", refs, "not_evaluated", message, values, [stated_evidence])
    if gap <= BENIGN_GAP_PCT:
        values["band"] = "benign"
        message = f"Revenue gap {gap:.1f}% (stated ${stated:,.2f}, verified ${verified:,.2f}) is 10.0% or less."
        return make_result("revenue_vs_deposits", refs, "pass", message, values, [stated_evidence, *totals_evidence])
    values["band"] = "explainable" if gap <= EXPLAINABLE_GAP_PCT else "unsupported"
    rows_evidence, row_categories = supporting_rows([statement for statement, _ in recent])
    source_data, source_evidence, source_categories = revenue_sources(packet)
    last_end = recent[-1][0].period_end.value
    data = {
        **values,
        **source_data,
        "gap_usd": round(stated - verified, 2),
        "statements_end": None if last_end is None else last_end.isoformat(),
        "supporting_categories": row_categories + source_categories,
    }
    issue = CheckIssue(
        issue="revenue_gap",
        entity="business",
        fields=["application.stated_annual_revenue", "statement.total_deposits"],
        side_a=[stated_evidence],
        side_b=totals_evidence,
        supporting=rows_evidence + source_evidence,
        data=data,
        message=f"Stated revenue ${stated:,.2f} is {gap:.1f}% above revenue verified from deposits "
        f"(${verified:,.2f}; NBC-3.4 {values['band']} band).",
    )
    severity = "medium" if values["band"] == "explainable" else "high"
    return make_result("revenue_vs_deposits", refs, "fail", issue.message, values, [], [issue], severity)


def lender_words(lender: str | None) -> set[str]:
    """The significant words of a lender's name (all words when every word is a noise word)."""
    words = set(canonical_name(lender).split())
    return (words - LENDER_NOISE_WORDS) or words


def payment_rows(packet: LoanPacket) -> list[tuple[BankStatement, ClassifiedRow]]:
    """Every loan payment and MCA remittance withdrawal, with its statement."""
    rows = []
    for statement in packet.statements:
        for row in classify_rows(statement):
            if row.kind == "withdrawal" and row.category in ("loan_payment", "mca_remittance"):
                rows.append((statement, row))
    return rows


def item_issue(
    index: int, item: DebtItem, doc_id: str, matched: list[tuple[BankStatement, ClassifiedRow]]
) -> CheckIssue | None:
    """Scheduled vs observed monthly payment for one debt item (None when they agree or cannot be compared)."""
    if not matched or not item.monthly_payment.found:
        return None
    by_month: dict[str, float] = {}
    for statement, row in matched:
        month = month_of(statement) or ""
        by_month[month] = by_month.get(month, 0.0) + row.amount
    observed = round(sum(by_month.values()) / len(by_month), 2)
    scheduled = float(item.monthly_payment.value)
    if abs(observed - scheduled) <= PAYMENT_TOLERANCE_USD:
        return None
    key = casefold_key(item.lender)
    side_b = []
    for statement, row in matched:
        evidence = cite(
            row.transaction.withdrawal,
            "statement.transactions.withdrawal",
            transaction_key(row.transaction),
            statement.doc_id,
        )
        side_b.extend([evidence] if evidence else [])
    return CheckIssue(
        issue="payment_differs",
        entity="business",
        fields=["debt_schedule.items.monthly_payment", "statement.transactions.withdrawal"],
        side_a=[cite(item.monthly_payment, "debt_schedule.items.monthly_payment", key, doc_id)],
        side_b=side_b,
        data={
            "debt_item": index,
            "scheduled_payment": scheduled,
            "observed_payment": observed,
            "months_observed": len(by_month),
            "difference": round(observed - scheduled, 2),
        },
        message=f"Debt item {index} is scheduled at ${scheduled:,.2f} a month but the statements show "
        f"${observed:,.2f} a month on average ({len(by_month)} month(s)).",
    )


def check_debt_payments(packet: LoanPacket) -> CheckResult:
    """NBC-4.1: debt schedule payments vs payments seen in the statements (see the module docstring)."""
    refs = CREDIT_REFS["debt_payment_consistency"]
    schedule = packet.debt_schedule
    if schedule is None:
        return make_result("debt_payment_consistency", refs, "not_evaluated", "No debt schedule was found.", {}, [])
    rows = payment_rows(packet)
    used: set[int] = set()
    issues = []
    evidence = [document_evidence(schedule, "document", "debt_schedule")]
    not_observed = []
    for index, item in enumerate(schedule.items, start=1):
        words = lender_words(item.lender.value)
        matched = []
        for position, (statement, row) in enumerate(rows):
            if words and words <= set(canonical_name(row.transaction.description.value).split()):
                matched.append((statement, row))
                used.add(position)
        key = casefold_key(item.lender)
        payment = cite(item.monthly_payment, "debt_schedule.items.monthly_payment", key, schedule.doc_id)
        if payment is not None:
            evidence.append(payment)
        if not matched:
            not_observed.append(index)
        issue = item_issue(index, item, schedule.doc_id, matched)
        if issue is not None:
            issues.append(issue)
    values = {
        "debt_items": len(schedule.items),
        "items_not_observed": not_observed,
        "unscheduled_payment_rows": len(rows) - len(used),
    }
    if issues:
        message = "Debt payments differ (NBC-4.1 uses the higher): " + " ".join(issue.message for issue in issues)
        return make_result("debt_payment_consistency", refs, "fail", message, values, evidence, issues)
    if not schedule.items:
        message = "The debt schedule lists no existing business debt."
        return make_result("debt_payment_consistency", refs, "pass", message, values, evidence)
    observed = len(schedule.items) - len(not_observed)
    message = (
        f"Scheduled payments match the statements for the {observed} of {len(schedule.items)} debt item(s) with a "
        "matching payment in the statements."
    )
    return make_result("debt_payment_consistency", refs, "pass", message, values, evidence)
