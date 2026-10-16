"""Rules extraction of the SYNTHETIC loan packet documents (bank statements are in rules_statements.py).

Each function reads one document (a classify.Segment) and returns its schema model with every value cited. Labels
are the printed labels of the Northwind layouts: APPLICATION_FIELDS lists the application form's labels, and the
other documents' labels are written where they are read. Values are parsed with docintel/parsing.py (amounts with
parse_amount: two decimals); codes are printed as words and mapped back with code_from_label ("Line of credit" ->
"line_of_credit").

Tables:
- owners (loan application page 2): rules_tables.owner_table, rows after the owners header up to "Section E";
- debt schedule: rows between the header and "Total balance". From the right: Refinance (Yes/No), Maturity (a
  date), then the two amounts (balance, monthly payment). The words left of the amounts are the lender and the
  type: split at the x position of the "Type" title when the header shows it, otherwise at a known debt type
  ending the text (KNOWN_DEBT_TYPES), otherwise the last two words are the type;
- credit report: rows after the header; the last word is bankruptcy Yes/No, the word before is the score, the rest
  is the name;
- formation members: rows after the "Name  Ownership %" header up to "Control person"; the last word is the share;
  the "Control person <name>" line marks the member with that name;
- tax return summary: rows after "Tax year  Gross receipts": a four-digit year and an amount. Each row is one
  TaxReturnSummary that shares the document's business name and FEIN.
"""

from uwcopilot.docintel.classify import Segment, normalize_for_match
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.parsing import (
    code_from_label,
    normalize_fein,
    parse_amount,
    parse_bool,
    parse_date,
    parse_identifier,
    parse_int,
    parse_percent,
    parse_text,
)
from uwcopilot.docintel.rules_common import (
    LineRef,
    amount_words,
    cite_lines,
    cite_words,
    document_lines,
    find_line,
    find_value,
    header_index,
    lines_between,
    lines_until,
    title_box,
    trim_noise,
    words_text,
)
from uwcopilot.docintel.rules_tables import owner_table
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

APPLICATION_FIELDS: tuple[tuple[str, tuple[str, ...], object], ...] = (
    ("legal_name", ("Legal name of business",), parse_text),
    ("fein", ("Federal EIN (FEIN)", "FEIN"), normalize_fein),
    ("business_structure", ("Business structure",), code_from_label),
    ("state", ("State of organization",), parse_text),
    ("formation_date", ("Date of formation",), parse_date),
    ("industry", ("Industry",), code_from_label),
    ("business_address", ("Business address",), parse_text),
    ("application_date", ("Application date",), parse_date),
    ("requested_amount", ("Requested amount",), parse_amount),
    ("product", ("Product",), code_from_label),
    ("term_months", ("Term (months)",), parse_int),
    ("note_rate_pct", ("Note rate",), parse_percent),
    ("rate_type", ("Rate type",), code_from_label),
    ("purpose", ("Purpose",), code_from_label),
    ("stated_annual_revenue", ("Stated annual revenue",), parse_amount),
    ("largest_customer_revenue_pct", ("Largest customer share of revenue",), parse_percent),
    ("tax_lien_unresolved", ("Unresolved tax lien",), parse_bool),
)
KNOWN_DEBT_TYPES = (
    "term loan",
    "equipment loan",
    "vehicle loan",
    "commercial mortgage",
    "line of credit",
    "merchant cash advance",
    "sba loan",
)
DEBT_TITLES = ("Lender", "Type", "Balance", "Monthly payment", "Maturity", "Refinance")
TAX_YEAR_RANGE = (1990, 2100)


def first_line_text(refs: list[LineRef], doc_id: str) -> Cited:
    """The first line of a document as text (the business name heading of the financial statements)."""
    if not refs:
        return Cited()
    words = trim_noise(refs[0].words)
    return cite_words(refs[0], words, parse_text(words_text(words)), doc_id)


def last_date_on(refs: list[LineRef], phrase: str, doc_id: str) -> Cited:
    """The last date printed on the first line holding the phrase (a title such as "Balance Sheet as of <date>")."""
    index = find_line(refs, phrase)
    if index is None:
        return Cited()
    ref = refs[index]
    for word in reversed(ref.words):
        value = parse_date(word.text)
        if value is not None:
            return cite_words(ref, [word], value, doc_id)
    return Cited()


def extract_application(segment: Segment) -> LoanApplicationForm:
    """The two-page loan application form."""
    refs = document_lines(segment)
    form = LoanApplicationForm(doc_id=segment.doc_id)
    for name, labels, parser in APPLICATION_FIELDS:
        setattr(form, name, find_value(refs, labels, parser, segment.doc_id))
    form.owners = owner_table(refs, "Section D - Owners", ("Section E", "Remarks"), segment.doc_id)
    remarks = lines_between(refs, "Section E - Remarks", ("Section F", "Certification"))
    if remarks:
        form.remarks = cite_lines(remarks, " ".join(ref.text for ref in remarks), segment.doc_id)
    return form


def extract_profit_and_loss(segment: Segment) -> ProfitAndLoss:
    """The trailing-twelve-month profit and loss statement."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    return ProfitAndLoss(
        doc_id=doc_id,
        business_name=first_line_text(refs, doc_id),
        period_end=last_date_on(refs, "Profit and Loss", doc_id),
        revenue=find_value(refs, ("Revenue",), parse_amount, doc_id),
        cost_of_goods_sold=find_value(refs, ("Cost of goods sold",), parse_amount, doc_id),
        operating_expenses=find_value(refs, ("Operating expenses",), parse_amount, doc_id),
        ebitda=find_value(refs, ("EBITDA",), parse_amount, doc_id),
        depreciation_amortization=find_value(refs, ("Depreciation and amortization",), parse_amount, doc_id),
        interest_expense=find_value(refs, ("Interest expense",), parse_amount, doc_id),
        net_income=find_value(refs, ("Net income",), parse_amount, doc_id),
    )


def extract_balance_sheet(segment: Segment) -> BalanceSheet:
    """The balance sheet."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    return BalanceSheet(
        doc_id=doc_id,
        business_name=first_line_text(refs, doc_id),
        as_of=last_date_on(refs, "Balance Sheet as of", doc_id),
        cash=find_value(refs, ("Cash",), parse_amount, doc_id),
        accounts_receivable=find_value(refs, ("Accounts receivable",), parse_amount, doc_id),
        inventory=find_value(refs, ("Inventory",), parse_amount, doc_id),
        total_assets=find_value(refs, ("Total assets",), parse_amount, doc_id),
        total_liabilities=find_value(refs, ("Total liabilities",), parse_amount, doc_id),
        long_term_debt=find_value(refs, ("Long-term debt",), parse_amount, doc_id),
        equity=find_value(refs, ("Owners' equity", "Equity"), parse_amount, doc_id),
    )


def split_lender_and_type(ref: LineRef, words: list, type_x: float | None) -> tuple[list, list]:
    """(lender words, type words) of a debt row (see the module docstring)."""
    if type_x is not None:
        lender = [word for word in words if word.x0 < type_x - 2.0]
        debt_type = [word for word in words if word.x0 >= type_x - 2.0]
        if lender and debt_type:
            return lender, debt_type
    text = normalize_for_match(words_text(words))
    for known in KNOWN_DEBT_TYPES:
        size = len(known.split())
        if text.endswith(known) and len(words) > size:
            return words[:-size], words[-size:]
    return words[:-2], words[-2:]


def parse_debt_row(ref: LineRef, type_x: float | None, doc_id: str) -> DebtItem | None:
    """One debt schedule row, or None when the line is not one (see the module docstring)."""
    words = ref.words
    amounts = amount_words(words)
    if len(amounts) < 2:
        return None
    item = DebtItem(
        balance=cite_words(ref, [amounts[0]], parse_amount(amounts[0].text), doc_id),
        monthly_payment=cite_words(ref, [amounts[1]], parse_amount(amounts[1].text), doc_id),
    )
    after = [word for word in words if word.x0 > amounts[1].x1]
    for word in after:
        if parse_date(word.text) is not None and not item.maturity.found:
            item.maturity = cite_words(ref, [word], parse_date(word.text), doc_id)
        elif parse_bool(word.text) is not None:
            item.to_be_refinanced = cite_words(ref, [word], parse_bool(word.text), doc_id)
    left = [word for word in words if word.x1 < amounts[0].x0]
    if len(left) >= 2:
        lender, debt_type = split_lender_and_type(ref, left, type_x)
        item.lender = cite_words(ref, lender, parse_text(words_text(lender)), doc_id)
        item.debt_type = cite_words(ref, debt_type, parse_text(words_text(debt_type)), doc_id)
    return item


def extract_debt_schedule(segment: Segment) -> DebtSchedule:
    """The schedule of existing business debt."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    schedule = DebtSchedule(
        doc_id=doc_id,
        business_name=first_line_text(refs, doc_id),
        total_balance=find_value(refs, ("Total balance",), parse_amount, doc_id),
        total_monthly_payment=find_value(refs, ("Total monthly payment",), parse_amount, doc_id),
    )
    start = header_index(refs, DEBT_TITLES, 3)
    if start is None:
        return schedule
    box = title_box(refs[start], "Type")
    type_x = None if box is None else box[0]
    for ref in lines_until(refs[start + 1 :], ("Total balance", "Total monthly payment")):
        item = parse_debt_row(ref, type_x, doc_id)
        if item is not None:
            schedule.items.append(item)
    return schedule


def extract_photo_id(segment: Segment) -> PhotoId:
    """One SYNTHETIC specimen photo-ID card."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    return PhotoId(
        doc_id=doc_id,
        name=find_value(refs, ("Name",), parse_text, doc_id),
        date_of_birth=find_value(refs, ("Date of birth",), parse_date, doc_id),
        id_number=find_value(refs, ("ID number",), parse_identifier, doc_id),
        expiry_date=find_value(refs, ("Expires",), parse_date, doc_id),
        address=find_value(refs, ("Address",), parse_text, doc_id),
    )


def parse_credit_entry(ref: LineRef, doc_id: str) -> CreditReportEntry | None:
    """One credit report row (name, score, bankruptcy Yes/No), or None."""
    words = ref.words
    if len(words) < 3 or parse_bool(words[-1].text) is None:
        return None
    score = parse_int(words[-2].text)
    if score is None:
        return None
    name = words[:-2]
    return CreditReportEntry(
        name=cite_words(ref, name, parse_text(words_text(name)), doc_id),
        credit_score=cite_words(ref, [words[-2]], score, doc_id),
        bankruptcy_within_7_years=cite_words(ref, [words[-1]], parse_bool(words[-1].text), doc_id),
    )


def extract_credit_report(segment: Segment) -> CreditReport:
    """The credit bureau summary."""
    refs = document_lines(segment)
    report = CreditReport(doc_id=segment.doc_id)
    start = header_index(refs, ("Name", "Credit score", "Bankruptcy"), 2)
    if start is None:
        return report
    for ref in refs[start + 1 :]:
        entry = parse_credit_entry(ref, segment.doc_id)
        if entry is not None:
            report.entries.append(entry)
    return report


def parse_member_row(ref: LineRef, doc_id: str) -> ExtractedOwner | None:
    """One formation-document member row (name, ownership %), or None."""
    words = ref.words
    if len(words) < 2 or "%" not in words[-1].text:
        return None
    pct = parse_percent(words[-1].text)
    if pct is None:
        return None
    name = words[:-1]
    return ExtractedOwner(
        name=cite_words(ref, name, parse_text(words_text(name)), doc_id),
        ownership_pct=cite_words(ref, [words[-1]], pct, doc_id),
    )


def extract_formation(segment: Segment) -> FormationDocument:
    """The formation document summary with its members and control person."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    formation = FormationDocument(
        doc_id=doc_id,
        entity_name=find_value(refs, ("Entity name",), parse_text, doc_id),
        fein=find_value(refs, ("FEIN",), normalize_fein, doc_id),
        state=find_value(refs, ("State",), parse_text, doc_id),
        formation_date=find_value(refs, ("Formation date",), parse_date, doc_id),
    )
    for ref in lines_between(refs, "Members and ownership", ("Control person",)):
        member = parse_member_row(ref, doc_id)
        if member is not None:
            formation.members.append(member)
    control = find_value(refs, ("Control person",), parse_text, doc_id)
    if control.found:
        for member in formation.members:
            if canonical_name(member.name.value) == canonical_name(control.value):
                member.is_control_person = control.model_copy(update={"value": True})
                break
    return formation


def extract_tax_returns(segment: Segment) -> list[TaxReturnSummary]:
    """One TaxReturnSummary per tax-year row of the business tax return summary."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    name = find_value(refs, ("Business name",), parse_text, doc_id)
    fein = find_value(refs, ("FEIN",), normalize_fein, doc_id)
    start = header_index(refs, ("Tax year", "Gross receipts"), 2)
    results = []
    for ref in [] if start is None else refs[start + 1 :]:
        year = parse_int(ref.words[0].text) if ref.words else None
        amounts = amount_words(ref.words)
        if year is None or not TAX_YEAR_RANGE[0] <= year <= TAX_YEAR_RANGE[1] or not amounts:
            continue
        results.append(
            TaxReturnSummary(
                doc_id=doc_id,
                business_name=name,
                fein=fein,
                tax_year=cite_words(ref, [ref.words[0]], year, doc_id),
                gross_receipts=cite_words(ref, [amounts[0]], parse_amount(amounts[0].text), doc_id),
            )
        )
    return results


def extract_collateral(segment: Segment) -> CollateralValuation:
    """One collateral valuation report."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    return CollateralValuation(
        doc_id=doc_id,
        collateral_type=find_value(refs, ("Collateral type",), code_from_label, doc_id),
        value=find_value(refs, ("Value",), parse_amount, doc_id),
        valuation_date=find_value(refs, ("Valuation date",), parse_date, doc_id),
    )
