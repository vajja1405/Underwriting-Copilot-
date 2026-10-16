"""Static tables the evidence ledger (ledger.py) is built from. Data only: no logic, no imports from the engine.

- FACTS_BY_LINE: the application facts that become ledger claims (id "APP-<application path>"), with label, unit
  and display format. Names, FEINs and free text are left out on purpose: they are untrusted document text and
  never enter a memo (parties appear as ids).
- COUNT_FACTS_BY_LINE: facts that count the items of a list (statement months, loss-run years, locations).
- CALC_UNITS: agent.policy.CalcUnit -> (ledger unit, renderer display format).
- GAP_CHECKS: document checks whose failure means something is missing (UNKNOWN), not a disagreement.
- CONFLICT_CLAIMS_BY_CHECK: the application facts a contradiction from that check puts in doubt (CONFLICTING),
  besides the check's own claim "CHK-<check_id>".
- CALC_INPUT_CLAIMS and CALC_EXTRA_DEPENDENCIES: which claims a calculation reads (its Calculation.inputs names,
  plus the claim lines a loss-run calculation reads without listing them). Used for depends_on_conflicting.
- CALC_UNKNOWN_PATTERNS: claim-id patterns of values the extraction may list as unread (owners, statement months,
  loss-run years) that a calculation reads. With CALC_INPUT_CLAIMS they give depends_on_unknown: a calculation
  over a value no document provided is UNKNOWN, never VERIFIED.
- NOT_EVALUATED_CLAIMS: calculations that do not exist because the policy could not run a rule (UNKNOWN claims),
  and NOT_EVALUATED_REASONS: a more precise reason for a calculation that does not depend on the missing input.
- UNDOCUMENTED_LOCATION_FIELDS: location fields no packet document prints (docintel to_application_insurance writes
  the agent model's defaults); ASSERTION_UNKNOWN_PATTERNS: the unread values each passed rule rests on.
- CONTRADICTION_APPLICATION_PATHS: the application paths each contradiction compares; a resolution that replaces
  one of them supersedes the contradiction. CHECK_INPUT_PATTERNS: the claim ids of the values each of those checks
  reads; a check outcome over an UNKNOWN one is UNKNOWN (depends_on_unknown).
- TOPIC_PHRASES and RULE_TOPIC: the expression and the three phrases of each rule's assertion. Phrases are what the
  renderer prints for {{assert:ID}}, so they contain no digits, number words, currency or percent signs. The
  expressions name no section: an assertion's section id is copied from the policy result it was built from.
"""

from typing import NamedTuple


class FactSpec(NamedTuple):
    """One application fact: claim id, application path, label, ledger unit, display format."""

    claim_id: str
    path: str
    label: str
    unit: str
    display: str


CREDIT_FACTS: tuple[FactSpec, ...] = (
    FactSpec("APP-product", "product", "Product", "TEXT", "text"),
    FactSpec("APP-requested_amount", "requested_amount", "Requested amount", "USD", "usd2"),
    FactSpec("APP-term_months", "term_months", "Term", "MONTHS", "months"),
    FactSpec("APP-note_rate_pct", "note_rate_pct", "Note rate", "PERCENT", "pct1"),
    FactSpec("APP-rate_type", "rate_type", "Rate type", "TEXT", "text"),
    FactSpec("APP-stated_annual_revenue", "stated_annual_revenue", "Stated annual revenue", "USD", "usd2"),
    FactSpec("APP-applicant.industry", "applicant.industry", "Industry code", "TEXT", "text"),
    FactSpec("APP-applicant.formation_date", "applicant.formation_date", "Formation date", "DATE", "text"),
    FactSpec("APP-application_date", "application_date", "Application date", "DATE", "text"),
    FactSpec("APP-debt.ttm_ebitda", "debt.ttm_ebitda", "TTM EBITDA", "USD", "usd2"),
    FactSpec("APP-debt.ttm_pnl_revenue", "debt.ttm_pnl_revenue", "TTM P&L revenue", "USD", "usd2"),
    FactSpec(
        "APP-debt.existing_monthly_payment_schedule",
        "debt.existing_monthly_payment_schedule",
        "Existing monthly debt payments (debt schedule)",
        "USD",
        "usd2",
    ),
    FactSpec(
        "APP-debt.existing_monthly_payment_observed",
        "debt.existing_monthly_payment_observed",
        "Existing monthly debt payments (observed in the statements)",
        "USD",
        "usd2",
    ),
    FactSpec("APP-debt.mca_monthly_remittance", "debt.mca_monthly_remittance", "MCA monthly remittance", "USD", "usd2"),
    FactSpec(
        "APP-debt.refinanced_monthly_payment",
        "debt.refinanced_monthly_payment",
        "Monthly payment to be refinanced",
        "USD",
        "usd2",
    ),
    FactSpec(
        "APP-debt.existing_total_debt_balance",
        "debt.existing_total_debt_balance",
        "Existing total debt balance",
        "USD",
        "usd2",
    ),
    FactSpec(
        "APP-new_loans_last_90_days", "new_loans_last_90_days", "New loans or advances, last 90 days", "COUNT", "int"
    ),
    FactSpec(
        "APP-bank_statements.nsf_events_last_6_months",
        "bank_statements.nsf_events_last_6_months",
        "NSF or overdraft events, last 6 months",
        "COUNT",
        "int",
    ),
    FactSpec(
        "APP-bank_statements.negative_balance_months_last_12",
        "bank_statements.negative_balance_months_last_12",
        "Months ending negative, last 12 months",
        "MONTHS",
        "months",
    ),
    FactSpec(
        "APP-bank_statements.average_daily_balance_last_3_months",
        "bank_statements.average_daily_balance_last_3_months",
        "Average daily balance, last 3 months",
        "USD",
        "usd2",
    ),
    FactSpec(
        "APP-largest_customer_revenue_pct",
        "largest_customer_revenue_pct",
        "Largest customer share of revenue",
        "PERCENT",
        "pct1",
    ),
)
INSURANCE_FACTS: tuple[FactSpec, ...] = (
    FactSpec("APP-coverage", "coverage", "Coverage requested", "TEXT", "text"),
    FactSpec("APP-applicant.class_code", "applicant.class_code", "Class code", "TEXT", "text"),
    FactSpec("APP-applicant.years_in_business", "applicant.years_in_business", "Years in business", "YEARS", "years"),
    FactSpec("APP-stated_tiv", "stated_tiv", "Application TIV", "USD", "usd2"),
    FactSpec("APP-stated_claim_count", "stated_claim_count", "Claims stated on the application", "COUNT", "int"),
    FactSpec(
        "APP-stated_total_incurred",
        "stated_total_incurred",
        "Total incurred stated on the application",
        "USD",
        "usd2",
    ),
    FactSpec("APP-annual_revenue", "annual_revenue", "Annual revenue", "USD", "usd2"),
    FactSpec("APP-annual_payroll", "annual_payroll", "Annual payroll", "USD", "usd2"),
    FactSpec("APP-gl_limit_per_occurrence", "gl_limit_per_occurrence", "GL limit per occurrence", "USD", "usd2"),
    FactSpec("APP-gl_limit_aggregate", "gl_limit_aggregate", "GL aggregate limit", "USD", "usd2"),
    FactSpec("APP-loss_runs.valued_as_of", "loss_runs.valued_as_of", "Loss runs valued as of", "DATE", "text"),
    FactSpec("APP-proposed_effective_date", "proposed_effective_date", "Proposed effective date", "DATE", "text"),
)
STARTUP_FACTS: tuple[FactSpec, ...] = (
    FactSpec("APP-loan_request.amount", "loan_request.amount", "Requested amount", "USD", "usd2"),
    FactSpec("APP-loan_request.term_months", "loan_request.term_months", "Term", "MONTHS", "months"),
    FactSpec("APP-loan_request.annual_rate_pct", "loan_request.annual_rate_pct", "Note rate", "PERCENT", "pct1"),
    FactSpec("APP-loan_request.rate_type", "loan_request.rate_type", "Rate type", "TEXT", "text"),
    FactSpec("APP-applicant.business_type", "applicant.business_type", "Business type", "TEXT", "text"),
    FactSpec("APP-applicant.metro_id", "applicant.metro_id", "Metro", "TEXT", "text"),
    FactSpec(
        "APP-applicant.planned_opening_date",
        "applicant.planned_opening_date",
        "Planned opening date",
        "DATE",
        "text",
    ),
    FactSpec(
        "APP-sources_and_uses.sources.owner_cash_equity",
        "sources_and_uses.sources.owner_cash_equity",
        "Owner cash equity",
        "USD",
        "usd2",
    ),
)
FACTS_BY_LINE: dict[str, tuple[FactSpec, ...]] = {
    "credit": CREDIT_FACTS,
    "insurance": INSURANCE_FACTS,
    "startup_credit": STARTUP_FACTS,
}
COUNT_FACTS_BY_LINE: dict[str, tuple[FactSpec, ...]] = {
    "credit": (
        FactSpec(
            "APP-bank_statements.months_provided",
            "bank_statements.months",
            "Statement months provided",
            "MONTHS",
            "months",
        ),
    ),
    "insurance": (
        FactSpec("APP-loss_runs.years_provided", "loss_runs.years", "Loss-run policy years provided", "YEARS", "years"),
        FactSpec("APP-locations.count", "locations", "Locations on the property schedule", "COUNT", "int"),
    ),
    "startup_credit": (),
}

# agent.policy.CalcUnit -> (ledger unit, renderer display format). probability prints as a 2-decimal ratio.
CALC_UNITS: dict[str, tuple[str, str]] = {
    "usd": ("USD", "usd2"),
    "ratio": ("RATIO", "ratio2"),
    "pct": ("PERCENT", "pct1"),
    "months": ("MONTHS", "months"),
    "count": ("COUNT", "int"),
    "days": ("DAYS", "int"),
    "score": ("NONE", "int"),
    "usd_per_sqft": ("USD_PER_SQFT", "usd2"),
    "probability": ("PROBABILITY", "ratio2"),
}

CONFLICT_CLAIMS_BY_CHECK: dict[str, tuple[str, ...]] = {
    "revenue_vs_deposits": ("APP-stated_annual_revenue",),
    "tax_return_vs_pnl": ("APP-debt.ttm_pnl_revenue",),
    "debt_payment_consistency": (
        "APP-debt.existing_monthly_payment_schedule",
        "APP-debt.existing_monthly_payment_observed",
    ),
    "tiv_consistency": ("APP-stated_tiv",),
    "claims_history_consistency": ("APP-stated_claim_count", "APP-stated_total_incurred"),
}

# Checks that find something missing (not a disagreement): a failure is an UNKNOWN, not a CONFLICTING claim.
GAP_CHECKS: frozenset[str] = frozenset(
    {"required_documents", "statement_months_complete", "loss_run_coverage", "loss_run_valuation_age"}
)
EXISTING_DEBT_CLAIMS: tuple[str, ...] = (
    "APP-debt.existing_monthly_payment_schedule",
    "APP-debt.existing_monthly_payment_observed",
    "APP-debt.mca_monthly_remittance",
    "APP-debt.refinanced_monthly_payment",
)
LOSS_RUN_CLAIMS: tuple[str, ...] = (
    "CHK-loss_run_totals",
    "CHK-loss_run_line_balance",
    "CHK-claims_history_consistency",
)
CALC_INPUT_CLAIMS: dict[str, tuple[str, ...]] = {
    "stated_annual_revenue": ("APP-stated_annual_revenue",),
    "verified_annual_revenue": ("CALC-verified_annual_revenue",),
    "ttm_ebitda": ("APP-debt.ttm_ebitda",),
    "ttm_pnl_revenue": ("APP-debt.ttm_pnl_revenue",),
    "noi": ("CALC-noi",),
    "annual_debt_service": ("CALC-annual_debt_service",),
    "existing_annual_debt_service": EXISTING_DEBT_CLAIMS,
    "proposed_annual_debt_service": ("CALC-proposed_annual_debt_service",),
    "requested_amount": ("APP-requested_amount",),
    "note_rate_pct": ("APP-note_rate_pct",),
    "term_months": ("APP-term_months",),
    "noi_with_stated_revenue": ("APP-stated_annual_revenue", "APP-debt.ttm_ebitda", "APP-debt.ttm_pnl_revenue"),
    "stressed_noi": ("CALC-noi", "CALC-verified_annual_revenue"),
    "annual_debt_service_stressed": (
        *EXISTING_DEBT_CLAIMS,
        "APP-requested_amount",
        "APP-note_rate_pct",
        "APP-term_months",
    ),
    "total_debt_after_closing": ("APP-debt.existing_total_debt_balance", "APP-requested_amount"),
    "loan_amount": ("APP-requested_amount",),
    "application_tiv": ("APP-stated_tiv",),
    "schedule_tiv": ("CALC-schedule_tiv",),
    "stated_claim_count": ("APP-stated_claim_count",),
    "stated_total_incurred": ("APP-stated_total_incurred",),
    "loss_run_total_incurred": LOSS_RUN_CLAIMS,
    "loss_run_claim_count": LOSS_RUN_CLAIMS,
    "valued_as_of": ("APP-loss_runs.valued_as_of",),
    "proposed_effective_date": ("APP-proposed_effective_date",),
}
CALC_EXTRA_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    # Verified revenue is read from the statements, so a statement integrity problem puts it in doubt.
    "CALC-verified_annual_revenue": ("CHK-balance_continuity", "APP-bank_statements.months_provided"),
    "CALC-schedule_tiv": ("CHK-tiv_consistency",),
    "CALC-loss_ratio_5yr_property": LOSS_RUN_CLAIMS,
    "CALC-loss_ratio_5yr_general_liability": LOSS_RUN_CLAIMS,
    "CALC-max_annual_loss_ratio_property": LOSS_RUN_CLAIMS,
    "CALC-max_annual_loss_ratio_general_liability": LOSS_RUN_CLAIMS,
    "CALC-claim_frequency": LOSS_RUN_CLAIMS,
}

# Calculation -> regular expressions over claim ids it reads besides CALC_INPUT_CLAIMS (ids built from application
# paths: "owners[0].credit_score" -> "APP-owners.0.credit_score", "loss_runs.years[2024-03-01]" ->
# "APP-loss_runs.years.2024-03-01"). Only UNKNOWN claims can match: these ids exist only for unread values.
STATEMENT_MONTH_PATTERN = r"^APP-bank_statements\.months\.\d+$"
LOSS_RUN_PATTERNS = (r"^APP-loss_runs\.years\.", r"^APP-loss_runs\.current_term_claims$")
CALC_UNKNOWN_PATTERNS: dict[str, tuple[str, ...]] = {
    "CALC-verified_annual_revenue": (STATEMENT_MONTH_PATTERN,),
    "CALC-min_principal_credit_score": (r"^APP-owners\.\d+\.credit_score$", r"^APP-owners\.\d+\.is_control_person$"),
    "CALC-loss_ratio_5yr_property": LOSS_RUN_PATTERNS,
    "CALC-loss_ratio_5yr_general_liability": LOSS_RUN_PATTERNS,
    "CALC-max_annual_loss_ratio_property": LOSS_RUN_PATTERNS,
    "CALC-max_annual_loss_ratio_general_liability": LOSS_RUN_PATTERNS,
    "CALC-claim_frequency": LOSS_RUN_PATTERNS,
    "CALC-incurred_difference_pct": LOSS_RUN_PATTERNS,
    "CALC-unbalanced_claim_lines": LOSS_RUN_PATTERNS,
}

# NotEvaluated rule -> calculations that are missing because of it: (calc id, label, agent CalcUnit).
NOT_EVALUATED_CLAIMS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "verified_revenue_minimum": (
        ("CALC-verified_annual_revenue", "Verified annual revenue (eligible deposits, 12 months)", "usd"),
    ),
    "revenue_gap": (("CALC-revenue_gap_pct", "Revenue gap (stated minus verified, percent of stated)", "pct"),),
    "dscr": (
        ("CALC-noi", "Net operating income (TTM EBITDA less revenue shortfall)", "usd"),
        ("CALC-annual_debt_service", "Annual debt service (existing plus proposed)", "usd"),
        ("CALC-dscr", "Decision DSCR (NOI / annual debt service)", "ratio"),
    ),
    "leverage": (("CALC-leverage", "Total debt after closing / NOI", "ratio"),),
    "stress_tests": (("CALC-stressed_dscr_revenue", "DSCR after a 20% revenue stress", "ratio"),),
    "projected_dscr": (("CALC-startup_projected_dscr", "Projected DSCR (year 2)", "ratio"),),
}
# A calculation the policy computes only inside a rule that did not run, although it does not need the missing
# input: its own reason ({reason} is the policy's reason for the rule).
NOT_EVALUATED_REASONS: dict[str, str] = {
    "CALC-annual_debt_service": "not computed: the reference policy computes annual debt service only inside its "
    "DSCR rule (NBC-4.1), which did not run ({reason}); the debt service itself does not need verified revenue",
}
# Finding rule -> calculations the policy skips when it raises that finding (calc id, label, CalcUnit, reason).
FINDING_MISSING_CALCS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "principal_credit_score_missing": (
        (
            "CALC-min_principal_credit_score",
            "Lowest principal credit score",
            "score",
            "not computed: no credit score was read for any principal (NBC-2.1)",
        ),
    ),
}
RATE_STRESS_CALC = ("CALC-stressed_dscr_rate", "DSCR with the note rate plus 2.00 percentage points", "ratio")
# Insurance: "loss_ratios_and_frequency" and "loss_ratio_<line>" are handled in ledger.py (they depend on the
# lines being quoted).
LOSS_RATIO_CALC = "CALC-loss_ratio_5yr_{line}"
CLAIM_FREQUENCY_CALC = ("CALC-claim_frequency", "Claim frequency checks (counts)", "count")

# Location fields no packet document prints: docintel/to_application_insurance.py writes the agent Location model's
# defaults (wind tier 0, flood zone X, low wildfire hazard, 0 vacant days, no knob-and-tube wiring; no systems
# update year). Every default except the systems year is the permissive one, so a rule can pass on it.
UNDOCUMENTED_LOCATION_FIELDS: tuple[str, ...] = (
    "wind_tier",
    "flood_zone",
    "wildfire_hazard",
    "vacant_days",
    "knob_and_tube_or_fuse_box",
    "systems_updated_year",
)
# Rule -> patterns of the UNKNOWN claims a PASSED rule rests on. A pass that rests on one is shown as not evaluated.
ASSERTION_UNKNOWN_PATTERNS: dict[str, tuple[str, ...]] = {
    "catastrophe_exposure": (r"^APP-locations\.\d+\.(wind_tier|wildfire_hazard)$",),
    "building_eligibility": (r"^APP-locations\.\d+\.knob_and_tube_or_fuse_box$",),
    "class_eligible": (r"^APP-locations\.\d+\.vacant_days$",),
    "documents_complete": (r"^APP-locations\.\d+\.flood_zone$",),  # HM-6.2: flood zones A/V need a certificate
    "principal_credit_scores": (r"^APP-owners\.\d+\.(credit_score|is_control_person)$",),
    "ownership": (r"^APP-owners\.\d+\.is_control_person$",),
    # HM-1.1 / HM-7.1 read the GL limits (an unread limit counts as 0 in the policy, so a pass would rest on it).
    "capacity": (r"^APP-gl_limit_(per_occurrence|aggregate)$",),
    "general_liability": (r"^APP-gl_limit_(per_occurrence|aggregate)$",),
}

# Contradiction check -> the application paths it compares. A resolution that substitutes one of them supersedes the
# contradiction (the documented disagreement is no longer in the application; the policy re-evaluates the values).
CONTRADICTION_APPLICATION_PATHS: dict[str, tuple[str, ...]] = {
    "revenue_vs_deposits": ("stated_annual_revenue", "bank_statements.months"),
    "tax_return_vs_pnl": ("debt.ttm_pnl_revenue",),
    "debt_payment_consistency": (
        "debt.existing_monthly_payment_schedule",
        "debt.existing_monthly_payment_observed",
    ),
    "tiv_consistency": ("stated_tiv", "locations"),
    "claims_history_consistency": ("stated_claim_count", "stated_total_incurred", "loss_runs.current_term_claims"),
}

# Contradiction check -> regular expressions over the claim ids of the values it compares (the same comparisons as
# CONTRADICTION_APPLICATION_PATHS, narrowed to the fields the check reads: the TIV check sums the locations' building,
# contents and business-income values, not their wind tier or year built). A check whose outcome rests on an UNKNOWN
# claim is UNKNOWN too (ledger_facts.check_claims), like a calculation over one.
CHECK_INPUT_PATTERNS: dict[str, tuple[str, ...]] = {
    "revenue_vs_deposits": (
        r"^APP-stated_annual_revenue$",
        r"^APP-bank_statements\.months_provided$",
        STATEMENT_MONTH_PATTERN,
    ),
    "tax_return_vs_pnl": (r"^APP-debt\.ttm_pnl_revenue$",),
    "debt_payment_consistency": (r"^APP-debt\.existing_monthly_payment_(schedule|observed)$",),
    "tiv_consistency": (
        r"^APP-stated_tiv$",
        r"^APP-locations\.count$",
        r"^APP-locations\.\d+\.(building|contents|business_income)_value$",
    ),
    "claims_history_consistency": (
        r"^APP-stated_(claim_count|total_incurred)$",
        r"^APP-loss_runs\.current_term_claims$",
        r"^APP-loss_runs\.years\.",
    ),
}

# Assertion phrases: topic -> (expression, phrase_true, phrase_false, phrase_unknown).
TOPIC_PHRASES: dict[str, tuple[str, str, str, str]] = {
    "product_limits": (
        "amount and term within the product limits",
        "is within the product limits",
        "is outside the product limits",
        "could not be checked against the product limits",
    ),
    "time_in_business": (
        "time in business at or above the minimum",
        "meets the time-in-business minimum",
        "is below the time-in-business minimum",
        "could not be evaluated",
    ),
    "industry": (
        "industry and structure eligible",
        "is eligible",
        "is restricted or not eligible",
        "could not be evaluated",
    ),
    "principals": (
        "principal credit and history requirements met",
        "meet the principal requirements",
        "do not meet the principal requirements",
        "could not be evaluated",
    ),
    "documents": (
        "every required document provided",
        "are complete",
        "are incomplete",
        "could not be checked",
    ),
    "statements": (
        "twelve consecutive statement months provided",
        "are complete",
        "are incomplete",
        "could not be checked",
    ),
    "account_conduct": (
        "account conduct within the limits",
        "is within the account conduct limits",
        "shows an account conduct trigger",
        "could not be evaluated",
    ),
    "verified_revenue": (
        "verified annual revenue at or above the minimum",
        "meets the revenue minimum",
        "is below the revenue minimum",
        "could not be evaluated",
    ),
    "revenue_gap": (
        "revenue gap at or below the benign limit",
        "is within the benign range",
        "is above the benign range",
        "could not be evaluated",
    ),
    "dscr": (
        "decision DSCR at or above the product minimum",
        "meets the product minimum",
        "is below the product minimum",
        "could not be evaluated",
    ),
    "projected_dscr": (
        "projected DSCR at or above the program minimum",
        "meets the program minimum",
        "is below the program minimum",
        "could not be evaluated",
    ),
    "stress": (
        "required stress tests passed",
        "passes the required stress tests",
        "fails a required stress test",
        "could not be evaluated",
    ),
    "stress_revenue": (
        "revenue-stressed DSCR at or above the stress minimum",
        "passes the revenue stress test",
        "fails the revenue stress test",
        "could not be evaluated",
    ),
    "stress_rate": (
        "rate-stressed DSCR at or above the stress minimum",
        "passes the rate stress test",
        "fails the rate stress test",
        "could not be evaluated",
    ),
    "leverage": (
        "leverage at or below the maximum",
        "is within the leverage limit",
        "is above the leverage limit",
        "could not be evaluated",
    ),
    "stacking": (
        "no stacking of new loans or advances",
        "shows no stacking",
        "shows stacking of new loans or advances",
        "could not be evaluated",
    ),
    "collateral": (
        "collateral coverage at or above the minimum",
        "meets the collateral requirement",
        "is below the collateral requirement",
        "could not be evaluated",
    ),
    "ownership": (
        "ownership adds up with one control person",
        "is consistent",
        "has a discrepancy",
        "could not be evaluated",
    ),
    "sanctions": (
        "every party screened with no potential match",
        "shows no potential match",
        "needs Compliance review",
        "could not be completed",
    ),
    "injection": (
        "no instruction-like text in the file",
        "contains no instruction-like text",
        "contains instruction-like text",
        "could not be checked",
    ),
    "model_pd": (
        "model PD not above the referral threshold",
        "is not above the referral threshold",
        "is above the referral threshold",
        "was not evaluated",
    ),
    "document_check": (
        "document check passes across the documents",
        "is consistent across the documents",
        "is inconsistent across the documents",
        "could not be checked",
    ),
    "capacity": (
        "request within Harborline capacity",
        "is within capacity",
        "is above capacity",
        "could not be evaluated",
    ),
    "class": (
        "class eligible",
        "is an eligible class",
        "is a restricted or prohibited class",
        "could not be evaluated",
    ),
    "building": (
        "buildings and operations eligible",
        "are eligible",
        "have an eligibility problem",
        "could not be evaluated",
    ),
    "loss_runs": (
        "usable loss runs for the experience period",
        "are usable",
        "are not usable as provided",
        "could not be evaluated",
    ),
    "reconciliation": (
        "application loss history reconciles to the loss runs",
        "reconciles to the loss runs",
        "does not reconcile to the loss runs",
        "could not be evaluated",
    ),
    "loss_ratio": (
        "loss ratios at or below the limits",
        "are within the loss ratio limits",
        "are above a loss ratio limit",
        "could not be evaluated",
    ),
    "frequency": (
        "claim frequency, large losses and trend within the limits",
        "are within the frequency limits",
        "show a frequency, large-loss or trend trigger",
        "could not be evaluated",
    ),
    "tiv": (
        "application TIV reconciles to the property schedule",
        "reconciles to the property schedule",
        "does not reconcile to the property schedule",
        "could not be evaluated",
    ),
    "catastrophe": (
        "catastrophe exposure within the limits",
        "is within the catastrophe limits",
        "needs catastrophe review",
        "could not be evaluated",
    ),
    "general_liability": (
        "general liability terms within the standard",
        "are within the standard",
        "are outside the standard",
        "could not be evaluated",
    ),
}
DEFAULT_PHRASES: tuple[str, str, str] = ("passes this rule", "does not pass this rule", "could not be evaluated")
RULE_TOPIC: dict[str, str] = {
    "product_limits": "product_limits",
    "outside_product_limits": "product_limits",
    "time_in_business": "time_in_business",
    "time_in_business_near_miss": "time_in_business",
    "time_in_business_short": "time_in_business",
    "industry_eligible": "industry",
    "restricted_industry": "industry",
    "prohibited_industry": "industry",
    "ineligible_business_structure": "industry",
    "principal_credit_scores": "principals",
    "credit_score_near_miss": "principals",
    "credit_score_below_minimum": "principals",
    "principal_credit_score_missing": "principals",
    "bankruptcy_within_7_years": "principals",
    "unresolved_tax_lien": "principals",
    "documents_complete": "documents",
    "missing_documents": "documents",
    "bank_statements_incomplete": "statements",
    "account_conduct": "account_conduct",
    "nsf_events": "account_conduct",
    "negative_balance_months": "account_conduct",
    "low_average_balance": "account_conduct",
    "verified_revenue_minimum": "verified_revenue",
    "revenue_near_miss": "verified_revenue",
    "revenue_below_minimum": "verified_revenue",
    "revenue_gap": "revenue_gap",
    "revenue_gap_explainable": "revenue_gap",
    "revenue_gap_unsupported": "revenue_gap",
    "dscr": "dscr",
    "dscr_below_minimum": "dscr",
    "dscr_below_decline_floor": "dscr",
    "projected_dscr": "projected_dscr",
    "projected_dscr_below_minimum": "projected_dscr",
    "projected_dscr_below_decline_floor": "projected_dscr",
    "stress_tests": "stress",
    "stress_test_revenue": "stress_revenue",
    "stress_test_rate": "stress_rate",
    "leverage": "leverage",
    "leverage_above_maximum": "leverage",
    "stacking": "stacking",
    "collateral": "collateral",
    "collateral_coverage_below_minimum": "collateral",
    "ownership": "ownership",
    "ownership_discrepancy": "ownership",
    "sanctions_screening_clear": "sanctions",
    "sanctions_potential_match": "sanctions",
    "screening_not_completed": "sanctions",
    "no_embedded_instructions": "injection",
    "embedded_instruction": "injection",
    "model_pd": "model_pd",
    "model_pd_above_threshold": "model_pd",
    "risk_model_pd": "model_pd",
    "capacity": "capacity",
    "above_capacity": "capacity",
    "class_eligible": "class",
    "restricted_class": "class",
    "prohibited_class": "class",
    "building_eligibility": "building",
    "eligibility_building_age": "building",
    "eligibility_experience": "building",
    "roof_older_than_20_years": "building",
    "protection_class_9_or_10": "building",
    "knob_and_tube_or_fuse_box": "building",
    "unsprinklered_large_location": "building",
    "loss_run_quality": "loss_runs",
    "loss_runs_out_of_date": "loss_runs",
    "loss_runs_not_carrier_produced": "loss_runs",
    "loss_history_under_3_years": "loss_runs",
    "uncovered_years_unexplained": "loss_runs",
    "earned_premium_missing": "loss_runs",
    "loss_run_integrity": "loss_runs",
    "loss_ratios_and_frequency": "loss_ratio",
    "loss_run_reconciliation": "reconciliation",
    "loss_ratio": "loss_ratio",
    "loss_ratio_above_55": "loss_ratio",
    "loss_ratio_above_70": "loss_ratio",
    "annual_loss_ratio_above_100": "loss_ratio",
    "claim_frequency": "frequency",
    "property_claim_frequency": "frequency",
    "large_loss": "frequency",
    "rising_claim_trend": "frequency",
    "tiv_reconciliation": "tiv",
    "tiv_difference_above_15": "tiv",
    "tiv_difference_correction": "tiv",
    "possible_underinsurance": "tiv",
    "catastrophe_exposure": "catastrophe",
    "wind_tier1_large_location": "catastrophe",
    "wildfire_hazard": "catastrophe",
    "general_liability": "general_liability",
    "gl_limits_above_standard": "general_liability",
    "subcontractor_coi_missing": "general_liability",
    "foreign_products": "general_liability",
}
DOCUMENT_RULE_TOPIC = "document_check"
