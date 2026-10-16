"""The structured application the agent reviews: a small-business loan (credit), a commercial insurance submission,
or a startup term loan for a business that has not opened yet (startup_credit, from a Fundproof founder package).

Every application in this project is SYNTHETIC (scripts/make_applications.py, scripts/make_startup_applications.py).
These structured JSON files stand in for document extraction, which is Phase 2: in production the same fields would
be extracted, with page citations, from bank statements, tax returns, loss runs and property schedules.

Two kinds of field:
- structured fields (amounts, dates, codes, booleans) are validated here and are the only input to the reference
  policy and to tool planning;
- free text (the "notes" field, and names) is UNTRUSTED data. It is screened for instruction-like text, it never
  chooses a tool call, and it reaches the LLM only quoted as data.

ground_truth holds the label the synthetic generator designed for each case (expected recommendation and the rules
it is built to trigger). Nothing in the agent reads it; only the evaluation does.
"""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from uwcopilot.agent.vocab import Recommendation

# --------------------------------------------------------------------------------------------------------------
# Code lists. The policy decides what each code means (prohibited, restricted, allowed).
# --------------------------------------------------------------------------------------------------------------

CREDIT_INDUSTRIES: dict[str, str] = {
    "professional_services": "professional services (accounting and consulting)",
    "dental_practice": "dental practice",
    "medical_clinic": "outpatient medical clinic",
    "software_development": "software development",
    "landscaping": "landscaping services",
    "hardware_retail": "hardware retail store",
    "auto_repair": "auto repair shop",
    "machine_shop": "machine shop manufacturing",
    "bakery": "bakery",
    "restaurant": "full-service restaurant",
    "commercial_printing": "commercial printing",
    "veterinary_clinic": "veterinary clinic",
    "long_haul_trucking": "long-haul trucking",
    "freight_broker": "freight broker",
    "construction_contractor": "construction contractor",
    "used_vehicle_dealer": "used vehicle dealer",
    "money_services_business": "money services business",
    "cannabis_retail": "cannabis retail",
    "adult_entertainment": "adult entertainment",
    "casino_gambling": "casino and gambling",
    "firearms_manufacturing": "firearms manufacturing",
    "payday_lending": "payday lending",
    "virtual_currency_exchange": "virtual currency exchange",
    "multi_level_marketing": "multi-level marketing",
    "speculative_real_estate": "speculative real estate development",
    "political_lobbying": "political lobbying",
    "shell_company": "shell company with no operating revenue",
}

INSURANCE_CLASSES: dict[str, str] = {
    "office": "professional office",
    "retail_store": "retail store",
    "light_assembly": "light assembly manufacturing",
    "dental_office": "dental office",
    "restaurant_no_fryer": "restaurant without deep-fat frying",
    "restaurant_fryer": "restaurant with deep-fat frying",
    "warehouse": "warehouse and distribution",
    "habitational": "apartment building",
    "general_contractor": "general contractor",
    "woodworking": "woodworking shop",
    "furniture_manufacturing": "furniture manufacturing",
    "plastics_manufacturing": "plastics manufacturing",
    "auto_repair": "auto repair shop",
    "body_shop": "auto body shop",
    "fuel_station": "fuel station",
    "fireworks_storage": "fireworks storage",
    "ammunition_manufacturing": "ammunition manufacturing",
    "cannabis_dispensary": "cannabis dispensary",
    "crypto_mining": "virtual currency mining facility",
    "demolition_contractor": "demolition and blasting contractor",
    "large_nightclub": "nightclub with licensed occupancy above 300",
    "waste_recycling": "waste processing and recycling",
    "salvage_yard": "salvage yard",
}

CREDIT_DOCUMENTS: tuple[str, ...] = (
    "signed_application",
    "bank_statements_12_months",
    "business_tax_returns_2_years",
    "ytd_pnl_and_balance_sheet",
    "debt_schedule",
    "principal_photo_ids",
    "formation_documents",
    "beneficial_ownership_certification",
    "cpa_reviewed_financials",
    "collateral_documents",
    "payoff_letters",
)
INSURANCE_DOCUMENTS: tuple[str, ...] = (
    "signed_application",
    "property_schedule",
    "loss_runs",
    "prior_carrier_history",
    "revenue_and_payroll",
    "beneficial_ownership_statement",
    "inspection_report",
    "elevation_certificate",
)

CreditProduct = Literal["term_loan", "line_of_credit", "equipment_loan"]
BusinessStructure = Literal["corporation", "llc", "partnership", "sole_proprietorship"]
CollateralType = Literal["owner_occupied_cre", "new_equipment", "used_equipment", "accounts_receivable", "inventory"]
ConstructionClass = Literal["frame", "joisted_masonry", "non_combustible", "masonry_non_combustible", "fire_resistive"]
Coverage = Literal["property", "general_liability", "package"]
ClaimLine = Literal["property", "general_liability"]
WildfireHazard = Literal["low", "moderate", "high", "very_high"]
PartyEntityType = Literal["person", "organization"]
CreditIndustry = Literal[tuple(CREDIT_INDUSTRIES)]  # type: ignore[valid-type]
InsuranceClass = Literal[tuple(INSURANCE_CLASSES)]  # type: ignore[valid-type]
CreditDocument = Literal[CREDIT_DOCUMENTS]  # type: ignore[valid-type]
InsuranceDocument = Literal[INSURANCE_DOCUMENTS]  # type: ignore[valid-type]
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
APPLICATION_ID_PATTERN = r"^APP-[CI]-\d{3}$"
MAX_NOTES_CHARS = 4000


class Strict(BaseModel):
    """Base for every application model: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------------------------
# Shared parts
# --------------------------------------------------------------------------------------------------------------


class Owner(Strict):
    """One owner of the applicant. Organizations can own shares too (layered ownership)."""

    owner_id: str = Field(pattern=r"^O\d{1,2}$")
    name: str = Field(min_length=1, max_length=200)
    entity_type: PartyEntityType
    ownership_pct: float = Field(ge=0, le=100)
    country: str | None = Field(default=None, max_length=64)
    is_control_person: bool = False
    credit_score: int | None = Field(default=None, ge=300, le=850)
    bankruptcy_within_7_years: bool = False


class WatchlistDerivedOwner(Strict):
    """Ground truth: an owner whose name was made from a real OFAC SDN organization entry with small noise."""

    owner_id: str
    derived_from_list_entry_id: str
    derived_from_name: str
    noise_operations: list[str]


class GroundTruth(Strict):
    """What the generator designed this case to be. Read only by the evaluation, never by the agent."""

    scenario: str = Field(min_length=1)
    expected_recommendation: Recommendation
    expected_rules: list[str]
    contains_injection: bool = False
    injection_kind: str | None = None
    watchlist_derived_owners: list[WatchlistDerivedOwner] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


# --------------------------------------------------------------------------------------------------------------
# Credit (Northwind Business Credit, corpus/guidelines/northwind_business_credit.md)
# --------------------------------------------------------------------------------------------------------------


class CreditApplicant(Strict):
    """The business asking for the loan."""

    legal_name: str = Field(min_length=1, max_length=200)
    fein: str = Field(pattern=r"^\d{2}-\d{7}$")
    business_structure: BusinessStructure
    state: str = Field(pattern=r"^[A-Z]{2}$")
    formation_date: date
    industry: CreditIndustry


class MonthlyDeposits(Strict):
    """One month of deposits to the operating accounts, split the way NBC-3.3 needs.

    total_deposits includes everything below. cash_deposits is the cash part of the revenue deposits.
    unidentified_deposits are deposits whose nature the statement does not show (treated as non-revenue).
    """

    month: str = Field(pattern=MONTH_PATTERN)
    total_deposits: float = Field(ge=0)
    transfers_between_accounts: float = Field(default=0, ge=0)
    refunds_and_reversals: float = Field(default=0, ge=0)
    loan_proceeds: float = Field(default=0, ge=0)
    owner_contributions: float = Field(default=0, ge=0)
    one_time_receipts: float = Field(default=0, ge=0)
    unidentified_deposits: float = Field(default=0, ge=0)
    cash_deposits: float = Field(default=0, ge=0)


class BankStatementSummary(Strict):
    """Summary of the bank statements (NBC-3.2 to NBC-3.5). months are oldest first."""

    account_number: str = Field(pattern=r"^\d{10,12}$")
    months: list[MonthlyDeposits]
    nsf_events_last_6_months: int = Field(ge=0)
    negative_balance_months_last_12: int = Field(ge=0, le=12)
    average_daily_balance_last_3_months: float
    cash_intensive_approved: bool = False


class DebtServiceInputs(Strict):
    """Inputs for NOI and annual debt service (NBC-4.1) and leverage (NBC-4.4). Monthly amounts in USD."""

    ttm_ebitda: float
    ttm_pnl_revenue: float = Field(ge=0)
    existing_monthly_payment_schedule: float = Field(ge=0)
    existing_monthly_payment_observed: float = Field(ge=0)
    mca_monthly_remittance: float = Field(default=0, ge=0)
    refinanced_monthly_payment: float = Field(default=0, ge=0)
    existing_total_debt_balance: float = Field(ge=0)


class CollateralItem(Strict):
    """One pledged collateral item and its value on the basis NBC-5.1 requires."""

    collateral_type: CollateralType
    value: float = Field(gt=0)


class CreditApplication(Strict):
    """A SYNTHETIC small-business loan application."""

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    data_label: Literal["SYNTHETIC"] = "SYNTHETIC"
    line: Literal["credit"] = "credit"
    institution: Literal["northwind_business_credit"] = "northwind_business_credit"
    application_date: date
    applicant: CreditApplicant
    product: CreditProduct
    requested_amount: float = Field(gt=0)
    term_months: int = Field(ge=1, le=360)
    note_rate_pct: float = Field(gt=0, lt=40)
    rate_type: Literal["fixed", "variable"]
    purpose: Literal["working_capital", "equipment_purchase", "expansion", "refinance"]
    stated_annual_revenue: float = Field(ge=0)
    bank_statements: BankStatementSummary
    debt: DebtServiceInputs
    new_loans_last_90_days: int = Field(ge=0)
    tax_lien_unresolved: bool = False
    largest_customer_revenue_pct: float = Field(ge=0, le=100)
    collateral: list[CollateralItem] = Field(default_factory=list)
    owners: list[Owner] = Field(min_length=1)
    documents_provided: list[CreditDocument]
    notes: str = Field(default="", max_length=MAX_NOTES_CHARS)
    ground_truth: GroundTruth | None = None

    @model_validator(mode="after")
    def owner_ids_unique(self) -> "CreditApplication":
        """Owner ids identify parties in tool calls and memos, so they must be unique."""
        ids = [owner.owner_id for owner in self.owners]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate owner ids: {ids}")
        return self


# --------------------------------------------------------------------------------------------------------------
# Insurance (Harborline Mutual, corpus/guidelines/harborline_mutual_commercial.md)
# --------------------------------------------------------------------------------------------------------------


class InsuranceApplicant(Strict):
    """The business asking for coverage."""

    legal_name: str = Field(min_length=1, max_length=200)
    fein: str = Field(pattern=r"^\d{2}-\d{7}$")
    years_in_business: int = Field(ge=0)
    prior_experience_years: int = Field(default=0, ge=0)
    class_code: InsuranceClass
    fryer_suppression_inspected_within_6_months: bool = False


class Location(Strict):
    """One insured location on the property schedule (HM-5.1). Values are replacement cost in USD."""

    location_id: str = Field(pattern=r"^L\d{1,2}$")
    construction_class: ConstructionClass
    year_built: int = Field(ge=1800, le=2100)
    roof_year: int = Field(ge=1800, le=2100)
    systems_updated_year: int | None = Field(default=None, ge=1800, le=2100)
    stories: int = Field(ge=1, le=100)
    square_feet: int = Field(gt=0)
    sprinklered: bool
    protection_class: int = Field(ge=1, le=10)
    knob_and_tube_or_fuse_box: bool = False
    building_value: float = Field(ge=0)
    contents_value: float = Field(ge=0)
    business_income_value: float = Field(ge=0)
    wind_tier: Literal[0, 1, 2] = 0
    flood_zone: str = Field(default="X", pattern=r"^[A-Z]{1,3}$")
    wildfire_hazard: WildfireHazard = "low"
    vacant_days: int = Field(default=0, ge=0)


class Claim(Strict):
    """One claim line from a carrier loss run. incurred should equal paid + reserve (HM-3.3)."""

    date_of_loss: date
    line: ClaimLine
    paid: float = Field(ge=0)
    reserve: float = Field(ge=0)
    incurred: float = Field(ge=0)


class LossRunYear(Strict):
    """One policy year of the experience period. A None premium means the line was not written that year."""

    policy_year_start: date
    property_earned_premium: float | None = Field(default=None, ge=0)
    gl_earned_premium: float | None = Field(default=None, ge=0)
    claims: list[Claim] = Field(default_factory=list)


class LossRuns(Strict):
    """Carrier loss runs (HM-3.2). years are the covered policy years, oldest first."""

    carrier_produced: bool
    valued_as_of: date
    years: list[LossRunYear]
    uncovered_years: int = Field(default=0, ge=0)
    uncovered_years_explained: bool = True
    current_term_claims: list[Claim] = Field(default_factory=list)


class InsuranceApplication(Strict):
    """A SYNTHETIC commercial property and general liability submission."""

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    data_label: Literal["SYNTHETIC"] = "SYNTHETIC"
    line: Literal["insurance"] = "insurance"
    institution: Literal["harborline_mutual_commercial"] = "harborline_mutual_commercial"
    application_date: date
    proposed_effective_date: date
    applicant: InsuranceApplicant
    coverage: Coverage
    gl_limit_per_occurrence: float | None = Field(default=None, gt=0)
    gl_limit_aggregate: float | None = Field(default=None, gt=0)
    stated_tiv: float = Field(gt=0)
    locations: list[Location] = Field(min_length=1)
    loss_runs: LossRuns
    stated_claim_count: int = Field(ge=0)
    stated_total_incurred: float = Field(ge=0)
    annual_revenue: float = Field(ge=0)
    annual_payroll: float = Field(ge=0)
    subcontracted_work_pct: float = Field(default=0, ge=0, le=100)
    has_subcontractor_coi_tracking: bool = False
    foreign_products: bool = False
    owners: list[Owner] = Field(min_length=1)
    documents_provided: list[InsuranceDocument]
    notes: str = Field(default="", max_length=MAX_NOTES_CHARS)
    ground_truth: GroundTruth | None = None

    @model_validator(mode="after")
    def ids_unique(self) -> "InsuranceApplication":
        """Owner and location ids must be unique."""
        owner_ids = [owner.owner_id for owner in self.owners]
        location_ids = [location.location_id for location in self.locations]
        if len(owner_ids) != len(set(owner_ids)) or len(location_ids) != len(set(location_ids)):
            raise ValueError("owner ids and location ids must be unique")
        return self


# --------------------------------------------------------------------------------------------------------------
# Startup credit (SYNTHETIC Northwind startup term loan program, corpus/programs/northwind_startup_program.md)
# --------------------------------------------------------------------------------------------------------------

# Industry codes for the six Fundproof business types (config/studio/business_types/<id>.yaml). NSP-2.3 gives each
# its category (eligible or restricted); every Northwind industry code (CREDIT_INDUSTRIES) is accepted too.
STARTUP_INDUSTRIES: dict[str, str] = {
    "car_wash": "car wash",
    "laundromat": "coin-operated laundry",
    "motel": "motel or small hotel",
    "short_term_rental": "short-term rental operation",
    "restaurant_cafe": "restaurant, cafe, coffee or dessert shop",
    "retail_storefront": "small retail storefront",
}
STARTUP_BUSINESS_TYPES: tuple[str, ...] = tuple(STARTUP_INDUSTRIES)
# Metro ids of the Studio metro registry (config/studio/metros/<id>.yaml; a test keeps the two lists equal). A closed
# list, so the metro field can only name a known metro and can never carry the applicant's own words.
STARTUP_METRO_IDS: tuple[str, ...] = (
    "chicago_il_in",
    "dallas_fort_worth_tx",
    "houston_tx",
    "los_angeles_ca",
    "new_york_ny_nj_pa",
    "synthetic_metro",
)
STARTUP_DOCUMENTS: tuple[str, ...] = (
    "signed_application",
    "business_plan",
    "financial_projections",
    "sources_and_uses_statement",
    "personal_financial_statements",
    "principal_photo_ids",
    "formation_documents",
    "beneficial_ownership_certification",
    "collateral_documents",
    "equity_bank_statements",
    "owner_resumes_and_references",
    "lease_or_letter_of_intent",
    "contractor_or_equipment_quotes",
    "comparable_data_with_source",
)
STARTUP_SCHEMA_VERSION = "startup-application-v1"
STARTUP_APPLICATION_ID_PATTERN = r"^APP-S-\d{3,8}$"
SIMULATION_LABEL = "simulated, based on stated assumptions"
StartupBusinessType = Literal[STARTUP_BUSINESS_TYPES]  # type: ignore[valid-type]
StartupMetro = Literal[STARTUP_METRO_IDS]  # type: ignore[valid-type]
StartupIndustry = Literal[tuple({**CREDIT_INDUSTRIES, **STARTUP_INDUSTRIES})]  # type: ignore[valid-type]
StartupDocument = Literal[STARTUP_DOCUMENTS]  # type: ignore[valid-type]
StartupPurpose = Literal[
    "build_out_and_equipment", "equipment_purchase", "real_estate_purchase", "working_capital", "mixed_project_costs"
]
AssumptionStatus = Literal["USER_STATED", "ASSUMPTION", "VERIFIED", "MODEL_ESTIMATE"]


class StartupApplicant(Strict):
    """The business asking for a startup term loan. site_description is free text: screened, never cited."""

    business_name: str = Field(min_length=1, max_length=200)
    business_type: StartupBusinessType
    industry: StartupIndustry
    entity_type: BusinessStructure
    state: str = Field(pattern=r"^[A-Z]{2}$")
    formation_date: date | None = None  # None: the entity is not formed yet (NSP-2.4)
    planned_opening_date: date  # planned, or actual when the business has already opened (NSP-1.2)
    metro_id: StartupMetro
    site_description: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def industry_fits_business_type(self) -> "StartupApplicant":
        """NSP-2.3: the code of one Fundproof business type is used only for that business type.

        A more specific Northwind code (for example bakery or cannabis_retail) is accepted for any business type;
        the policy then applies the stricter category of the two codes.
        """
        if self.industry in STARTUP_INDUSTRIES and self.industry != self.business_type:
            raise ValueError(
                f"industry code {self.industry} is the code of business type {self.industry}, not of business type "
                f"{self.business_type}"
            )
        return self


class StartupOwner(Owner):
    """An owner with the fields the startup program adds (NSP-2.1, NSP-2.2, NSP-7.2)."""

    years_relevant_experience: float = Field(ge=0, le=70)
    experience_evidence_provided: bool = False  # resume and at least one reference for this owner
    tax_lien_unresolved: bool = False
    personal_guarantee: bool = False


class StartupLoanRequest(Strict):
    """The requested startup term loan (NSP-8.1)."""

    product: Literal["startup_term_loan"] = "startup_term_loan"
    amount: float = Field(gt=0)
    term_months: int = Field(ge=1, le=360)
    annual_rate_pct: float = Field(gt=0, lt=40)
    rate_type: Literal["fixed", "variable"]
    purpose: StartupPurpose


class UsesOfFunds(Strict):
    """Uses of funds by category in USD (NSP-3.2). Their sum is the total project cost (NSP-1.2)."""

    build_out: float = Field(default=0, ge=0)
    equipment: float = Field(default=0, ge=0)
    real_estate: float = Field(default=0, ge=0)
    initial_inventory: float = Field(default=0, ge=0)
    soft_costs: float = Field(default=0, ge=0)
    working_capital_reserve: float = Field(default=0, ge=0)
    other: float = Field(default=0, ge=0)

    def total(self) -> float:
        """Total project cost, rounded to cents."""
        return round(sum(self.model_dump().values()), 2)


class OtherDebtSource(Strict):
    """A loan or seller note that funds part of the project besides the startup loan (NSP-1.2, NSP-3.2).

    monthly_payment is the payment due in year 2; it is added to the projected annual debt service.
    """

    kind: Literal["seller_note", "other_loan"]
    amount: float = Field(gt=0)
    monthly_payment: float = Field(ge=0)


class SourcesOfFunds(Strict):
    """Sources of funds in USD (NSP-3.2). Only owner_cash_equity counts as equity (NSP-1.2).

    other_debt lists every other loan or seller note, with its payment; other is only for sources that are never
    repaid and are not owner cash (grants, landlord allowances).
    """

    loan: float = Field(ge=0)
    owner_cash_equity: float = Field(ge=0)
    other_debt: list[OtherDebtSource] = Field(default_factory=list, max_length=10)
    other: float = Field(default=0, ge=0)

    def other_debt_total(self) -> float:
        """Total of the other loans and seller notes, rounded to cents."""
        return round(sum(item.amount for item in self.other_debt), 2)

    def total(self) -> float:
        """Total sources, rounded to cents."""
        return round(self.loan + self.owner_cash_equity + self.other_debt_total() + self.other, 2)


class SourcesAndUses(Strict):
    """The sources and uses statement, and whether bank statements verify the owner cash equity (NSP-3.1)."""

    uses: UsesOfFunds
    sources: SourcesOfFunds
    equity_verified_by_statements: bool = False

    @model_validator(mode="after")
    def has_uses(self) -> "SourcesAndUses":
        """A project with no uses has no total project cost to measure equity against."""
        if self.uses.total() <= 0:
            raise ValueError("the uses of funds add up to zero")
        return self


class ProjectionYear(Strict):
    """One projection year: P10 / P50 / P90 revenue and NOI in USD (NSP-1.2). NOI can be negative."""

    year: Literal[1, 2, 3]
    revenue_p10: float = Field(ge=0)
    revenue_p50: float = Field(ge=0)
    revenue_p90: float = Field(ge=0)
    noi_p10: float
    noi_p50: float
    noi_p90: float

    @model_validator(mode="after")
    def percentiles_in_order(self) -> "ProjectionYear":
        """NSP-4.3: P10 may not be above P50, and P50 may not be above P90."""
        if not self.revenue_p10 <= self.revenue_p50 <= self.revenue_p90:
            raise ValueError(f"year {self.year}: revenue P10 <= P50 <= P90 does not hold")
        if not self.noi_p10 <= self.noi_p50 <= self.noi_p90:
            raise ValueError(f"year {self.year}: NOI P10 <= P50 <= P90 does not hold")
        return self

    @model_validator(mode="after")
    def noi_not_above_revenue(self) -> "ProjectionYear":
        """NSP-1.2: NOI is revenue minus operating expenses, so it can never be above revenue.

        This holds percentile by percentile too: when NOI is at most revenue on every simulated path, each NOI
        percentile is at most the same revenue percentile.
        """
        for label in ("p10", "p50", "p90"):
            noi = getattr(self, f"noi_{label}")
            revenue = getattr(self, f"revenue_{label}")
            if noi > revenue:
                raise ValueError(f"year {self.year}: NOI {label.upper()} is above revenue {label.upper()}")
        return self


class SimulationOutputs(Strict):
    """Information-only simulation outputs (NSP-4.4). They never change the recommendation."""

    label: Literal["simulated, based on stated assumptions"] = SIMULATION_LABEL
    simulator_version: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:+-]+$")
    n_paths: int = Field(ge=1)
    seed: int
    dscr_target: float = Field(gt=0)
    p_dscr_ge_target_year2: float = Field(ge=0, le=1)
    p_cash_negative_24m: float = Field(ge=0, le=1)


class Projections(Strict):
    """The 36-month projection summarized by year, monthly fixed costs, and optional simulation outputs."""

    years: list[ProjectionYear] = Field(min_length=3, max_length=3)
    monthly_fixed_costs: float = Field(ge=0)
    simulation: SimulationOutputs | None = None

    @model_validator(mode="after")
    def years_in_order(self) -> "Projections":
        """Years 1, 2 and 3, in that order."""
        if [item.year for item in self.years] != [1, 2, 3]:
            raise ValueError("projection years must be 1, 2 and 3 in order")
        return self

    def year(self, number: int) -> ProjectionYear:
        """The projection for year 1, 2 or 3."""
        return self.years[number - 1]


class MaterialAssumption(Strict):
    """One input of the projection (NSP-4.3, NSP-6.1).

    name and unit are short codes; source is free text from the plan, so it is screened for injection and never
    put into the evidence or the LLM prompt (evidence only says whether a source is stated).
    tornado_rank 1 is the assumption that moves year-2 net income the most (None: not ranked).
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: float
    unit: str = Field(pattern=r"^[A-Za-z0-9/%$._()-]{1,24}$")
    status: AssumptionStatus
    source: str | None = Field(default=None, max_length=300)
    supporting_document_provided: bool = False
    tornado_rank: int | None = Field(default=None, ge=1, le=50)


class StartupCreditApplication(Strict):
    """A SYNTHETIC startup term loan application (schema startup-application-v1), e.g. from a founder package."""

    application_id: str = Field(pattern=STARTUP_APPLICATION_ID_PATTERN)
    schema_version: Literal["startup-application-v1"] = STARTUP_SCHEMA_VERSION
    data_label: Literal["SYNTHETIC"] = "SYNTHETIC"
    line: Literal["startup_credit"] = "startup_credit"
    institution: Literal["northwind_business_credit"] = "northwind_business_credit"
    program: Literal["northwind_startup_program"] = "northwind_startup_program"
    submitted_date: date
    applicant: StartupApplicant
    owners: list[StartupOwner] = Field(min_length=1)
    loan_request: StartupLoanRequest
    sources_and_uses: SourcesAndUses
    projections: Projections
    material_assumptions: list[MaterialAssumption] = Field(default_factory=list, max_length=50)
    collateral: list[CollateralItem] = Field(default_factory=list)
    documents_provided: list[StartupDocument]
    notes: str = Field(default="", max_length=MAX_NOTES_CHARS)
    ground_truth: GroundTruth | None = None

    @model_validator(mode="after")
    def ids_unique(self) -> "StartupCreditApplication":
        """Owner ids, assumption names and tornado ranks must each be unique."""
        owner_ids = [owner.owner_id for owner in self.owners]
        names = [item.name for item in self.material_assumptions]
        ranks = [item.tornado_rank for item in self.material_assumptions if item.tornado_rank is not None]
        if len(owner_ids) != len(set(owner_ids)):
            raise ValueError(f"duplicate owner ids: {owner_ids}")
        if len(names) != len(set(names)):
            raise ValueError("material assumption names must be unique")
        if len(ranks) != len(set(ranks)):
            raise ValueError("tornado ranks must be unique")
        return self


AnyApplication = CreditApplication | InsuranceApplication | StartupCreditApplication
Application = Annotated[AnyApplication, Field(discriminator="line")]
APPLICATION_ADAPTER: TypeAdapter[AnyApplication] = TypeAdapter(Application)


# --------------------------------------------------------------------------------------------------------------
# Parties to screen, loading and hashing
# --------------------------------------------------------------------------------------------------------------


class Party(Strict):
    """Someone who must be screened (NBC-6.3, HM-8.2). party_id is used instead of the name in memos."""

    party_id: str
    name: str
    entity_type: PartyEntityType
    country: str | None
    role: str


def parse_application(data: dict) -> AnyApplication:
    """Validate a dict as a credit, insurance or startup credit application (chosen by its "line" field)."""
    return APPLICATION_ADAPTER.validate_python(data)


def load_application(path: Path) -> AnyApplication:
    """Read and validate one application JSON file."""
    return parse_application(json.loads(path.read_text(encoding="utf-8")))


def load_applications(directory: Path) -> list[AnyApplication]:
    """Every APP-*.json file in a directory, sorted by file name."""
    paths = sorted(directory.glob("APP-*.json"))
    if not paths:
        raise FileNotFoundError(f"No APP-*.json files in {directory}. Run `make applications` first.")
    return [load_application(path) for path in paths]


def application_inputs(application: AnyApplication) -> dict:
    """The application as plain JSON data, without the ground-truth label (which the agent never sees)."""
    return application.model_dump(mode="json", exclude={"ground_truth"})


def canonical_json(data: object) -> str:
    """Stable JSON text: sorted keys, no extra spaces. Used for hashes and loop-guard signatures."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def inputs_sha256(application: AnyApplication) -> str:
    """SHA-256 of the application inputs (ground truth excluded), recorded in every decision record."""
    return hashlib.sha256(canonical_json(application_inputs(application)).encode("utf-8")).hexdigest()


def applicant_name(application: AnyApplication) -> str:
    """The applicant's name: the legal name (credit, insurance) or the business name (startup credit)."""
    if isinstance(application, StartupCreditApplication):
        return application.applicant.business_name
    return application.applicant.legal_name


def screened_parties(application: AnyApplication) -> list[Party]:
    """The applicant and every listed owner: everyone the guidelines require to be screened."""
    parties = [
        Party(
            party_id="APPLICANT",
            name=applicant_name(application),
            entity_type="organization",
            country="US",
            role="applicant",
        )
    ]
    for owner in application.owners:
        parties.append(
            Party(
                party_id=owner.owner_id,
                name=owner.name,
                entity_type=owner.entity_type,
                country=owner.country,
                role=f"owner {owner.ownership_pct:g}%",
            )
        )
    return parties
