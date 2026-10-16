"""Map a LoanPacket extraction to the agent's CreditApplication JSON, with provenance (see to_application.py).

The derivation of every credit field follows the rules written down for the as-documented application (the agent
reads what a careful reader of the documents would conclude):
- application_id "APP-C-<nnn>" from the packet id "PKT-C-<nnn>"; form fields from the loan application;
- bank_statements: the account number printed on the statements; one MonthlyDeposits per statement present, oldest
  first, by the keyword rules of docintel/deposits.py; NSF events (last 6 months), negative closing balances (last
  12 months) and the average daily balance (last 3 months) with the same module; cash_intensive_approved False.
  A month whose deposit rows do not add up to the printed "Total deposits" (more than $1.00 apart: rows OCR could
  not read, or a total that is itself wrong) keeps the rows' figures but its path is listed in unknowns;
- debt: EBITDA and revenue from the P&L; existing_monthly_payment_schedule = the debt schedule's monthly payments of
  EVERY row (the agent's existing_annual_debt_service subtracts refinanced_monthly_payment itself, and only when a
  payoff letter is in the file, NBC-4.4); refinanced_monthly_payment = the payments of the rows marked "to be
  refinanced"; existing_total_debt_balance = the schedule's printed total balance (the sum of the rows when the
  total is not printed); all 0 when the schedule lists no debt (the two payment figures are then cited to the
  schedule's printed total monthly payment line); observed loan payments and MCA remittances =
  deposits.py averages over the months with a payment (NBC-4.1 "observed monthly total");
- new_loans_last_90_days: deposits.new_loans_last_90_days over the 90 days ending on the last statement period end
  (NBC-4.4 "the most recent 90 days of statements");
- collateral: one item per collateral valuation; owners: the application's owners table (O1, O2, ...) with the
  credit score and bankruptcy flag of the credit-report row with the same (canonical) name, for persons only;
- documents_provided: the checklist codes of checks_credit.documents_provided (or, when check results are passed,
  the required_documents check's values["provided"]), in the agent's CREDIT_DOCUMENTS order;
- notes: the Remarks text ("" when there is none); ground_truth None.

Nothing is filled in silently (NBC-8.1 and the repo rules). Where the agent's model has no room for None (the
DebtServiceInputs amounts, is_control_person, bankruptcy_within_7_years), the default is written AND the path is
listed in unknowns, so a reviewer and the agent see that it was not read:
- no debt schedule in the packet: the three schedule figures are 0.0 and listed; a schedule row whose monthly
  payment or balance (when no total is printed) was not read, or whose "to be refinanced" flag was not read, lists
  the figure it feeds;
- a person principal (20% or more, the control person, or a percentage that was not read) without a matching
  credit-report row (no report, or a name OCR misread): credit_score None and bankruptcy False, both listed;
- an owner whose control-person cell was not read: False, listed.
"""

from uwcopilot.agent.applications import CREDIT_DOCUMENTS
from uwcopilot.agent.policy_credit import PRINCIPAL_OWNERSHIP_PCT
from uwcopilot.docintel import deposits
from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.checks_credit import documents_provided as credit_documents_provided
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import BankStatement, Cited, ExtractedOwner, LoanApplicationForm, LoanPacket
from uwcopilot.docintel.to_application_common import (
    CENTS,
    ApplicationBuilder,
    Provenance,
    application_id,
    iso,
    money,
    provided_codes,
)

SCHEDULE_PATHS: tuple[str, ...] = (
    "debt.existing_monthly_payment_schedule",
    "debt.refinanced_monthly_payment",
    "debt.existing_total_debt_balance",
)
TOTAL_TOLERANCE_USD = 1.00  # NBC-9.1: printed totals and rows may differ by up to $1.00


def statement_sources(statements: list) -> list[Cited]:
    """The period citation of every statement (what a computed statement figure cites)."""
    return [statement.period_start for statement in statements]


def account_number(statements: list, builder: ApplicationBuilder) -> str | None:
    """The account number of the first statement (by month) that prints one."""
    for statement in deposits.sorted_by_month(statements):
        if statement.account_number.found:
            return builder.value("bank_statements.account_number", statement.account_number)
    builder.unknowns.append("bank_statements.account_number")
    return None


def rows_disagree_with_total(statement: BankStatement, month: dict) -> bool:
    """True when the statement has no deposit row, or its deposit rows and printed total are more than $1.00 apart."""
    has_rows = any(row.kind == "deposit" for row in deposits.classify_rows(statement))
    if not has_rows:
        return True
    printed = statement.total_deposits.value
    return printed is not None and abs(float(printed) - float(month["total_deposits"])) > TOTAL_TOLERANCE_USD


def bank_statements(packet: LoanPacket, builder: ApplicationBuilder) -> dict:
    """The BankStatementSummary dict (see the module docstring)."""
    ordered = deposits.sorted_by_month(packet.statements)
    months = []
    for index, statement in enumerate(ordered):
        months.append(deposits.monthly_deposits(statement))
        builder.computed(f"bank_statements.months[{index}]", months[-1], [statement.period_start])
        if rows_disagree_with_total(statement, months[-1]):
            builder.unknowns.append(f"bank_statements.months[{index}]")
    sources = statement_sources(ordered)
    known = bool(ordered)
    nsf = deposits.nsf_events(ordered) if known else None
    negative = deposits.negative_balance_months(ordered) if known else None
    adb = deposits.average_daily_balance(ordered) if known else None
    return {
        "account_number": account_number(ordered, builder),
        "months": months,
        "nsf_events_last_6_months": builder.computed("bank_statements.nsf_events_last_6_months", nsf, sources),
        "negative_balance_months_last_12": builder.computed(
            "bank_statements.negative_balance_months_last_12", negative, sources
        ),
        "average_daily_balance_last_3_months": builder.computed(
            "bank_statements.average_daily_balance_last_3_months", adb, sources
        ),
        "cash_intensive_approved": False,
    }


def add_unknown(builder: ApplicationBuilder, path: str) -> None:
    """List a path in unknowns once."""
    if path not in builder.unknowns:
        builder.unknowns.append(path)


def schedule_figures(packet: LoanPacket, builder: ApplicationBuilder) -> tuple[float, float, float]:
    """(monthly payment of every row, refinanced monthly payment, total balance) from the debt schedule.

    0s when the packet has no debt schedule, then listed in unknowns (module docstring).
    """
    schedule = packet.debt_schedule
    if schedule is None:
        for path in SCHEDULE_PATHS:
            add_unknown(builder, path)
        return 0.0, 0.0, 0.0
    every_row = 0.0
    refinanced = 0.0
    item_balance = 0.0
    payments = []
    for item in schedule.items:
        if not item.monthly_payment.found:
            add_unknown(builder, "debt.existing_monthly_payment_schedule")
        if not item.to_be_refinanced.found:
            add_unknown(builder, "debt.refinanced_monthly_payment")
        if not item.balance.found and not schedule.total_balance.found:
            add_unknown(builder, "debt.existing_total_debt_balance")
        payment = float(item.monthly_payment.value or 0.0)
        payments.append(item.monthly_payment)
        item_balance += float(item.balance.value or 0.0)
        every_row += payment
        if item.to_be_refinanced.value is True:
            refinanced += payment
    total = schedule.total_balance.value if schedule.total_balance.found else item_balance
    # A schedule with no rows ("No existing business debt.") is cited by its printed total monthly payment line.
    payment_sources = payments or [schedule.total_monthly_payment]
    builder.computed("debt.existing_monthly_payment_schedule", round(every_row, CENTS), payment_sources)
    builder.computed("debt.refinanced_monthly_payment", round(refinanced, CENTS), payment_sources)
    builder.computed("debt.existing_total_debt_balance", round(float(total), CENTS), [schedule.total_balance])
    return round(every_row, CENTS), round(refinanced, CENTS), round(float(total), CENTS)


def debt_inputs(packet: LoanPacket, builder: ApplicationBuilder) -> dict:
    """The DebtServiceInputs dict (see the module docstring)."""
    pnl = packet.profit_and_loss
    ebitda = Cited() if pnl is None else pnl.ebitda
    revenue = Cited() if pnl is None else pnl.revenue
    kept, refinanced, balance = schedule_figures(packet, builder)
    sources = statement_sources(packet.statements)
    observed = deposits.average_monthly_loan_payments(packet.statements)
    mca = deposits.average_monthly_mca_remittance(packet.statements)
    return {
        "ttm_ebitda": builder.value("debt.ttm_ebitda", ebitda, money),
        "ttm_pnl_revenue": builder.value("debt.ttm_pnl_revenue", revenue, money),
        "existing_monthly_payment_schedule": kept,
        "existing_monthly_payment_observed": builder.computed(
            "debt.existing_monthly_payment_observed", observed, sources
        ),
        "mca_monthly_remittance": builder.computed("debt.mca_monthly_remittance", mca, sources),
        "refinanced_monthly_payment": refinanced,
        "existing_total_debt_balance": balance,
    }


def is_principal(owner: ExtractedOwner) -> bool:
    """NBC-1.2 principal: 20% or more, or the control person; True too when the percentage was not read."""
    if owner.is_control_person.value is True:
        return True
    return owner.ownership_pct.value is None or float(owner.ownership_pct.value) >= PRINCIPAL_OWNERSHIP_PCT


def credit_owners(packet: LoanPacket, form: LoanApplicationForm, builder: ApplicationBuilder) -> list[dict]:
    """The application's owners with the credit report's score and bankruptcy flag for persons (module docstring)."""
    report = {}
    if packet.credit_report is not None:
        for entry in packet.credit_report.entries:
            if entry.name.found:
                report[canonical_name(entry.name.value)] = entry
    owners = []
    for index, owner in enumerate(form.owners, start=1):
        path = f"owners[{index - 1}]"
        entity_type = builder.value(f"{path}.entity_type", owner.entity_type)
        entry = report.get(canonical_name(owner.name.value)) if entity_type == "person" else None
        score = None
        bankruptcy = False
        if entry is not None:
            score = builder.value(f"{path}.credit_score", entry.credit_score)
            bankruptcy = bool(builder.value(f"{path}.bankruptcy_within_7_years", entry.bankruptcy_within_7_years))
        elif entity_type != "organization" and is_principal(owner):
            add_unknown(builder, f"{path}.credit_score")
            add_unknown(builder, f"{path}.bankruptcy_within_7_years")
        if not owner.is_control_person.found:
            add_unknown(builder, f"{path}.is_control_person")
        owners.append(
            {
                "owner_id": f"O{index}",
                "name": builder.value(f"{path}.name", owner.name),
                "entity_type": entity_type,
                "ownership_pct": builder.value(f"{path}.ownership_pct", owner.ownership_pct),
                "country": builder.value(f"{path}.country", owner.country),
                "is_control_person": owner.is_control_person.value is True,
                "credit_score": score,
                "bankruptcy_within_7_years": bankruptcy,
            }
        )
    if not owners:
        builder.unknowns.append("owners")
    return owners


def credit_documents(packet: LoanPacket, checks: list[CheckResult] | None, builder: ApplicationBuilder) -> list[str]:
    """documents_provided for a credit packet (see the module docstring)."""
    required = next((check for check in checks or [] if check.check_id == "required_documents"), None)
    if required is not None and isinstance(required.values.get("provided"), list):
        provided = set(required.values["provided"])
    else:
        provided = set(credit_documents_provided(packet))
    builder.documents(
        "documents_provided", [(item.doc_id, item.pages[0] if item.pages else None) for item in packet.documents]
    )
    return provided_codes(provided, CREDIT_DOCUMENTS)


def credit_application(
    packet: LoanPacket, checks: list[CheckResult] | None = None
) -> tuple[dict, Provenance, list[str]]:
    """The CreditApplication dict of a loan packet extraction (see the module docstring)."""
    builder = ApplicationBuilder()
    form = packet.application or LoanApplicationForm(doc_id="")
    if packet.application is None:
        builder.unknowns.append("application_form")
    collateral = []
    for index, item in enumerate(packet.collateral):
        collateral.append(
            {
                "collateral_type": builder.value(f"collateral[{index}].collateral_type", item.collateral_type),
                "value": builder.value(f"collateral[{index}].value", item.value, money),
            }
        )
    new_loans = None
    as_of = deposits.last_period_end(packet.statements)
    if as_of is not None:
        new_loans = deposits.new_loans_last_90_days(packet.statements, as_of)
    application = {
        "application_id": application_id(packet.packet_id),
        "data_label": "SYNTHETIC",
        "line": "credit",
        "institution": "northwind_business_credit",
        "application_date": builder.value("application_date", form.application_date, iso),
        "applicant": {
            "legal_name": builder.value("applicant.legal_name", form.legal_name),
            "fein": builder.value("applicant.fein", form.fein),
            "business_structure": builder.value("applicant.business_structure", form.business_structure),
            "state": builder.value("applicant.state", form.state),
            "formation_date": builder.value("applicant.formation_date", form.formation_date, iso),
            "industry": builder.value("applicant.industry", form.industry),
        },
        "product": builder.value("product", form.product),
        "requested_amount": builder.value("requested_amount", form.requested_amount, money),
        "term_months": builder.value("term_months", form.term_months),
        "note_rate_pct": builder.value("note_rate_pct", form.note_rate_pct),
        "rate_type": builder.value("rate_type", form.rate_type),
        "purpose": builder.value("purpose", form.purpose),
        "stated_annual_revenue": builder.value("stated_annual_revenue", form.stated_annual_revenue, money),
        "bank_statements": bank_statements(packet, builder),
        "debt": debt_inputs(packet, builder),
        "new_loans_last_90_days": builder.computed(
            "new_loans_last_90_days", new_loans, statement_sources(packet.statements)
        ),
        "tax_lien_unresolved": builder.value("tax_lien_unresolved", form.tax_lien_unresolved),
        "largest_customer_revenue_pct": builder.value(
            "largest_customer_revenue_pct", form.largest_customer_revenue_pct
        ),
        "collateral": collateral,
        "owners": credit_owners(packet, form, builder),
        "documents_provided": credit_documents(packet, checks, builder),
        "notes": form.remarks.value or "",
        "ground_truth": None,
    }
    return application, builder.provenance, builder.unknowns
