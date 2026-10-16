"""The internal model of one SYNTHETIC loan case: what the generator knows before anything is printed.

A CreditCase holds the business, its owners, the loan request, the existing debts, the profit and loss statement,
the balance sheet, two tax years, the collateral and 12 months of bank transactions WITH their categories. It
also holds the document-level differences the injected problems create (a typo on one document, a removed
statement month, a missing document, an embedded instruction). The renderers print the case; the
application builder (credit_application.py) reads the documented view of it.

How every field of application_as_documented is derived (to_application(oracle) must give the same result; amounts
in USD, rounded to cents):

- application_id: "APP-C-<nnn>" from the packet number. application_date, product, requested_amount, term_months,
  note_rate_pct, rate_type, purpose, stated_annual_revenue, tax_lien_unresolved, largest_customer_revenue_pct:
  the loan application form. Codes are printed as words (code_label: "line_of_credit" -> "Line of credit"; the
  structure "llc" prints as "LLC"); the code is the printed label casefolded with spaces replaced by "_".
- applicant.legal_name, fein, business_structure, state, formation_date, industry: the loan application form
  (when another document shows a different name or FEIN, the application form's value is used).
- bank_statements.account_number: the account number printed on the statements (the same on every month).
- bank_statements.months: one MonthlyDeposits per statement PRESENT in the packet, oldest first, computed from
  the transaction rows with the keyword rules of docintel/deposits.py (classify_deposit): total_deposits = sum of
  deposit rows; transfers_between_accounts / refunds_and_reversals / loan_proceeds / owner_contributions /
  one_time_receipts / unidentified_deposits = sums of the deposit rows of that category; cash_deposits = sum of
  "CASH DEPOSIT" rows. A removed statement month is absent.
- nsf_events_last_6_months: NSF / overdraft fee rows in the statements whose month is within 6 months of the
  latest statement month present. negative_balance_months_last_12: statements in the last 12 months whose
  printed closing balance is below zero. average_daily_balance_last_3_months: mean over every calendar day of the
  statements in the last 3 months of the end-of-day balance (the printed running balance after the last row
  posted on or before that day; the printed opening balance before the first row). cash_intensive_approved: False.
- debt.ttm_ebitda, debt.ttm_pnl_revenue: EBITDA and revenue printed on the profit and loss statement.
- debt.existing_monthly_payment_schedule: sum of the "Monthly payment" column of the debt schedule over EVERY row,
  including rows marked to be refinanced (0 when the schedule is missing or says "No existing business debt"):
  the agent's existing_annual_debt_service subtracts the refinanced payment itself, only when a payoff letter is
  in the file (NBC-4.4). debt.refinanced_monthly_payment: the same sum over rows marked to be refinanced (always 0
  in this generator). debt.existing_total_debt_balance: the printed total balance of the debt schedule (0 when
  missing or empty). A missing debt schedule is listed in to_application's unknowns (the 0s are not documented).
- debt.existing_monthly_payment_observed: sum of the loan payment withdrawal rows ("ACH PMT <lender> LOAN") over
  the statements present, divided by the number of statement months in which at least one such row appears
  (NBC-4.1 "observed monthly total": a loan or advance that started inside the window is not diluted by the
  months before it existed; 0 when no month has one). debt.mca_monthly_remittance: the same for "MCA REMIT" rows.
  Merchant cash advances are not listed on the debt schedule.
- new_loans_last_90_days: loan-proceeds deposit rows ("LOAN PROCEEDS", "MCA FUNDING") posted in the 90 calendar
  days that end on the last statement period end (NBC-4.4 "the most recent 90 days of statements"; both ends
  included).
- collateral: one item per collateral valuation document (type code and value).
- owners: the application's owners table, in order, with owner_id "O1", "O2", ...; credit_score and
  bankruptcy_within_7_years from the credit report row with the same name (persons only; None / False when the
  owner has no row).
- documents_provided (checklist codes): signed_application <- loan application; bank_statements_12_months <- 12
  consecutive statement months present; business_tax_returns_2_years <- a tax return summary with two tax years;
  ytd_pnl_and_balance_sheet <- P&L and balance sheet; debt_schedule <- debt schedule; principal_photo_ids <- a
  photo ID for every person principal (20% or more, or the control person); formation_documents and
  beneficial_ownership_certification <- the formation document (its members table); collateral_documents <-
  collateral valuation. cpa_reviewed_financials and payoff_letters are never in a packet (no document type); a
  request above $500,000 therefore always has a missing_document problem for cpa_reviewed_financials, and no
  request has the refinance purpose that would need payoff letters.
- notes: the application's Remarks text. ground_truth: None.
"""

from dataclasses import dataclass, field
from datetime import date

PERSON = "person"
ORGANIZATION = "organization"


@dataclass
class Party:
    """An owner of the business (a person, or an organization owning shares)."""

    owner_id: str
    name: str
    entity_type: str
    title: str
    ownership_pct: float
    is_control_person: bool
    country: str = "US"
    date_of_birth: date | None = None
    id_number: str | None = None
    id_expiry: date | None = None
    address: str | None = None
    credit_score: int | None = None
    bankruptcy_within_7_years: bool = False

    @property
    def is_principal(self) -> bool:
        """NBC-1.2: a person owning 20% or more, or the control person."""
        return self.entity_type == PERSON and (self.ownership_pct >= 20.0 or self.is_control_person)


@dataclass
class Txn:
    """One statement row. category is what the description documents (deposits.py rules); true_category is real.

    kind is "D" (deposit) or "W" (withdrawal). memo is an optional extra line printed under the row (free text).
    """

    posted: date
    description: str
    kind: str
    amount: float
    category: str
    true_category: str
    order: int = 0
    balance: float = 0.0
    memo: str | None = None


@dataclass
class Statement:
    """One month of the operating account. printed_* fields override what the page shows (integrity problems)."""

    month: date
    period_start: date
    period_end: date
    opening: float
    rows: list[Txn] = field(default_factory=list)
    printed_opening: float | None = None
    printed_total_deposits: float | None = None
    printed_total_withdrawals: float | None = None

    @property
    def total_deposits(self) -> float:
        """Sum of the deposit rows."""
        return round(sum(row.amount for row in self.rows if row.kind == "D"), 2)

    @property
    def total_withdrawals(self) -> float:
        """Sum of the withdrawal rows."""
        return round(sum(row.amount for row in self.rows if row.kind == "W"), 2)

    @property
    def closing(self) -> float:
        """Opening balance plus deposits minus withdrawals."""
        return round(self.opening + self.total_deposits - self.total_withdrawals, 2)

    @property
    def shown_opening(self) -> float:
        """The opening balance as printed."""
        return self.opening if self.printed_opening is None else self.printed_opening

    @property
    def shown_total_deposits(self) -> float:
        """Total deposits as printed."""
        return self.total_deposits if self.printed_total_deposits is None else self.printed_total_deposits

    @property
    def shown_total_withdrawals(self) -> float:
        """Total withdrawals as printed."""
        return self.total_withdrawals if self.printed_total_withdrawals is None else self.printed_total_withdrawals


@dataclass
class Debt:
    """One existing business debt. kind "loan" is on the debt schedule; kind "mca" is a merchant cash advance.

    scheduled_payment is what the debt schedule prints; observed_payment is what each statement month shows
    (they differ only for a debt_payment_mismatch). first_payment_index is the first statement month (0-11) with
    a payment; funded_on is set when the proceeds were deposited inside the statement window.
    """

    lender: str
    debt_type: str
    kind: str
    balance: float
    scheduled_payment: float
    observed_payment: float
    maturity: date
    payment_day: int
    first_payment_index: int = 0
    funded_on: date | None = None
    funded_amount: float = 0.0
    extra_payment_index: int | None = None
    extra_payment: float = 0.0
    to_be_refinanced: bool = False


@dataclass
class ProfitAndLossData:
    """Trailing-twelve-month profit and loss statement."""

    period_end: date
    revenue: float
    cost_of_goods_sold: float
    operating_expenses: float
    ebitda: float
    depreciation_amortization: float
    interest_expense: float
    net_income: float
    notes: list[str] = field(default_factory=list)


@dataclass
class BalanceSheetData:
    """Balance sheet at the P&L period end."""

    as_of: date
    cash: float
    accounts_receivable: float
    inventory: float
    fixed_assets: float
    total_assets: float
    accounts_payable: float
    long_term_debt: float
    total_liabilities: float
    equity: float


@dataclass
class TaxYear:
    """One year of the business tax return summary."""

    year: int
    gross_receipts: float


@dataclass
class CollateralData:
    """One pledged collateral item and its valuation."""

    collateral_type: str
    description: str
    value: float
    valuation_date: date
    appraiser: str


@dataclass
class InjectionPlan:
    """An embedded instruction: where it is printed and what it says."""

    location: str  # "remarks", "memo" (a transaction memo line) or "pnl_notes"
    technique: str
    text: str
    month_index: int | None = None


@dataclass
class CreditCase:
    """One SYNTHETIC loan case, with the document-level differences of its injected problems."""

    number: int
    packet_id: str
    application_date: date
    legal_name: str
    fein: str
    structure: str
    state: str
    formation_date: date
    industry: str
    address: str
    product: str
    amount: float
    term_months: int
    rate_pct: float
    rate_type: str
    purpose: str
    stated_revenue: float
    largest_customer_pct: float
    tax_lien: bool
    owners: list[Party]
    bank: str
    bank_layout: int
    account_number: str
    statements: list[Statement]
    debts: list[Debt]
    pnl: ProfitAndLossData
    balance_sheet: BalanceSheetData
    tax_years: list[TaxYear]
    collateral: list[CollateralData]
    remarks: str
    signer: str
    cpa_firm: str
    # Document-level differences (injected problems). Keys of doc_names / doc_feins are document types.
    doc_names: dict[str, str] = field(default_factory=dict)
    doc_feins: dict[str, str] = field(default_factory=dict)
    formation_members: list[Party] = field(default_factory=list)
    removed_month_index: int | None = None
    missing_documents: list[str] = field(default_factory=list)
    missing_photo_id_owner: str | None = None
    injection: InjectionPlan | None = None

    @property
    def application_id(self) -> str:
        """APP-C-<nnn>, matching the packet number."""
        return f"APP-C-{self.number:03d}"

    def name_on(self, doc_type: str) -> str:
        """The business name printed on a document type."""
        return self.doc_names.get(doc_type, self.legal_name)

    def fein_on(self, doc_type: str) -> str:
        """The FEIN printed on a document type."""
        return self.doc_feins.get(doc_type, self.fein)

    def present_statements(self) -> list[Statement]:
        """The statements printed in the packet (a removed month is left out), oldest first."""
        return [item for index, item in enumerate(self.statements) if index != self.removed_month_index]

    def loans(self) -> list[Debt]:
        """Debts listed on the debt schedule (merchant cash advances are not)."""
        return [debt for debt in self.debts if debt.kind == "loan"]

    def principals(self) -> list[Party]:
        """Person principals (NBC-1.2): they get a photo ID and a credit report row."""
        return [owner for owner in self.owners if owner.is_principal]
