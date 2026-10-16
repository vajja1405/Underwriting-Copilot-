"""Renderers for the SYNTHETIC loan packet documents other than bank statements (render_statement.py).

Each function draws one document with a DocumentWriter and returns its part of the oracle extraction, with every
printed value cited (page, line, bbox). Renderers only read the case; they never draw random numbers, so the
page-counting pass and the drawing pass print the same rows. Every page carries the SYNTHETIC footer.
"""

from datetime import date

from uwcopilot.docintel.schemas import (
    BalanceSheet,
    Cited,
    CollateralValuation,
    CreditReport,
    CreditReportEntry,
    DebtItem,
    DebtSchedule,
    ExtractedOwner,
    FormationDocument,
    LoanApplicationForm,
    PhotoId,
    ProfitAndLoss,
    TaxReturnSummary,
)
from uwcopilot.docintel.synthetic.common import CREDIT_BUREAU, format_date, money, percent, yes_no
from uwcopilot.docintel.synthetic.credit_case import CollateralData, CreditCase, Party
from uwcopilot.docintel.synthetic.render_base import LEFT_MARGIN, DocumentWriter, PageWriter, Segment
from uwcopilot.docintel.synthetic.render_helpers import (
    Cell,
    cite_named,
    field_date,
    heading,
    label,
    note,
    section,
    table_header,
    table_row,
)

FORM_DATE_STYLE = "mdy"
REMARKS_WIDTH = 500.0
FORMATION_TITLES = {
    "llc": "ARTICLES OF ORGANIZATION - SUMMARY (SYNTHETIC)",
    "corporation": "ARTICLES OF INCORPORATION - SUMMARY (SYNTHETIC)",
    "partnership": "CERTIFICATE OF PARTNERSHIP - SUMMARY (SYNTHETIC)",
}


def owner_table(page: PageWriter, owners: list[Party]) -> list[ExtractedOwner]:
    """Owners table: Name | Type | Title | Ownership % | Control person | Country."""
    table_header(
        page,
        [
            ("Name", 54.0, "left"),
            ("Type", 205.0, "left"),
            ("Title", 275.0, "left"),
            ("Ownership %", 435.0, "right"),
            ("Control person", 445.0, "left"),
            ("Country", 520.0, "left"),
        ],
    )
    extracted = []
    for owner in owners:
        printed, positions = table_row(
            page,
            [
                Cell("name", owner.name, 54.0, max_width=145.0),
                Cell("entity_type", label(owner.entity_type), 205.0),
                Cell("title", owner.title, 275.0, max_width=110.0),
                Cell("ownership_pct", percent(owner.ownership_pct), 435.0, "right"),
                Cell("is_control_person", yes_no(owner.is_control_person), 445.0),
                Cell("country", owner.country, 520.0),
            ],
        )
        extracted.append(
            ExtractedOwner(
                name=cite_named(page, printed, positions, "name", owner.name),
                entity_type=cite_named(page, printed, positions, "entity_type", owner.entity_type),
                title=cite_named(page, printed, positions, "title", owner.title),
                ownership_pct=cite_named(page, printed, positions, "ownership_pct", owner.ownership_pct),
                is_control_person=cite_named(page, printed, positions, "is_control_person", owner.is_control_person),
                country=cite_named(page, printed, positions, "country", owner.country),
            )
        )
    return extracted


def render_loan_application(writer: DocumentWriter, case: CreditCase) -> LoanApplicationForm:
    """The two-page loan application form (our own layout)."""
    page = writer.new_page()
    heading(page, "NORTHWIND BUSINESS CREDIT - SMALL BUSINESS LOAN APPLICATION (SYNTHETIC)")
    note(page, "Northwind Business Credit is a fictional institution. Application form for demonstration only.")
    section(page, "Section A - Business information")
    form = LoanApplicationForm(doc_id=writer.doc_id)
    form.legal_name = page.field("Legal name of business", case.legal_name, case.legal_name)
    form.fein = page.field("Federal EIN (FEIN)", case.fein, case.fein)
    form.business_structure = page.field("Business structure", label(case.structure), case.structure)
    form.state = page.field("State of organization", case.state, case.state)
    form.formation_date = field_date(page, "Date of formation", case.formation_date, FORM_DATE_STYLE)
    form.industry = page.field("Industry", label(case.industry), case.industry)
    form.business_address = page.field("Business address", case.address, case.address)
    section(page, "Section B - Loan request")
    form.application_date = field_date(page, "Application date", case.application_date, FORM_DATE_STYLE)
    form.requested_amount = page.field("Requested amount", money(case.amount), case.amount)
    form.product = page.field("Product", label(case.product), case.product)
    form.term_months = page.field("Term (months)", str(case.term_months), case.term_months)
    form.note_rate_pct = page.field("Note rate", percent(case.rate_pct, 2), case.rate_pct)
    form.rate_type = page.field("Rate type", label(case.rate_type), case.rate_type)
    form.purpose = page.field("Purpose", label(case.purpose), case.purpose)
    section(page, "Section C - Financial information")
    form.stated_annual_revenue = page.field("Stated annual revenue", money(case.stated_revenue), case.stated_revenue)
    form.largest_customer_revenue_pct = page.field(
        "Largest customer share of revenue", percent(case.largest_customer_pct), case.largest_customer_pct
    )
    form.tax_lien_unresolved = page.field("Unresolved tax lien", yes_no(case.tax_lien), case.tax_lien)
    page = writer.new_page()
    section(page, "Section D - Owners (every owner and the control person)")
    form.owners = owner_table(page, case.owners)
    section(page, "Section E - Remarks")
    form.remarks = page.cite_lines(page.wrapped(case.remarks, REMARKS_WIDTH), case.remarks)
    section(page, "Section F - Certification")
    note(page, "I certify that the information in this application is true and complete.", size=9.0)
    page.row(
        [
            Segment(f"Signature: /s/ {case.signer}", LEFT_MARGIN),
            Segment(f"Date: {format_date(case.application_date, FORM_DATE_STYLE)}", 380.0),
        ]
    )
    return form


def money_line(page: PageWriter, text: str, value: float, bold: bool = False) -> Cited:
    """'Label ..... $1,234.56' with the amount right-aligned; returns the cited amount."""
    font = "Helvetica-Bold" if bold else "Helvetica"
    printed = page.row([Segment(text, 72.0, font=font), Segment(money(value), 470.0, font=font, align="right")])
    return page.cite(printed, 1, value)


def statement_title(
    writer: DocumentWriter, case: CreditCase, doc_type: str, title: str, when: date
) -> tuple[PageWriter, Cited, Cited]:
    """Business name and a title row ending with a date; returns (page, cited name, cited date)."""
    page = writer.new_page()
    name = case.name_on(doc_type)
    name_row = page.row([Segment(name, LEFT_MARGIN, size=12, font="Helvetica-Bold")])
    title_row = page.row([Segment(title, LEFT_MARGIN, size=10), Segment(format_date(when, "iso"), 380.0, size=10)])
    return page, page.cite(name_row, 0, name), page.cite(title_row, 1, when)


def render_profit_and_loss(writer: DocumentWriter, case: CreditCase) -> ProfitAndLoss:
    """Trailing-twelve-month P&L with a notes section (free text)."""
    pnl = case.pnl
    page, name, period = statement_title(
        writer, case, "profit_and_loss", "Profit and Loss - trailing twelve months ended", pnl.period_end
    )
    note(page, "SYNTHETIC financial statement prepared by management (unaudited)")
    page.skip(6)
    result = ProfitAndLoss(doc_id=writer.doc_id, business_name=name, period_end=period)
    result.revenue = money_line(page, "Revenue", pnl.revenue)
    result.cost_of_goods_sold = money_line(page, "Cost of goods sold", pnl.cost_of_goods_sold)
    money_line(page, "Gross profit", round(pnl.revenue - pnl.cost_of_goods_sold, 2))
    result.operating_expenses = money_line(page, "Operating expenses", pnl.operating_expenses)
    result.ebitda = money_line(page, "EBITDA", pnl.ebitda, bold=True)
    result.depreciation_amortization = money_line(page, "Depreciation and amortization", pnl.depreciation_amortization)
    result.interest_expense = money_line(page, "Interest expense", pnl.interest_expense)
    result.net_income = money_line(page, "Net income", pnl.net_income, bold=True)
    section(page, "Notes")
    for text in pnl.notes:
        page.wrapped(text, REMARKS_WIDTH)
    return result


def render_balance_sheet(writer: DocumentWriter, case: CreditCase) -> BalanceSheet:
    """Balance sheet at the P&L date."""
    sheet = case.balance_sheet
    page, name, as_of = statement_title(writer, case, "balance_sheet", "Balance Sheet as of", sheet.as_of)
    note(page, "SYNTHETIC financial statement prepared by management (unaudited)")
    result = BalanceSheet(doc_id=writer.doc_id, business_name=name, as_of=as_of)
    section(page, "Assets")
    result.cash = money_line(page, "Cash", sheet.cash)
    result.accounts_receivable = money_line(page, "Accounts receivable", sheet.accounts_receivable)
    result.inventory = money_line(page, "Inventory", sheet.inventory)
    money_line(page, "Fixed assets, net", sheet.fixed_assets)
    result.total_assets = money_line(page, "Total assets", sheet.total_assets, bold=True)
    section(page, "Liabilities")
    money_line(page, "Accounts payable", sheet.accounts_payable)
    result.long_term_debt = money_line(page, "Long-term debt", sheet.long_term_debt)
    result.total_liabilities = money_line(page, "Total liabilities", sheet.total_liabilities, bold=True)
    section(page, "Equity")
    result.equity = money_line(page, "Owners' equity", sheet.equity, bold=True)
    return result


def render_debt_schedule(writer: DocumentWriter, case: CreditCase) -> DebtSchedule:
    """Schedule of existing business debt (merchant cash advances are not listed)."""
    page, name, _ = statement_title(
        writer, case, "debt_schedule", "Schedule of Business Debt as of", case.application_date
    )
    note(page, "SYNTHETIC schedule prepared by the applicant; lender names are fictional")
    page.skip(6)
    result = DebtSchedule(doc_id=writer.doc_id, business_name=name)
    loans = case.loans()
    if not loans:
        note(page, "No existing business debt.", size=9.0)
    else:
        table_header(
            page,
            [
                ("Lender", 54.0, "left"),
                ("Type", 215.0, "left"),
                ("Balance", 385.0, "right"),
                ("Monthly payment", 465.0, "right"),
                ("Maturity", 472.0, "left"),
                ("Refinance", 525.0, "left"),
            ],
        )
    for debt in loans:
        printed, positions = table_row(
            page,
            [
                Cell("lender", debt.lender, 54.0, max_width=155.0),
                Cell("debt_type", debt.debt_type, 215.0, max_width=95.0),
                Cell("balance", money(debt.balance, symbol=False), 385.0, "right"),
                Cell("monthly_payment", money(debt.scheduled_payment, symbol=False), 465.0, "right"),
                Cell("maturity", format_date(debt.maturity, "iso"), 472.0),
                Cell("to_be_refinanced", yes_no(debt.to_be_refinanced), 525.0),
            ],
        )
        result.items.append(
            DebtItem(
                lender=cite_named(page, printed, positions, "lender", debt.lender),
                debt_type=cite_named(page, printed, positions, "debt_type", debt.debt_type),
                balance=cite_named(page, printed, positions, "balance", debt.balance),
                monthly_payment=cite_named(page, printed, positions, "monthly_payment", debt.scheduled_payment),
                maturity=cite_named(page, printed, positions, "maturity", debt.maturity),
                to_be_refinanced=cite_named(page, printed, positions, "to_be_refinanced", debt.to_be_refinanced),
            )
        )
    page.skip(6)
    balance = round(sum(debt.balance for debt in loans), 2)
    payment = round(sum(debt.scheduled_payment for debt in loans), 2)
    result.total_balance = page.field("Total balance", money(balance), balance)
    result.total_monthly_payment = page.field("Total monthly payment", money(payment), payment)
    return result


def render_photo_id(writer: DocumentWriter, case: CreditCase, owner: Party) -> PhotoId:
    """A specimen photo-ID card for one principal (clearly not a valid identity document)."""
    page = writer.new_page()
    page.skip(40)
    top = page.y + 6
    page.box(90.0, top, 430.0, 210.0)
    page.box(104.0, top - 44, 90.0, 110.0, fill_gray=0.85)
    page.row([Segment("SYNTHETIC SPECIMEN - NOT A VALID IDENTITY DOCUMENT", 104.0, size=11, font="Helvetica-Bold")])
    page.row([Segment(f"State of {case.state} - specimen identification card (fictional)", 104.0, size=8)])
    result = PhotoId(doc_id=writer.doc_id)
    page.skip(10)
    for text, attribute, value, shown in (
        ("Name", "name", owner.name, owner.name),
        ("Date of birth", "date_of_birth", owner.date_of_birth, format_date(owner.date_of_birth, "mdy")),
        ("ID number", "id_number", owner.id_number, owner.id_number),
        ("Expires", "expiry_date", owner.id_expiry, format_date(owner.id_expiry, "mdy")),
        ("Address", "address", owner.address, owner.address),
    ):
        printed = page.row([Segment(text, 210.0, font="Helvetica-Bold"), Segment(str(shown), 290.0, max_width=220.0)])
        setattr(result, attribute, page.cite(printed, 1, value))
    return result


def render_credit_report(writer: DocumentWriter, case: CreditCase) -> CreditReport:
    """The fictional bureau's summary: one row per person principal."""
    page = writer.new_page()
    heading(page, f"{CREDIT_BUREAU} (fictional)")
    note(page, "Business principal credit summary - SYNTHETIC, for demonstration only")
    field_date(page, "Report date", case.application_date, "iso")
    page.field("Requested for", case.legal_name, case.legal_name)
    page.skip(6)
    table_header(
        page, [("Name", 54.0, "left"), ("Credit score", 330.0, "right"), ("Bankruptcy in last 7 years", 360.0, "left")]
    )
    result = CreditReport(doc_id=writer.doc_id)
    for owner in case.principals():
        printed, positions = table_row(
            page,
            [
                Cell("name", owner.name, 54.0, max_width=200.0),
                Cell("credit_score", str(owner.credit_score), 330.0, "right"),
                Cell("bankruptcy", yes_no(owner.bankruptcy_within_7_years), 360.0),
            ],
        )
        result.entries.append(
            CreditReportEntry(
                name=cite_named(page, printed, positions, "name", owner.name),
                credit_score=cite_named(page, printed, positions, "credit_score", owner.credit_score),
                bankruptcy_within_7_years=cite_named(
                    page, printed, positions, "bankruptcy", owner.bankruptcy_within_7_years
                ),
            )
        )
    return result


def render_formation(writer: DocumentWriter, case: CreditCase) -> FormationDocument:
    """Articles summary: entity name, FEIN, state, formation date, members table and the control person."""
    page = writer.new_page()
    heading(page, FORMATION_TITLES[case.structure])
    note(page, f"Summary of the formation filing in {case.state}; prepared for demonstration, not a government record")
    page.skip(4)
    name = case.name_on("formation_document")
    fein = case.fein_on("formation_document")
    result = FormationDocument(doc_id=writer.doc_id)
    result.entity_name = page.field("Entity name", name, name)
    result.fein = page.field("FEIN", fein, fein)
    result.state = page.field("State", case.state, case.state)
    result.formation_date = field_date(page, "Formation date", case.formation_date, "iso")
    section(page, "Members and ownership")
    table_header(page, [("Name", 54.0, "left"), ("Ownership %", 400.0, "right")])
    for member in case.formation_members:
        printed, positions = table_row(
            page,
            [
                Cell("name", member.name, 54.0, max_width=250.0),
                Cell("pct", percent(member.ownership_pct), 400.0, "right"),
            ],
        )
        result.members.append(
            ExtractedOwner(
                name=cite_named(page, printed, positions, "name", member.name),
                ownership_pct=cite_named(page, printed, positions, "pct", member.ownership_pct),
            )
        )
    page.skip(6)
    control = next((index for index, member in enumerate(case.formation_members) if member.is_control_person), None)
    if control is not None:
        printed = page.row(
            [
                Segment("Control person", LEFT_MARGIN, font="Helvetica-Bold"),
                Segment(case.formation_members[control].name, 230.0),
            ]
        )
        result.members[control].is_control_person = page.cite(printed, 1, True)
    return result


def render_tax_return(writer: DocumentWriter, case: CreditCase) -> list[TaxReturnSummary]:
    """Business tax return summary for the two most recent tax years (not an IRS form)."""
    page = writer.new_page()
    heading(page, "Business tax return summary (SYNTHETIC; not an IRS form)")
    note(page, f"Prepared by {case.cpa_firm} (fictional) from the filed returns")
    page.skip(4)
    name = case.name_on("tax_return_summary")
    fein = case.fein_on("tax_return_summary")
    cited_name = page.field("Business name", name, name)
    cited_fein = page.field("FEIN", fein, fein)
    page.skip(6)
    table_header(page, [("Tax year", 54.0, "left"), ("Gross receipts", 330.0, "right")])
    results = []
    for year in case.tax_years:
        printed, positions = table_row(
            page, [Cell("year", str(year.year), 54.0), Cell("receipts", money(year.gross_receipts), 330.0, "right")]
        )
        results.append(
            TaxReturnSummary(
                doc_id=writer.doc_id,
                business_name=cited_name,
                fein=cited_fein,
                tax_year=cite_named(page, printed, positions, "year", year.year),
                gross_receipts=cite_named(page, printed, positions, "receipts", year.gross_receipts),
            )
        )
    return results


def render_collateral(writer: DocumentWriter, case: CreditCase, item: CollateralData) -> CollateralValuation:
    """A collateral valuation report from a fictional appraiser."""
    page = writer.new_page()
    heading(page, f"{item.appraiser} (fictional)")
    note(page, "Collateral valuation report - SYNTHETIC, for demonstration only")
    page.skip(4)
    page.field("Prepared for", "Northwind Business Credit (fictional)", None)
    page.field("Borrower", case.legal_name, case.legal_name)
    result = CollateralValuation(doc_id=writer.doc_id)
    result.collateral_type = page.field("Collateral type", label(item.collateral_type), item.collateral_type)
    page.field("Description", item.description, item.description)
    page.field("Valuation basis", "As required by the lender's collateral policy (NBC-5.1)", None)
    result.value = page.field("Value", money(item.value), item.value)
    result.valuation_date = field_date(page, "Valuation date", item.valuation_date, "iso")
    return result
