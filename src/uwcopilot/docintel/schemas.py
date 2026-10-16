"""Extraction schemas for SYNTHETIC document packets: LoanPacket (credit) and InsuranceSubmission (insurance).

Every extracted value is a Cited value: the value plus where it came from (document, page, line, bounding box)
and a confidence. Both extractors (the deterministic rules extractor and the LLM extractor) must return these
models, and nothing outside them is accepted (extra="forbid").

The generator builds a perfect ("oracle") extraction of every packet in the same schema, with the page and line
where the renderer printed each value. flatten_fields() turns any extraction into (field, key, value, citation)
rows. The ground-truth FieldTruth rows are the flattened oracle extraction, and the evaluation compares the
flattened pipeline output with them. Field paths and row keys are therefore defined once, here.

Row keys (so that table rows can be matched even when an extractor misses or adds a row):
- statement scalars: the statement month "YYYY-MM" (from the period start date);
- transactions: "YYYY-MM-DD|D|amount" for a deposit, "YYYY-MM-DD|W|amount" for a withdrawal (amount with 2 decimals);
- owners: "O1", "O2", ... by position in the table they come from;
- debt items: the lender name, casefolded;
- loss runs: the carrier name, casefolded; claims: the claim number; loss-run years: "carrier|YYYY-MM-DD";
- locations: the location id ("L1"); prior carriers: "carrier|YYYY-MM-DD";
- photo ids and credit-report entries: the person's name, casefolded; tax returns: the tax year.
"""

from datetime import date
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Strict(BaseModel):
    """Base for every extraction model: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class Cited(BaseModel, Generic[T]):
    """One extracted value and its source. value None means "not found in the packet".

    page is the 1-based page number in the packet PDF; line is the 1-based line number on that page in reading
    order as the extractor saw it; bbox is (x0, top, x1, bottom) in PDF points with the origin at the top-left.
    """

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    doc_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_text: str | None = None

    @property
    def found(self) -> bool:
        """True when the extractor found a value."""
        return self.value is not None


class DocumentInfo(Strict):
    """A document the pipeline found in the packet: its predicted type and pages."""

    doc_id: str
    doc_type: str
    pages: list[int]
    ocr_used: bool
    classification_confidence: float = Field(ge=0.0, le=1.0)


class ExtractedOwner(Strict):
    """An owner row from an application, a formation document or an ownership statement."""

    name: Cited[str] = Field(default_factory=Cited)
    entity_type: Cited[Literal["person", "organization"]] = Field(default_factory=Cited)
    title: Cited[str] = Field(default_factory=Cited)
    ownership_pct: Cited[float] = Field(default_factory=Cited)
    is_control_person: Cited[bool] = Field(default_factory=Cited)
    country: Cited[str] = Field(default_factory=Cited)


# --------------------------------------------------------------------------------------------------------------
# Credit (Northwind) packet
# --------------------------------------------------------------------------------------------------------------


class LoanApplicationForm(Strict):
    """The signed loan application form."""

    doc_id: str
    legal_name: Cited[str] = Field(default_factory=Cited)
    fein: Cited[str] = Field(default_factory=Cited)
    business_structure: Cited[str] = Field(default_factory=Cited)
    state: Cited[str] = Field(default_factory=Cited)
    formation_date: Cited[date] = Field(default_factory=Cited)
    industry: Cited[str] = Field(default_factory=Cited)
    business_address: Cited[str] = Field(default_factory=Cited)
    application_date: Cited[date] = Field(default_factory=Cited)
    requested_amount: Cited[float] = Field(default_factory=Cited)
    product: Cited[str] = Field(default_factory=Cited)
    term_months: Cited[int] = Field(default_factory=Cited)
    note_rate_pct: Cited[float] = Field(default_factory=Cited)
    rate_type: Cited[str] = Field(default_factory=Cited)
    purpose: Cited[str] = Field(default_factory=Cited)
    stated_annual_revenue: Cited[float] = Field(default_factory=Cited)
    largest_customer_revenue_pct: Cited[float] = Field(default_factory=Cited)
    tax_lien_unresolved: Cited[bool] = Field(default_factory=Cited)
    owners: list[ExtractedOwner] = Field(default_factory=list)
    remarks: Cited[str] = Field(default_factory=Cited)


class Transaction(Strict):
    """One bank statement transaction row. Exactly one of withdrawal and deposit is set."""

    posted_date: Cited[date] = Field(default_factory=Cited)
    description: Cited[str] = Field(default_factory=Cited)
    withdrawal: Cited[float] = Field(default_factory=Cited)
    deposit: Cited[float] = Field(default_factory=Cited)
    balance: Cited[float] = Field(default_factory=Cited)


class BankStatement(Strict):
    """One monthly statement of the business operating account."""

    doc_id: str
    bank_name: Cited[str] = Field(default_factory=Cited)
    account_holder: Cited[str] = Field(default_factory=Cited)
    account_number: Cited[str] = Field(default_factory=Cited)
    period_start: Cited[date] = Field(default_factory=Cited)
    period_end: Cited[date] = Field(default_factory=Cited)
    opening_balance: Cited[float] = Field(default_factory=Cited)
    total_deposits: Cited[float] = Field(default_factory=Cited)
    total_withdrawals: Cited[float] = Field(default_factory=Cited)
    closing_balance: Cited[float] = Field(default_factory=Cited)
    transactions: list[Transaction] = Field(default_factory=list)


class ProfitAndLoss(Strict):
    """Trailing-twelve-month profit and loss statement."""

    doc_id: str
    business_name: Cited[str] = Field(default_factory=Cited)
    period_end: Cited[date] = Field(default_factory=Cited)
    revenue: Cited[float] = Field(default_factory=Cited)
    cost_of_goods_sold: Cited[float] = Field(default_factory=Cited)
    operating_expenses: Cited[float] = Field(default_factory=Cited)
    ebitda: Cited[float] = Field(default_factory=Cited)
    depreciation_amortization: Cited[float] = Field(default_factory=Cited)
    interest_expense: Cited[float] = Field(default_factory=Cited)
    net_income: Cited[float] = Field(default_factory=Cited)


class BalanceSheet(Strict):
    """Balance sheet at the P&L period end."""

    doc_id: str
    business_name: Cited[str] = Field(default_factory=Cited)
    as_of: Cited[date] = Field(default_factory=Cited)
    cash: Cited[float] = Field(default_factory=Cited)
    accounts_receivable: Cited[float] = Field(default_factory=Cited)
    inventory: Cited[float] = Field(default_factory=Cited)
    total_assets: Cited[float] = Field(default_factory=Cited)
    total_liabilities: Cited[float] = Field(default_factory=Cited)
    long_term_debt: Cited[float] = Field(default_factory=Cited)
    equity: Cited[float] = Field(default_factory=Cited)


class DebtItem(Strict):
    """One row of the existing business debt schedule (NBC-3.1 item 5)."""

    lender: Cited[str] = Field(default_factory=Cited)
    debt_type: Cited[str] = Field(default_factory=Cited)
    balance: Cited[float] = Field(default_factory=Cited)
    monthly_payment: Cited[float] = Field(default_factory=Cited)
    maturity: Cited[date] = Field(default_factory=Cited)
    to_be_refinanced: Cited[bool] = Field(default_factory=Cited)


class DebtSchedule(Strict):
    """Schedule of all existing business debt."""

    doc_id: str
    business_name: Cited[str] = Field(default_factory=Cited)
    items: list[DebtItem] = Field(default_factory=list)
    total_balance: Cited[float] = Field(default_factory=Cited)
    total_monthly_payment: Cited[float] = Field(default_factory=Cited)


class PhotoId(Strict):
    """A SYNTHETIC specimen photo-ID card for one principal."""

    doc_id: str
    name: Cited[str] = Field(default_factory=Cited)
    date_of_birth: Cited[date] = Field(default_factory=Cited)
    id_number: Cited[str] = Field(default_factory=Cited)
    expiry_date: Cited[date] = Field(default_factory=Cited)
    address: Cited[str] = Field(default_factory=Cited)


class CreditReportEntry(Strict):
    """One principal's line on the (fictional) credit bureau summary."""

    name: Cited[str] = Field(default_factory=Cited)
    credit_score: Cited[int] = Field(default_factory=Cited)
    bankruptcy_within_7_years: Cited[bool] = Field(default_factory=Cited)


class CreditReport(Strict):
    """Credit bureau summary for every principal."""

    doc_id: str
    entries: list[CreditReportEntry] = Field(default_factory=list)


class FormationDocument(Strict):
    """Articles of organization summary with members, ownership and control person (NBC-6.1)."""

    doc_id: str
    entity_name: Cited[str] = Field(default_factory=Cited)
    fein: Cited[str] = Field(default_factory=Cited)
    state: Cited[str] = Field(default_factory=Cited)
    formation_date: Cited[date] = Field(default_factory=Cited)
    members: list[ExtractedOwner] = Field(default_factory=list)


class TaxReturnSummary(Strict):
    """Business tax return summary for one tax year (NOT an IRS form)."""

    doc_id: str
    business_name: Cited[str] = Field(default_factory=Cited)
    fein: Cited[str] = Field(default_factory=Cited)
    tax_year: Cited[int] = Field(default_factory=Cited)
    gross_receipts: Cited[float] = Field(default_factory=Cited)


class CollateralValuation(Strict):
    """Value of one pledged collateral item on the basis NBC-5.1 requires."""

    doc_id: str
    collateral_type: Cited[str] = Field(default_factory=Cited)
    value: Cited[float] = Field(default_factory=Cited)
    valuation_date: Cited[date] = Field(default_factory=Cited)


class LoanPacket(Strict):
    """Everything extracted from one SYNTHETIC loan packet."""

    packet_id: str
    line: Literal["credit"] = "credit"
    documents: list[DocumentInfo] = Field(default_factory=list)
    application: LoanApplicationForm | None = None
    statements: list[BankStatement] = Field(default_factory=list)
    profit_and_loss: ProfitAndLoss | None = None
    balance_sheet: BalanceSheet | None = None
    debt_schedule: DebtSchedule | None = None
    photo_ids: list[PhotoId] = Field(default_factory=list)
    credit_report: CreditReport | None = None
    formation: FormationDocument | None = None
    tax_returns: list[TaxReturnSummary] = Field(default_factory=list)
    collateral: list[CollateralValuation] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------------------------
# Insurance (Harborline) submission
# --------------------------------------------------------------------------------------------------------------


class PriorCarrier(Strict):
    """One prior-carrier row on the application (HM-3.1 item 4)."""

    carrier: Cited[str] = Field(default_factory=Cited)
    policy_number: Cited[str] = Field(default_factory=Cited)
    policy_year_start: Cited[date] = Field(default_factory=Cited)
    property_premium: Cited[float] = Field(default_factory=Cited)
    gl_premium: Cited[float] = Field(default_factory=Cited)


class InsuranceApplicationForm(Strict):
    """The commercial lines application (our own layout, loosely inspired by industry forms; not a copy)."""

    doc_id: str
    named_insured: Cited[str] = Field(default_factory=Cited)
    fein: Cited[str] = Field(default_factory=Cited)
    years_in_business: Cited[int] = Field(default_factory=Cited)
    business_class: Cited[str] = Field(default_factory=Cited)
    coverage: Cited[str] = Field(default_factory=Cited)
    gl_limit_per_occurrence: Cited[float] = Field(default_factory=Cited)
    gl_limit_aggregate: Cited[float] = Field(default_factory=Cited)
    stated_tiv: Cited[float] = Field(default_factory=Cited)
    annual_revenue: Cited[float] = Field(default_factory=Cited)
    annual_payroll: Cited[float] = Field(default_factory=Cited)
    subcontracted_work_pct: Cited[float] = Field(default_factory=Cited)
    has_subcontractor_coi_tracking: Cited[bool] = Field(default_factory=Cited)
    foreign_products: Cited[bool] = Field(default_factory=Cited)
    application_date: Cited[date] = Field(default_factory=Cited)
    proposed_effective_date: Cited[date] = Field(default_factory=Cited)
    stated_claim_count: Cited[int] = Field(default_factory=Cited)
    stated_total_incurred: Cited[float] = Field(default_factory=Cited)
    prior_carriers: list[PriorCarrier] = Field(default_factory=list)
    owners: list[ExtractedOwner] = Field(default_factory=list)
    remarks: Cited[str] = Field(default_factory=Cited)


class LossRunClaim(Strict):
    """One claim line on a carrier loss run."""

    claim_number: Cited[str] = Field(default_factory=Cited)
    date_of_loss: Cited[date] = Field(default_factory=Cited)
    line: Cited[Literal["property", "general_liability"]] = Field(default_factory=Cited)
    status: Cited[str] = Field(default_factory=Cited)
    paid: Cited[float] = Field(default_factory=Cited)
    reserve: Cited[float] = Field(default_factory=Cited)
    incurred: Cited[float] = Field(default_factory=Cited)


class LossRunYear(Strict):
    """Earned premium for one policy year on a loss run."""

    policy_year_start: Cited[date] = Field(default_factory=Cited)
    property_earned_premium: Cited[float] = Field(default_factory=Cited)
    gl_earned_premium: Cited[float] = Field(default_factory=Cited)


class LossRunSummary(Strict):
    """The totals block a carrier prints on its loss run."""

    claim_count: Cited[int] = Field(default_factory=Cited)
    total_paid: Cited[float] = Field(default_factory=Cited)
    total_reserve: Cited[float] = Field(default_factory=Cited)
    total_incurred: Cited[float] = Field(default_factory=Cited)


class LossRun(Strict):
    """One carrier's loss run (each fictional carrier has its own layout)."""

    doc_id: str
    carrier: Cited[str] = Field(default_factory=Cited)
    policy_number: Cited[str] = Field(default_factory=Cited)
    insured_name: Cited[str] = Field(default_factory=Cited)
    valued_as_of: Cited[date] = Field(default_factory=Cited)
    carrier_produced: Cited[bool] = Field(default_factory=Cited)
    years: list[LossRunYear] = Field(default_factory=list)
    claims: list[LossRunClaim] = Field(default_factory=list)
    summary: LossRunSummary = Field(default_factory=LossRunSummary)


class ScheduleLocation(Strict):
    """One location row on the property schedule / statement of values (HM-5.1)."""

    location_id: Cited[str] = Field(default_factory=Cited)
    address: Cited[str] = Field(default_factory=Cited)
    construction_class: Cited[str] = Field(default_factory=Cited)
    occupancy: Cited[str] = Field(default_factory=Cited)
    year_built: Cited[int] = Field(default_factory=Cited)
    roof_year: Cited[int] = Field(default_factory=Cited)
    stories: Cited[int] = Field(default_factory=Cited)
    square_feet: Cited[int] = Field(default_factory=Cited)
    sprinklered: Cited[bool] = Field(default_factory=Cited)
    protection_class: Cited[int] = Field(default_factory=Cited)
    building_value: Cited[float] = Field(default_factory=Cited)
    contents_value: Cited[float] = Field(default_factory=Cited)
    business_income_value: Cited[float] = Field(default_factory=Cited)
    tiv: Cited[float] = Field(default_factory=Cited)


class PropertySchedule(Strict):
    """The signed property schedule."""

    doc_id: str
    insured_name: Cited[str] = Field(default_factory=Cited)
    locations: list[ScheduleLocation] = Field(default_factory=list)
    total_tiv: Cited[float] = Field(default_factory=Cited)
    signed_date: Cited[date] = Field(default_factory=Cited)


class CoverEmail(Strict):
    """The broker's cover email. Its body is untrusted free text."""

    doc_id: str
    sender: Cited[str] = Field(default_factory=Cited)
    recipient: Cited[str] = Field(default_factory=Cited)
    subject: Cited[str] = Field(default_factory=Cited)
    sent_date: Cited[date] = Field(default_factory=Cited)
    body: Cited[str] = Field(default_factory=Cited)


class OwnershipStatement(Strict):
    """Beneficial ownership statement (HM-3.1 item 6)."""

    doc_id: str
    entity_name: Cited[str] = Field(default_factory=Cited)
    owners: list[ExtractedOwner] = Field(default_factory=list)


class InspectionReport(Strict):
    """Inspection report for a location with TIV above $5,000,000."""

    doc_id: str
    location_id: Cited[str] = Field(default_factory=Cited)
    inspection_date: Cited[date] = Field(default_factory=Cited)


class InsuranceSubmission(Strict):
    """Everything extracted from one SYNTHETIC insurance submission packet."""

    packet_id: str
    line: Literal["insurance"] = "insurance"
    documents: list[DocumentInfo] = Field(default_factory=list)
    application: InsuranceApplicationForm | None = None
    loss_runs: list[LossRun] = Field(default_factory=list)
    property_schedule: PropertySchedule | None = None
    cover_email: CoverEmail | None = None
    ownership_statement: OwnershipStatement | None = None
    inspection_reports: list[InspectionReport] = Field(default_factory=list)


PacketExtraction = LoanPacket | InsuranceSubmission


# --------------------------------------------------------------------------------------------------------------
# Flattening: one row per extracted value, with the path and row key used by the ground truth and evaluation
# --------------------------------------------------------------------------------------------------------------


class FlatField(Strict):
    """One extracted value with its field path, row key and citation (bbox added for the citation evaluation;
    optional, so older callers that build FlatFields without it still work)."""

    field: str
    key: str | None
    value: str | float | int | bool | None
    doc_id: str | None
    page: int | None
    line: int | None
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


def money_key(amount: float | None) -> str:
    """Amounts in row keys always have two decimals, so 4250 and 4250.0 give the same key."""
    return "" if amount is None else f"{amount:.2f}"


def plain(value: Any) -> str | float | int | bool | None:
    """Make a value JSON-simple: dates become ISO strings; everything else is kept."""
    if isinstance(value, date):
        return value.isoformat()
    return value


def cited_rows(prefix: str, key: str | None, model: BaseModel) -> list[FlatField]:
    """Every Cited attribute of a model as a FlatField named prefix.attribute (lists and sub-models skipped)."""
    rows: list[FlatField] = []
    for name in type(model).model_fields:
        item = getattr(model, name)
        if isinstance(item, Cited) and item.found:
            rows.append(
                FlatField(
                    field=f"{prefix}.{name}",
                    key=key,
                    value=plain(item.value),
                    doc_id=item.doc_id,
                    page=item.page,
                    line=item.line,
                    confidence=item.confidence,
                    bbox=item.bbox,
                )
            )
    return rows


def casefold_key(cited: Cited) -> str | None:
    """A row key from a cited text value: casefolded and whitespace-normalized (None when missing)."""
    if not cited.found:
        return None
    return " ".join(str(cited.value).split()).casefold()


def owner_rows(prefix: str, owners: list[ExtractedOwner]) -> list[FlatField]:
    """Owner rows keyed O1, O2, ... by table position."""
    rows: list[FlatField] = []
    for index, owner in enumerate(owners, start=1):
        rows.extend(cited_rows(prefix, f"O{index}", owner))
    return rows


def statement_month(statement: BankStatement) -> str | None:
    """The statement's natural key: the month of its period start."""
    if statement.period_start.value is None:
        return None
    return statement.period_start.value.strftime("%Y-%m")


def transaction_key(transaction: Transaction) -> str | None:
    """'YYYY-MM-DD|D|amount' for a deposit, '...|W|amount' for a withdrawal (None if the date is missing)."""
    if transaction.posted_date.value is None:
        return None
    day = transaction.posted_date.value.isoformat()
    if transaction.deposit.found:
        return f"{day}|D|{money_key(transaction.deposit.value)}"
    if transaction.withdrawal.found:
        return f"{day}|W|{money_key(transaction.withdrawal.value)}"
    return f"{day}|?|"


def flatten_loan(packet: LoanPacket) -> list[FlatField]:
    """Flatten a LoanPacket into field rows (see the module docstring for keys)."""
    rows: list[FlatField] = []
    if packet.application is not None:
        rows.extend(cited_rows("application", None, packet.application))
        rows.extend(owner_rows("application.owners", packet.application.owners))
    for statement in packet.statements:
        month = statement_month(statement)
        rows.extend(cited_rows("statement", month, statement))
        for transaction in statement.transactions:
            rows.extend(cited_rows("statement.transactions", transaction_key(transaction), transaction))
    if packet.profit_and_loss is not None:
        rows.extend(cited_rows("profit_and_loss", None, packet.profit_and_loss))
    if packet.balance_sheet is not None:
        rows.extend(cited_rows("balance_sheet", None, packet.balance_sheet))
    if packet.debt_schedule is not None:
        rows.extend(cited_rows("debt_schedule", None, packet.debt_schedule))
        for item in packet.debt_schedule.items:
            rows.extend(cited_rows("debt_schedule.items", casefold_key(item.lender), item))
    for photo_id in packet.photo_ids:
        rows.extend(cited_rows("photo_id", casefold_key(photo_id.name), photo_id))
    if packet.credit_report is not None:
        for entry in packet.credit_report.entries:
            rows.extend(cited_rows("credit_report.entries", casefold_key(entry.name), entry))
    if packet.formation is not None:
        rows.extend(cited_rows("formation", None, packet.formation))
        rows.extend(owner_rows("formation.members", packet.formation.members))
    for tax_return in packet.tax_returns:
        year = None if tax_return.tax_year.value is None else str(tax_return.tax_year.value)
        rows.extend(cited_rows("tax_return", year, tax_return))
    for item in packet.collateral:
        rows.extend(cited_rows("collateral", casefold_key(item.collateral_type), item))
    return rows


def loss_run_year_key(carrier: str | None, year: LossRunYear) -> str | None:
    """'carrier|YYYY-MM-DD' for a loss-run premium year."""
    if year.policy_year_start.value is None:
        return None
    return f"{carrier or ''}|{year.policy_year_start.value.isoformat()}"


def flatten_insurance(packet: InsuranceSubmission) -> list[FlatField]:
    """Flatten an InsuranceSubmission into field rows (see the module docstring for keys)."""
    rows: list[FlatField] = []
    if packet.application is not None:
        rows.extend(cited_rows("application", None, packet.application))
        rows.extend(owner_rows("application.owners", packet.application.owners))
        for prior in packet.application.prior_carriers:
            key = None
            if prior.policy_year_start.found:
                key = f"{casefold_key(prior.carrier) or ''}|{prior.policy_year_start.value.isoformat()}"
            rows.extend(cited_rows("application.prior_carriers", key, prior))
    for loss_run in packet.loss_runs:
        carrier = casefold_key(loss_run.carrier)
        rows.extend(cited_rows("loss_run", carrier, loss_run))
        rows.extend(cited_rows("loss_run.summary", carrier, loss_run.summary))
        for year in loss_run.years:
            rows.extend(cited_rows("loss_run.years", loss_run_year_key(carrier, year), year))
        for claim in loss_run.claims:
            claim_key = None if not claim.claim_number.found else str(claim.claim_number.value).strip()
            rows.extend(cited_rows("loss_run.claims", claim_key, claim))
    if packet.property_schedule is not None:
        rows.extend(cited_rows("property_schedule", None, packet.property_schedule))
        for location in packet.property_schedule.locations:
            location_key = None if not location.location_id.found else str(location.location_id.value).strip()
            rows.extend(cited_rows("property_schedule.locations", location_key, location))
    if packet.cover_email is not None:
        rows.extend(cited_rows("cover_email", None, packet.cover_email))
    if packet.ownership_statement is not None:
        rows.extend(cited_rows("ownership_statement", None, packet.ownership_statement))
        rows.extend(owner_rows("ownership_statement.owners", packet.ownership_statement.owners))
    for report in packet.inspection_reports:
        location_key = None if not report.location_id.found else str(report.location_id.value).strip()
        rows.extend(cited_rows("inspection_report", location_key, report))
    return rows


def flatten_fields(packet: PacketExtraction) -> list[FlatField]:
    """Flatten either kind of packet extraction."""
    if isinstance(packet, LoanPacket):
        return flatten_loan(packet)
    return flatten_insurance(packet)


def parse_extraction(data: dict) -> PacketExtraction:
    """Validate a dict as a LoanPacket or InsuranceSubmission (chosen by its "line" field)."""
    if data.get("line") == "credit":
        return LoanPacket.model_validate(data)
    return InsuranceSubmission.model_validate(data)
