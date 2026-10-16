"""What the LLM extractor asks for, per document type: the key-value fields and the small tables (Phase 2).

Each DocSpec (extract_llm_fields.py) names the schema model the document fills (docintel/schemas.py), where it
goes in the packet extraction (slot; many=True for list slots such as photo_ids), the single fields and the tables.
Each field has a kind (text, money, date, ...) that decides how the copied text is parsed and described.

Which documents the LLM reads ("hybrid" extractor): the application forms, the P&L, the balance sheet, the debt
schedule, photo IDs, the credit report, the formation document, the tax return summary, the collateral
valuation, the cover email, the ownership statement, the inspection report, the property schedule (header and
location rows) and the loss runs (header, summary and premium years). Long tables stay with the rules table
parsers: bank statements (12 per packet, with every transaction) and the loss-run claim rows. Keeping them out
keeps each prompt inside a small local model's 4,096-token context.
"""

from uwcopilot.docintel.extract_llm_fields import DocSpec, FieldSpec, TableSpec, specs
from uwcopilot.docintel.schemas import (
    BalanceSheet,
    CollateralValuation,
    CoverEmail,
    CreditReport,
    CreditReportEntry,
    DebtItem,
    DebtSchedule,
    ExtractedOwner,
    FormationDocument,
    InspectionReport,
    InsuranceApplicationForm,
    LoanApplicationForm,
    LossRun,
    LossRunYear,
    OwnershipStatement,
    PhotoId,
    PriorCarrier,
    ProfitAndLoss,
    PropertySchedule,
    ScheduleLocation,
    TaxReturnSummary,
)

OWNER_FIELDS = (
    FieldSpec("name", "text"),
    FieldSpec("entity_type", "code", hint="person or organization"),
    FieldSpec("title", "text"),
    FieldSpec("ownership_pct", "percent"),
    FieldSpec("is_control_person", "bool", hint="the Control person column"),
    FieldSpec("country", "text"),
)
OWNERS = TableSpec("owners", ExtractedOwner, OWNER_FIELDS)
MEMBERS = TableSpec(
    "members",
    ExtractedOwner,
    (
        FieldSpec("name", "text"),
        FieldSpec("ownership_pct", "percent"),
        FieldSpec("is_control_person", "bool", hint="true only for the member named on the Control person line"),
    ),
)
MULTILINE = "may span several lines: cite p<page>:L<first>-L<last>"

LOAN_APPLICATION = DocSpec(
    "loan_application",
    LoanApplicationForm,
    "application",
    False,
    (
        FieldSpec("legal_name", "text"),
        FieldSpec("fein", "fein"),
        FieldSpec("business_structure", "code"),
        FieldSpec("state", "text", hint="state of organization"),
        FieldSpec("formation_date", "date"),
        FieldSpec("industry", "code"),
        FieldSpec("business_address", "text"),
        FieldSpec("application_date", "date"),
        FieldSpec("requested_amount", "money"),
        FieldSpec("product", "code"),
        FieldSpec("term_months", "int"),
        FieldSpec("note_rate_pct", "percent"),
        FieldSpec("rate_type", "code"),
        FieldSpec("purpose", "code"),
        FieldSpec("stated_annual_revenue", "money"),
        FieldSpec("largest_customer_revenue_pct", "percent"),
        FieldSpec("tax_lien_unresolved", "bool"),
        FieldSpec("remarks", "text", hint=f"the Remarks section; {MULTILINE}"),
    ),
    (OWNERS,),
)
PROFIT_AND_LOSS = DocSpec(
    "profit_and_loss",
    ProfitAndLoss,
    "profit_and_loss",
    False,
    (
        FieldSpec("business_name", "text", hint="the business name heading"),
        FieldSpec("period_end", "date"),
        *specs("money", "revenue", "cost_of_goods_sold", "operating_expenses", "ebitda"),
        *specs("money", "depreciation_amortization", "interest_expense", "net_income"),
    ),
)
BALANCE_SHEET = DocSpec(
    "balance_sheet",
    BalanceSheet,
    "balance_sheet",
    False,
    (
        FieldSpec("business_name", "text", hint="the business name heading"),
        FieldSpec("as_of", "date"),
        *specs("money", "cash", "accounts_receivable", "inventory", "total_assets", "total_liabilities"),
        *specs("money", "long_term_debt", "equity"),
    ),
)
DEBT_SCHEDULE = DocSpec(
    "debt_schedule",
    DebtSchedule,
    "debt_schedule",
    False,
    (
        FieldSpec("business_name", "text", hint="the business name heading"),
        *specs("money", "total_balance", "total_monthly_payment"),
    ),
    (
        TableSpec(
            "items",
            DebtItem,
            (
                FieldSpec("lender", "text"),
                FieldSpec("debt_type", "text"),
                *specs("money", "balance", "monthly_payment"),
                FieldSpec("maturity", "date"),
                FieldSpec("to_be_refinanced", "bool", hint="the Refinance column"),
            ),
        ),
    ),
)
PHOTO_ID = DocSpec(
    "photo_id",
    PhotoId,
    "photo_ids",
    True,
    (
        FieldSpec("name", "text"),
        FieldSpec("date_of_birth", "date"),
        FieldSpec("id_number", "identifier"),
        FieldSpec("expiry_date", "date"),
        FieldSpec("address", "text"),
    ),
)
CREDIT_REPORT = DocSpec(
    "credit_report",
    CreditReport,
    "credit_report",
    False,
    (),
    (
        TableSpec(
            "entries",
            CreditReportEntry,
            (
                FieldSpec("name", "text"),
                FieldSpec("credit_score", "int"),
                FieldSpec("bankruptcy_within_7_years", "bool"),
            ),
        ),
    ),
)
FORMATION = DocSpec(
    "formation_document",
    FormationDocument,
    "formation",
    False,
    (
        FieldSpec("entity_name", "text"),
        FieldSpec("fein", "fein"),
        FieldSpec("state", "text"),
        FieldSpec("formation_date", "date"),
    ),
    (MEMBERS,),
)
# One tax return document can list two tax years; every row becomes one TaxReturnSummary (see extract_llm.py).
TAX_RETURN = DocSpec(
    "tax_return_summary",
    TaxReturnSummary,
    "tax_returns",
    True,
    (FieldSpec("business_name", "text"), FieldSpec("fein", "fein")),
    (TableSpec("returns", TaxReturnSummary, (FieldSpec("tax_year", "int"), FieldSpec("gross_receipts", "money"))),),
)
COLLATERAL = DocSpec(
    "collateral_valuation",
    CollateralValuation,
    "collateral",
    True,
    (FieldSpec("collateral_type", "code"), FieldSpec("value", "money"), FieldSpec("valuation_date", "date")),
)

INSURANCE_APPLICATION = DocSpec(
    "insurance_application",
    InsuranceApplicationForm,
    "application",
    False,
    (
        FieldSpec("named_insured", "text"),
        FieldSpec("fein", "fein"),
        FieldSpec("years_in_business", "int"),
        *specs("code", "business_class", "coverage"),
        *specs(
            "money", "gl_limit_per_occurrence", "gl_limit_aggregate", "stated_tiv", "annual_revenue", "annual_payroll"
        ),
        FieldSpec("subcontracted_work_pct", "percent"),
        *specs("bool", "has_subcontractor_coi_tracking", "foreign_products"),
        *specs("date", "application_date", "proposed_effective_date"),
        FieldSpec("stated_claim_count", "int", hint="number of claims"),
        FieldSpec("stated_total_incurred", "money", hint="total incurred losses"),
        FieldSpec("remarks", "text", hint=f"the Remarks section; {MULTILINE}"),
    ),
    (
        TableSpec(
            "prior_carriers",
            PriorCarrier,
            (
                FieldSpec("carrier", "text"),
                FieldSpec("policy_number", "identifier"),
                FieldSpec("policy_year_start", "date"),
                *specs("money", "property_premium", "gl_premium"),
            ),
        ),
        OWNERS,
    ),
)
LOSS_RUN = DocSpec(
    "loss_run",
    LossRun,
    "loss_runs",
    True,
    (
        FieldSpec("carrier", "text", hint="the carrier name in the letterhead"),
        FieldSpec("policy_number", "identifier"),
        FieldSpec("insured_name", "text"),
        FieldSpec("valued_as_of", "date"),
        FieldSpec("carrier_produced", "bool", hint="true when a line says the loss run is carrier-produced"),
        FieldSpec("claim_count", "int", "summary.claim_count", hint="loss summary"),
        FieldSpec("total_paid", "money", "summary.total_paid", hint="loss summary"),
        FieldSpec("total_reserve", "money", "summary.total_reserve", hint="loss summary"),
        FieldSpec("total_incurred", "money", "summary.total_incurred", hint="loss summary"),
    ),
    (
        TableSpec(
            "years",
            LossRunYear,
            (
                FieldSpec("policy_year_start", "date"),
                FieldSpec("property_earned_premium", "money"),
                FieldSpec("gl_earned_premium", "money"),
            ),
        ),
    ),
)
PROPERTY_SCHEDULE = DocSpec(
    "property_schedule",
    PropertySchedule,
    "property_schedule",
    False,
    (FieldSpec("insured_name", "text"), FieldSpec("total_tiv", "money"), FieldSpec("signed_date", "date")),
    (
        TableSpec(
            "locations",
            ScheduleLocation,
            (
                FieldSpec("location_id", "identifier"),
                FieldSpec("address", "text"),
                FieldSpec("construction_class", "code"),
                FieldSpec("occupancy", "text"),
                *specs("int", "year_built", "roof_year", "stories", "square_feet"),
                FieldSpec("sprinklered", "bool"),
                FieldSpec("protection_class", "int"),
                *specs("money", "building_value", "contents_value", "business_income_value", "tiv"),
            ),
        ),
    ),
)
COVER_EMAIL = DocSpec(
    "cover_email",
    CoverEmail,
    "cover_email",
    False,
    (
        FieldSpec("sender", "text", hint="the From line"),
        FieldSpec("recipient", "text", hint="the To line"),
        FieldSpec("subject", "text"),
        FieldSpec("sent_date", "date"),
        FieldSpec("body", "text", hint=f"the message text after the headers; {MULTILINE}"),
    ),
)
OWNERSHIP_STATEMENT = DocSpec(
    "ownership_statement",
    OwnershipStatement,
    "ownership_statement",
    False,
    (FieldSpec("entity_name", "text"),),
    (OWNERS,),
)
INSPECTION_REPORT = DocSpec(
    "inspection_report",
    InspectionReport,
    "inspection_reports",
    True,
    (FieldSpec("location_id", "identifier"), FieldSpec("inspection_date", "date")),
)

DOC_SPECS: dict[str, DocSpec] = {
    spec.doc_type: spec
    for spec in (
        LOAN_APPLICATION,
        PROFIT_AND_LOSS,
        BALANCE_SHEET,
        DEBT_SCHEDULE,
        PHOTO_ID,
        CREDIT_REPORT,
        FORMATION,
        TAX_RETURN,
        COLLATERAL,
        INSURANCE_APPLICATION,
        LOSS_RUN,
        PROPERTY_SCHEDULE,
        COVER_EMAIL,
        OWNERSHIP_STATEMENT,
        INSPECTION_REPORT,
    )
}
