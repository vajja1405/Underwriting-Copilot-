"""SYNTHETIC Northwind startup term loan program (corpus/programs/northwind_startup_program.md) as plain code.

A startup has no operating history, so the history-based Northwind rules (time in business, verified revenue,
historical DSCR) can never pass for it. The startup program checks the owners, the equity, the plan and the
evidence behind the plan instead. Each check names the program sections (NSP-x.y) it implements. Thresholds are
copied from the program text; where the program says a Northwind rule applies (NBC-2.2, NBC-2.3, NBC-4.2, NBC-4.5,
NBC-5.1, NBC-6.1), the constant is imported from policy_credit.py so the two documents cannot drift apart in code.

Calculations reuse credit_calculations.py (the NBC-4.1 term-loan payment and the NBC-5.1 advance rates) and are
recorded as CALC-startup_* so they never collide with the Northwind calculation ids. Ratios and percentages follow
the program's rounding convention: they are computed from the dollar amounts in exact decimal arithmetic and rounded
half up (59,800 / 400,000 is exactly 14.95%, which is 15.0%), so an exact tie never depends on binary floating point.

Sanctions screening and the injection check are shared by every line and run in policy.py. The Northwind risk model
is not applicable to startups (NSP-1.1): it is recorded as not evaluated with the reason, never estimated.
The thresholds are illustrative, not an industry standard, and not lending, legal or compliance advice.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

from uwcopilot.agent.applications import MaterialAssumption, StartupCreditApplication, StartupOwner
from uwcopilot.agent.credit_calculations import ADVANCE_RATES, months_between, proposed_annual_debt_service
from uwcopilot.agent.policy import PolicyInputs, RuleContext, money, pct, ratio
from uwcopilot.agent.policy_credit import (
    COLLATERAL_REQUIRED_ABOVE,
    COMMITTEE_COLLATERAL_COVERAGE,
    DSCR_DECLINE_FLOOR,
    DSCR_MINIMUM,
    ELIGIBLE_STRUCTURES,
    MIN_COLLATERAL_COVERAGE,
    OWNERSHIP_TOTAL_TOLERANCE_PCT,
    PRINCIPAL_OWNERSHIP_PCT,
    PROHIBITED_INDUSTRIES,
    RATE_STRESS_ADD_PCT,
    RATE_STRESS_MIN_DSCR,
    RESTRICTED_INDUSTRIES,
    REVENUE_STRESS_MIN_DSCR,
    REVENUE_STRESS_SHARE,
)
from uwcopilot.agent.vocab import CHIEF_CREDIT_OFFICER, CREDIT_COMMITTEE, KYC_OPERATIONS, SENIOR_UNDERWRITER
from uwcopilot.security.normalize import has_visible_text

STARTUP_POLICY_VERSION = (
    "reference-policy-v1 (northwind_startup_program 2026.1 SYNTHETIC, with northwind_business_credit 2026.1)"
)
# NSP-1.2: projected debt service uses the NBC-4.1 formula for a fully amortizing term loan.
DEBT_SERVICE_PRODUCT = "term_loan"
# NSP-8.1: startup term loan limits.
STARTUP_MIN_AMOUNT = 50_000
STARTUP_MAX_AMOUNT = 500_000
STARTUP_MIN_TERM_MONTHS = 60
STARTUP_MAX_TERM_MONTHS = 120
# NSP-1.2: a startup has been open for less than 24 months (or has not opened yet).
STARTUP_MAX_MONTHS_OPEN = 24
# NSP-2.1: owner experience and the near-miss floor.
MIN_EXPERIENCE_MONTHS = 24
NEAR_MISS_EXPERIENCE_MONTHS = 12
# NSP-2.2: principal credit score and the near-miss floor.
MIN_STARTUP_CREDIT_SCORE = 680
NEAR_MISS_STARTUP_CREDIT_SCORE = 660
# NSP-2.3: restaurants are always restricted for startups (NBC-2.3, open less than 36 months); short-term rentals
# are restricted by this program. Every other code keeps its NBC-2.2 / NBC-2.3 category.
RESTAURANT_INDUSTRIES = frozenset({"restaurant", "restaurant_cafe"})
STARTUP_RESTRICTED_INDUSTRIES = frozenset({"short_term_rental"})
# NSP-2.3: when the business type and the industry code fall in different categories, the stricter one applies.
CATEGORY_STRICTNESS: dict[str, int] = {
    "eligible": 0,
    "restricted": 1,
    "restricted_startup": 1,
    "restricted_restaurant": 1,
    "prohibited": 2,
}
# NSP-1.3, NSP-2.1, NSP-3.1, NSP-6.1: a flag in the application counts only when its document is listed in
# documents_provided.
EQUITY_STATEMENTS_DOCUMENT = "equity_bank_statements"
EXPERIENCE_EVIDENCE_DOCUMENT = "owner_resumes_and_references"
SUPPORTING_DOCUMENTS: tuple[str, ...] = (
    "lease_or_letter_of_intent",
    "contractor_or_equipment_quotes",
    "comparable_data_with_source",
)
# NSP-9.1 / NBC-7.2: findings that can only be approved through a policy exception. A Senior Underwriter may
# approve at most one exception; two or more go to a higher authority.
EXCEPTION_RULES = frozenset(
    {"experience_near_miss", "credit_score_near_miss", "equity_injection_near_miss", "projected_dscr_below_minimum"}
)
# NSP-3.1: equity injection (percent of total project cost) and the near-miss floor.
MIN_EQUITY_INJECTION_PCT = 20.0
NEAR_MISS_EQUITY_INJECTION_PCT = 15.0
# NSP-3.2: sources must equal uses, and the loan source the requested amount, within this many dollars.
SOURCES_USES_TOLERANCE_USD = 1.00
# NSP-4.1: the NBC-4.2 term loan minimum and decline floor, on the year-2 base case.
STARTUP_DSCR_MINIMUM = DSCR_MINIMUM["term_loan"]
STARTUP_DSCR_DECLINE_FLOOR = DSCR_DECLINE_FLOOR
PROJECTED_DSCR_YEAR = 2
# NSP-5.1: months of fixed costs plus debt service held as working-capital reserve at closing.
RESERVE_MONTHS_REQUIRED = 3
# NSP-6.1: how many top drivers must be backed by a supporting document.
TOP_DRIVER_COUNT = 3
# NSP-7.2: owners at or above this share must guarantee the loan.
GUARANTEE_OWNERSHIP_PCT = 20.0
# NSP-1.3: documents every startup file needs (collateral documents are added when collateral applies).
STARTUP_BASE_DOCUMENTS: tuple[str, ...] = (
    "signed_application",
    "business_plan",
    "financial_projections",
    "sources_and_uses_statement",
    "personal_financial_statements",
    "principal_photo_ids",
    "formation_documents",
    "beneficial_ownership_certification",
)
RISK_MODEL_NOT_APPLICABLE = (
    "not applicable: the Northwind credit risk model was not developed on startup loans (NSP-1.1), and the only "
    "credit model in this repository (uwcopilot.risk.credit) was trained on Lending Club loan outcomes, not on "
    "startup business loans; no probability of default is estimated, so model risk is UNKNOWN and score_risk is "
    "not called"
)
NOT_EVALUATED: tuple[tuple[str, list[str], str], ...] = (
    ("risk_model_pd", ["NSP-1.1", "NBC-8.1"], RISK_MODEL_NOT_APPLICABLE),
    (
        "existing_business_debt_and_stacking",
        ["NSP-1.1", "NBC-4.4"],
        "not implemented: the application has no field for business debt that exists before this loan or for loans "
        "taken in the last 90 days; only the loans and seller notes listed in the sources of funds are counted "
        "(NSP-1.2)",
    ),
    (
        "applicant_bankruptcy_and_tax_liens",
        ["NSP-2.2"],
        "not implemented: the application records bankruptcies and tax liens for principals only, not for the "
        "applicant entity",
    ),
    (
        "identity_verification",
        ["NBC-6.2"],
        "not implemented: needs ID documents and credit reports (document extraction, Phase 2)",
    ),
    (
        "ownership_matches_formation_documents",
        ["NBC-6.1"],
        "not implemented: needs the formation documents; only the application's own ownership total is checked",
    ),
    ("adverse_media_and_pep", ["NBC-6.4"], "not implemented: no adverse-media or PEP input in the application"),
    ("document_integrity", ["NBC-9.1"], "not implemented: needs the founder package documents and their metadata"),
    (
        "compensating_factors",
        ["NBC-4.3"],
        "not implemented: an exception decision for the human approver, not a policy calculation",
    ),
    (
        "approval_authority",
        ["NSP-9.1", "NBC-7.2"],
        "not implemented: checked at the human approval checkpoint, not by the policy",
    ),
)


# --------------------------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------------------------


def startup_principals(app: StartupCreditApplication) -> list[StartupOwner]:
    """NBC-1.2 (kept by NSP-1.2): individuals owning 20% or more, or the control person."""
    return [
        owner
        for owner in app.owners
        if owner.entity_type == "person" and (owner.ownership_pct >= PRINCIPAL_OWNERSHIP_PCT or owner.is_control_person)
    ]


def experience_months(owner: StartupOwner) -> int:
    """Relevant experience in whole months (NSP-2.1): 1.99 years is 23 months, never rounded up to 24."""
    return math.floor(round(owner.years_relevant_experience * 12, 6))


def months_open(app: StartupCreditApplication) -> int:
    """Whole months the business has been open on the submission date (0 when it has not opened yet)."""
    opening = app.applicant.planned_opening_date
    if opening >= app.submitted_date:
        return 0
    return months_between(opening, app.submitted_date)


def industry_category(industry: str) -> str:
    """NSP-2.3 category: prohibited, restricted_restaurant, restricted_startup, restricted or eligible."""
    if industry in PROHIBITED_INDUSTRIES:
        return "prohibited"
    if industry in RESTAURANT_INDUSTRIES:
        return "restricted_restaurant"
    if industry in STARTUP_RESTRICTED_INDUSTRIES:
        return "restricted_startup"
    if industry in RESTRICTED_INDUSTRIES:
        return "restricted"
    return "eligible"


def strictest_industry(app: StartupCreditApplication) -> tuple[str, str]:
    """NSP-2.3: (code, category) of the stricter of the industry code and the business type's own code.

    A restaurant cannot become eligible by using another business type's industry code: the business type still
    counts. On a tie the industry code is reported, because it is the more specific of the two.
    """
    codes = [app.applicant.industry, app.applicant.business_type]
    code = max(codes, key=lambda item: CATEGORY_STRICTNESS[industry_category(item)])
    return code, industry_category(code)


# --------------------------------------------------------------------------------------------------------------
# Program arithmetic: exact decimals, rounded half up (program conventions)
# --------------------------------------------------------------------------------------------------------------


def to_decimal(value: float) -> Decimal:
    """A number from the application as the decimal it was written as (repr gives the shortest form: 0.1 -> 0.1)."""
    return Decimal(repr(value))


def half_up(value: Decimal, places: int) -> float:
    """Round to `places` decimals; an exact half rounds up (14.95 -> 15.0, 1.095 -> 1.10)."""
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def program_ratio(numerator: float, denominator: float) -> float | None:
    """numerator / denominator rounded half up to two decimals (DSCR, coverage); None when denominator <= 0."""
    if denominator <= 0:
        return None
    return half_up(to_decimal(numerator) / to_decimal(denominator), 2)


def program_pct(part: float, whole: float) -> float:
    """part / whole as a percentage, rounded half up to one decimal (equity injection)."""
    return half_up(to_decimal(part) * 100 / to_decimal(whole), 1)


def startup_loan_annual_debt_service(app: StartupCreditApplication, rate_add_pct: float = 0.0) -> float:
    """12 fully amortizing payments of the requested startup loan (optionally at a stressed rate), to the cent."""
    loan = app.loan_request
    service = proposed_annual_debt_service(
        DEBT_SERVICE_PRODUCT, loan.amount, loan.annual_rate_pct + rate_add_pct, loan.term_months
    )
    return round(service, 2)


def other_debt_annual_service(app: StartupCreditApplication) -> float:
    """12 payments of every other loan and seller note listed in the sources of funds (NSP-1.2, NSP-3.2)."""
    monthly = sum((to_decimal(item.monthly_payment) for item in app.sources_and_uses.sources.other_debt), Decimal(0))
    return half_up(monthly * 12, 2)


def projected_annual_debt_service(app: StartupCreditApplication, rate_add_pct: float = 0.0) -> float:
    """NSP-1.2: the startup loan's annual debt service plus that of the other loans and seller notes, to the cent.

    The rate stress (NSP-4.2) raises only the startup loan's note rate; other debt keeps its stated payment.
    """
    loan_service = to_decimal(startup_loan_annual_debt_service(app, rate_add_pct))
    return half_up(loan_service + to_decimal(other_debt_annual_service(app)), 2)


def revenue_stressed_noi(noi: float, revenue: float) -> float:
    """NSP-4.2: NOI after removing REVENUE_STRESS_SHARE of revenue dollar for dollar, to the cent."""
    return half_up(to_decimal(noi) - to_decimal(REVENUE_STRESS_SHARE) * to_decimal(revenue), 2)


def startup_collateral_coverage(app: StartupCreditApplication) -> float:
    """NSP-7.1 (NBC-5.1): sum of value x maximum advance rate, divided by the loan amount, rounded half up."""
    lendable = sum(
        (to_decimal(item.value) * to_decimal(ADVANCE_RATES[item.collateral_type]) for item in app.collateral),
        Decimal(0),
    )
    return half_up(lendable / to_decimal(app.loan_request.amount), 2)


# --------------------------------------------------------------------------------------------------------------
# Documents behind the plan
# --------------------------------------------------------------------------------------------------------------


def document_listed(app: StartupCreditApplication, document: str) -> bool:
    """True when the document is in the application's documents_provided list."""
    return document in app.documents_provided


def supporting_document_listed(app: StartupCreditApplication) -> bool:
    """NSP-1.2: a quote, a lease or letter of intent, or comparable data with its source is in the file."""
    return any(document_listed(app, document) for document in SUPPORTING_DOCUMENTS)


def required_driver_ranks(app: StartupCreditApplication) -> list[int]:
    """NSP-6.1: ranks 1, 2 and 3 (every rank from 1 when the plan lists fewer than three assumptions)."""
    return list(range(1, min(TOP_DRIVER_COUNT, len(app.material_assumptions)) + 1))


def missing_driver_ranks(app: StartupCreditApplication) -> list[int]:
    """The required top-driver ranks that no material assumption carries."""
    present = {item.tornado_rank for item in app.material_assumptions}
    return [rank for rank in required_driver_ranks(app) if rank not in present]


def top_drivers(app: StartupCreditApplication) -> list[MaterialAssumption]:
    """NSP-6.1: the material assumptions ranked 1, 2 and 3 (the required ranks), in rank order."""
    required = set(required_driver_ranks(app))
    drivers = [item for item in app.material_assumptions if item.tornado_rank in required]
    return sorted(drivers, key=lambda item: item.tornado_rank or 0)


def is_backed(assumption: MaterialAssumption, app: StartupCreditApplication) -> bool:
    """NSP-6.1: VERIFIED, or marked as backed with a supporting document listed in documents_provided."""
    if assumption.status == "VERIFIED":
        return True
    return assumption.supporting_document_provided and supporting_document_listed(app)


def required_startup_documents(app: StartupCreditApplication) -> list[str]:
    """NSP-1.3: the base documents, plus collateral documents when collateral applies (NBC-5.1)."""
    required = list(STARTUP_BASE_DOCUMENTS)
    if app.collateral or app.loan_request.amount > COLLATERAL_REQUIRED_ABOVE:
        required.append("collateral_documents")
    return required


# --------------------------------------------------------------------------------------------------------------
# Eligibility: product, startup status, entity, industry, owners, documents
# --------------------------------------------------------------------------------------------------------------


def check_startup_product_limits(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-8.1: amount and term within the startup term loan limits, otherwise refer to the Chief Credit Officer."""
    loan = app.loan_request
    evidence = ["APP-requested_amount", "APP-term_months", "APP-product"]
    problems = []
    if loan.amount < STARTUP_MIN_AMOUNT:
        problems.append(f"requested amount {money(loan.amount)} is below the minimum {money(STARTUP_MIN_AMOUNT)}")
    if loan.amount > STARTUP_MAX_AMOUNT:
        problems.append(f"requested amount {money(loan.amount)} is above the maximum {money(STARTUP_MAX_AMOUNT)}")
    if not STARTUP_MIN_TERM_MONTHS <= loan.term_months <= STARTUP_MAX_TERM_MONTHS:
        problems.append(
            f"term of {loan.term_months} months is outside {STARTUP_MIN_TERM_MONTHS} to {STARTUP_MAX_TERM_MONTHS} "
            "months"
        )
    if problems:
        context.find(
            "outside_product_limits",
            "refer",
            ["NSP-8.1", "NSP-9.2"],
            [CHIEF_CREDIT_OFFICER],
            "Request is outside the startup term loan limits: " + "; ".join(problems) + ".",
            evidence,
            trigger_ids=["R-NSP-10"],
        )
    else:
        context.passed(
            "product_limits",
            ["NSP-8.1"],
            f"Requested startup term loan of {money(loan.amount)} over {loan.term_months} months is within the "
            f"program limits ({money(STARTUP_MIN_AMOUNT)} to {money(STARTUP_MAX_AMOUNT)}, {STARTUP_MIN_TERM_MONTHS} "
            f"to {STARTUP_MAX_TERM_MONTHS} months).",
            evidence,
        )


def check_startup_status(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-1.2: open for less than 24 months, or not open yet; otherwise the Northwind guidelines apply."""
    months = months_open(app)
    calc_id = context.calc(
        "startup_months_open",
        "Months open on the submission date (0 = not open yet)",
        months,
        "months",
        ["NSP-1.2"],
        planned_opening_date=app.applicant.planned_opening_date.isoformat(),
        submitted_date=app.submitted_date.isoformat(),
    )
    if months >= STARTUP_MAX_MONTHS_OPEN:
        context.find(
            "not_a_startup",
            "refer",
            ["NSP-1.2", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"The business has been open for {months} months (24 or more), so it is not a startup; it must be "
            "underwritten under the Northwind guidelines.",
            [calc_id, "APP-planned_opening_date"],
            trigger_ids=["R-NSP-01"],
        )
        return
    if app.applicant.planned_opening_date >= app.submitted_date:
        state = "has not opened yet"
    else:
        state = f"has been open for {months} months"
    context.passed(
        "startup_status",
        ["NSP-1.2"],
        f"The business {state} (less than 24 months), so it qualifies as a startup.",
        [calc_id],
    )


def check_startup_entity(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-2.4: a corporation, LLC or partnership (formed before closing); a sole proprietorship must form one."""
    entity = app.applicant.entity_type
    if entity in ELIGIBLE_STRUCTURES:
        formed = "already formed" if app.applicant.formation_date is not None else "to be formed before closing"
        context.passed(
            "entity_type",
            ["NSP-2.4"],
            f"Entity type {entity} is eligible ({formed}).",
            ["APP-entity_type", "APP-formation_date"],
        )
        return
    context.find(
        "ineligible_entity_type",
        "request_more_information",
        ["NSP-2.4"],
        [SENIOR_UNDERWRITER],
        f"Entity type {entity} is not a corporation, LLC or partnership; an eligible entity must be formed before "
        "closing.",
        ["APP-entity_type"],
    )


def check_startup_industry(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-2.3: prohibited industries (decline-for-review) and restricted industries (refer, R-NSP-02).

    The stricter category of the industry code and the business type applies, so neither field can hide the other.
    """
    industry, category = strictest_industry(app)
    evidence = ["APP-industry"] if industry == app.applicant.industry else ["APP-industry", "APP-business_type"]
    if category == "prohibited":
        context.find(
            "prohibited_industry",
            "decline_for_review",
            ["NSP-2.3", "NBC-2.2", "NBC-7.1"],
            [SENIOR_UNDERWRITER],
            f"Industry code {industry} is a prohibited industry; no exception may be granted.",
            evidence,
        )
        return
    if category == "eligible":
        context.passed(
            "industry_eligible",
            ["NSP-2.3"],
            f"Industry code {industry} (business type {app.applicant.business_type}) is neither prohibited nor "
            "restricted for startups.",
            ["APP-industry", "APP-business_type"],
        )
        return
    reasons = {
        "restricted_restaurant": ("restaurants, cafes and bars are restricted while open less than 36 months", True),
        "restricted_startup": ("short-term rentals are restricted under the startup program", False),
        "restricted": ("it is a restricted industry", True),
    }
    reason, northwind_rule = reasons[category]
    sections = ["NSP-2.3", "NBC-2.3", "NSP-9.2"] if northwind_rule else ["NSP-2.3", "NSP-9.2"]
    context.find(
        "restricted_industry",
        "refer",
        sections,
        [SENIOR_UNDERWRITER],
        f"Industry code {industry} needs Senior Underwriter review: {reason}.",
        evidence,
        trigger_ids=["R-NSP-02"],
    )


def check_owner_experience(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-2.1: one principal with 24+ months of relevant experience, evidenced by a resume and references."""
    people = startup_principals(app)
    if not people:
        context.find(
            "no_principal",
            "request_more_information",
            ["NSP-2.1", "NSP-1.2"],
            [SENIOR_UNDERWRITER],
            "The application names no individual principal, so owner experience and credit cannot be checked.",
            ["APP-owners"],
        )
        return
    best = max(people, key=experience_months)
    months = experience_months(best)
    calc_id = context.calc(
        "startup_max_principal_experience_months",
        "Most relevant experience of any principal (whole months)",
        months,
        "months",
        ["NSP-2.1"],
        owner_id=best.owner_id,
        years_relevant_experience=best.years_relevant_experience,
    )
    if months >= MIN_EXPERIENCE_MONTHS:
        qualified = [owner for owner in people if experience_months(owner) >= MIN_EXPERIENCE_MONTHS]
        resumes_listed = document_listed(app, EXPERIENCE_EVIDENCE_DOCUMENT)
        flagged = [owner.owner_id for owner in qualified if owner.experience_evidence_provided]
        evidence = [calc_id, "APP-owners", "APP-documents_provided"]
        if flagged and resumes_listed:
            context.passed(
                "owner_experience",
                ["NSP-2.1"],
                f"Principal {flagged[0]} has at least 24 months of relevant experience, evidenced by a resume and "
                f"references in the file; the most experience of any principal is {months} months.",
                evidence,
            )
        else:
            reason = (
                "the documents provided list no owner resumes and references"
                if flagged
                else "no resume and references are in the file"
            )
            context.find(
                "experience_evidence_missing",
                "request_more_information",
                ["NSP-2.1"],
                [SENIOR_UNDERWRITER],
                f"Principal(s) {', '.join(owner.owner_id for owner in qualified)} claim at least 24 months of relevant "
                f"experience, but {reason}.",
                evidence,
            )
    elif months >= NEAR_MISS_EXPERIENCE_MONTHS:
        context.find(
            "experience_near_miss",
            "refer",
            ["NSP-2.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"The most relevant experience of any principal is {months} months: below 24 but at least 12, so only an "
            "exception can approve it.",
            [calc_id],
            trigger_ids=["R-NSP-03"],
        )
    else:
        context.find(
            "experience_below_minimum",
            "decline_for_review",
            ["NSP-2.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"The most relevant experience of any principal is {months} months, below the 12-month exception floor.",
            [calc_id],
            trigger_ids=["R-NSP-03"],
        )


def check_startup_principals(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-2.2: principal credit scores, bankruptcies and unresolved tax liens."""
    people = startup_principals(app)
    missing = [owner.owner_id for owner in people if owner.credit_score is None]
    if missing:
        context.find(
            "principal_credit_score_missing",
            "request_more_information",
            ["NSP-2.2"],
            [SENIOR_UNDERWRITER],
            f"No credit score for principal(s) {', '.join(missing)}.",
            ["APP-owners"],
        )
    scores = [owner.credit_score for owner in people if owner.credit_score is not None]
    if scores:
        lowest = min(scores)
        calc_id = context.calc(
            "startup_min_principal_credit_score", "Lowest principal credit score", lowest, "score", ["NSP-2.2"]
        )
        if lowest >= MIN_STARTUP_CREDIT_SCORE:
            context.passed(
                "principal_credit_scores",
                ["NSP-2.2"],
                f"Lowest principal credit score is {lowest} (at least 680).",
                [calc_id],
            )
        elif lowest >= NEAR_MISS_STARTUP_CREDIT_SCORE:
            context.find(
                "credit_score_near_miss",
                "refer",
                ["NSP-2.2", "NSP-9.2"],
                [SENIOR_UNDERWRITER],
                f"Lowest principal credit score is {lowest}: below 680 but at least 660 (exception only).",
                [calc_id],
                trigger_ids=["R-NSP-04"],
            )
        else:
            context.find(
                "credit_score_below_minimum",
                "decline_for_review",
                ["NSP-2.2", "NSP-9.2"],
                [SENIOR_UNDERWRITER],
                f"Lowest principal credit score is {lowest}, below the 660 exception floor.",
                [calc_id],
                trigger_ids=["R-NSP-04"],
            )
    bankrupt = [owner.owner_id for owner in people if owner.bankruptcy_within_7_years]
    liens = [owner.owner_id for owner in people if owner.tax_lien_unresolved]
    if bankrupt:
        context.find(
            "bankruptcy_within_7_years",
            "decline_for_review",
            ["NSP-2.2", "NBC-7.4"],
            [SENIOR_UNDERWRITER],
            f"Principal(s) {', '.join(bankrupt)} had a bankruptcy within the last 7 years; no exception is possible.",
            ["APP-owners"],
        )
    if liens:
        context.find(
            "unresolved_tax_lien",
            "decline_for_review",
            ["NSP-2.2", "NBC-7.4"],
            [SENIOR_UNDERWRITER],
            f"Principal(s) {', '.join(liens)} have a tax lien that is not on an approved repayment plan; no exception "
            "is possible.",
            ["APP-owners"],
        )
    if people and not bankrupt and not liens:
        context.passed(
            "principal_history",
            ["NSP-2.2"],
            "No principal had a bankruptcy within the last 7 years or has an unresolved tax lien.",
            ["APP-owners"],
        )


def check_startup_documents(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-1.3: a missing required document puts the file in request-more-information status."""
    missing = [name for name in required_startup_documents(app) if name not in app.documents_provided]
    if missing:
        context.find(
            "missing_documents",
            "request_more_information",
            ["NSP-1.3"],
            [SENIOR_UNDERWRITER],
            f"Required documents are missing: {', '.join(missing)}.",
            ["APP-documents_provided"],
        )
    else:
        context.passed(
            "documents_complete", ["NSP-1.3"], "Every required document is in the file.", ["APP-documents_provided"]
        )


# --------------------------------------------------------------------------------------------------------------
# Equity, sources and uses
# --------------------------------------------------------------------------------------------------------------


def check_sources_and_uses(app: StartupCreditApplication, context: RuleContext) -> float:
    """NSP-3.2: sources equal uses and the loan source equals the request. Returns the total project cost."""
    uses = app.sources_and_uses.uses
    sources = app.sources_and_uses.sources
    total_cost = uses.total()
    cost_id = context.calc(
        "startup_total_project_cost",
        "Total project cost (sum of uses)",
        total_cost,
        "usd",
        ["NSP-1.2", "NSP-3.2"],
        **uses.model_dump(),
    )
    total_sources = sources.total()
    sources_id = context.calc(
        "startup_total_sources",
        "Total sources of funds",
        total_sources,
        "usd",
        ["NSP-3.2"],
        loan=sources.loan,
        owner_cash_equity=sources.owner_cash_equity,
        other_debt=sources.other_debt_total(),
        other=sources.other,
    )
    amount = app.loan_request.amount
    problems = []
    if abs(total_sources - total_cost) > SOURCES_USES_TOLERANCE_USD:
        problems.append(f"total sources {money(total_sources)} differ from total uses {money(total_cost)}")
    if abs(sources.loan - amount) > SOURCES_USES_TOLERANCE_USD:
        problems.append(f"the loan in the sources ({money(sources.loan)}) differs from the requested {money(amount)}")
    if problems:
        context.find(
            "sources_and_uses_inconsistent",
            "request_more_information",
            ["NSP-3.2"],
            [SENIOR_UNDERWRITER],
            "The sources and uses statement does not reconcile: " + "; ".join(problems) + ".",
            [cost_id, sources_id, "APP-requested_amount"],
        )
    else:
        context.passed(
            "sources_and_uses",
            ["NSP-3.2"],
            f"Total sources equal total uses ({money(total_cost)}), and the loan in the sources equals the requested "
            "amount.",
            [cost_id, sources_id],
        )
    return total_cost


def check_equity_injection(app: StartupCreditApplication, total_cost: float, context: RuleContext) -> None:
    """NSP-3.1: equity injection of at least 20.0% of total project cost, verified by bank statements."""
    equity = app.sources_and_uses.sources.owner_cash_equity
    share = program_pct(equity, total_cost)
    equity_id = context.calc(
        "startup_equity_injection_pct",
        "Equity injection (owner cash equity / total project cost, percent)",
        share,
        "pct",
        ["NSP-3.1"],
        owner_cash_equity=equity,
        total_project_cost=total_cost,
    )
    if share >= MIN_EQUITY_INJECTION_PCT:
        context.passed(
            "equity_injection",
            ["NSP-3.1"],
            f"Equity injection is {pct(share)} of total project cost (at least 20.0%).",
            [equity_id],
        )
    elif share >= NEAR_MISS_EQUITY_INJECTION_PCT:
        context.find(
            "equity_injection_near_miss",
            "refer",
            ["NSP-3.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Equity injection is {pct(share)}: below 20.0% but at least 15.0% (exception only).",
            [equity_id],
            trigger_ids=["R-NSP-05"],
        )
    else:
        context.find(
            "equity_injection_below_minimum",
            "decline_for_review",
            ["NSP-3.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Equity injection is {pct(share)}, below the 15.0% exception floor.",
            [equity_id],
            trigger_ids=["R-NSP-05"],
        )
    marked_verified = app.sources_and_uses.equity_verified_by_statements
    statements_listed = document_listed(app, EQUITY_STATEMENTS_DOCUMENT)
    if marked_verified and statements_listed:
        context.passed(
            "equity_verified",
            ["NSP-3.1"],
            "Bank statements in the file verify the owner cash equity.",
            ["APP-sources_and_uses", "APP-documents_provided"],
        )
    else:
        reason = (
            "is marked as verified, but the documents provided list no equity bank statements"
            if marked_verified
            else "is not verified by bank statements yet"
        )
        context.find(
            "equity_not_verified",
            "request_more_information",
            ["NSP-3.1"],
            [SENIOR_UNDERWRITER],
            f"Owner cash equity of {money(equity)} {reason}; request them.",
            [equity_id, "APP-sources_and_uses", "APP-documents_provided"],
        )


# --------------------------------------------------------------------------------------------------------------
# The plan: projected DSCR, stress, assumptions, simulation, reserve, top drivers
# --------------------------------------------------------------------------------------------------------------


def check_projected_dscr(app: StartupCreditApplication, context: RuleContext) -> float:
    """NSP-4.1: year-2 base-case NOI / projected annual debt service. Returns the annual debt service."""
    loan = app.loan_request
    service = projected_annual_debt_service(app)
    service_id = context.calc(
        "startup_annual_debt_service",
        "Projected annual debt service (startup term loan plus other loans and seller notes)",
        service,
        "usd",
        ["NSP-1.2", "NSP-4.1"],
        requested_amount=loan.amount,
        note_rate_pct=loan.annual_rate_pct,
        term_months=loan.term_months,
        startup_loan_annual_debt_service=startup_loan_annual_debt_service(app),
        other_debt_annual_debt_service=other_debt_annual_service(app),
    )
    year = app.projections.year(PROJECTED_DSCR_YEAR)
    dscr = program_ratio(year.noi_p50, service)
    dscr_id = context.calc(
        "startup_projected_dscr",
        "Projected DSCR (year 2 P50 NOI / projected annual debt service)",
        dscr,
        "ratio",
        ["NSP-4.1"],
        noi_year2_p50=year.noi_p50,
        annual_debt_service=service,
    )
    evidence = [dscr_id, service_id, "APP-projections"]
    if dscr is None:
        context.skip("projected_dscr", ["NSP-4.1"], "projected annual debt service is zero")
    elif dscr >= STARTUP_DSCR_MINIMUM:
        context.passed(
            "projected_dscr",
            ["NSP-4.1"],
            f"Projected DSCR is {ratio(dscr)} (year 2 P50 NOI {money(year.noi_p50)} / annual debt service "
            f"{money(service)}), at or above the program minimum {ratio(STARTUP_DSCR_MINIMUM)}.",
            evidence,
        )
    elif dscr >= STARTUP_DSCR_DECLINE_FLOOR:
        context.find(
            "projected_dscr_below_minimum",
            "refer",
            ["NSP-4.1", "NBC-4.3", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Projected DSCR is {ratio(dscr)}, below the program minimum {ratio(STARTUP_DSCR_MINIMUM)} but at least "
            f"{ratio(STARTUP_DSCR_DECLINE_FLOOR)}; approval needs two compensating factors and an exception.",
            evidence,
            trigger_ids=["R-NSP-06"],
        )
    else:
        context.find(
            "projected_dscr_below_decline_floor",
            "decline_for_review",
            ["NSP-4.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER, CREDIT_COMMITTEE],
            f"Projected DSCR is {ratio(dscr)}, below {ratio(STARTUP_DSCR_DECLINE_FLOOR)}: does not meet the program "
            "unless the Credit Committee grants an exception.",
            evidence,
            trigger_ids=["R-NSP-06"],
        )
    return service


def check_startup_stress(app: StartupCreditApplication, service: float, context: RuleContext) -> None:
    """NSP-4.2: the NBC-4.5 revenue stress on every startup loan, and the rate stress on variable-rate loans."""
    year = app.projections.year(PROJECTED_DSCR_YEAR)
    stressed_noi = revenue_stressed_noi(year.noi_p50, year.revenue_p50)
    stressed = program_ratio(stressed_noi, service)
    stress_id = context.calc(
        "startup_stressed_dscr_revenue",
        "Projected DSCR after a 20% revenue stress (year 2 P50)",
        stressed,
        "ratio",
        ["NSP-4.2"],
        stressed_noi=stressed_noi,
        revenue_year2_p50=year.revenue_p50,
        annual_debt_service=service,
    )
    if stressed is not None and stressed < REVENUE_STRESS_MIN_DSCR:
        context.find(
            "stress_test_revenue",
            "refer",
            ["NSP-4.2", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Revenue-stressed projected DSCR is {ratio(stressed)}, below {ratio(REVENUE_STRESS_MIN_DSCR)}.",
            [stress_id],
            trigger_ids=["R-NSP-07"],
        )
    elif stressed is not None:
        context.passed(
            "stress_test_revenue",
            ["NSP-4.2"],
            f"Revenue-stressed projected DSCR is {ratio(stressed)} (at least {ratio(REVENUE_STRESS_MIN_DSCR)}).",
            [stress_id],
        )
    if app.loan_request.rate_type != "variable":
        context.passed(
            "stress_test_rate",
            ["NSP-4.2"],
            "No rate stress is required: the note rate is fixed.",
            ["APP-note_rate_pct"],
        )
        return
    stressed_rate = app.loan_request.annual_rate_pct + RATE_STRESS_ADD_PCT
    rate_service = projected_annual_debt_service(app, RATE_STRESS_ADD_PCT)
    rate_dscr = program_ratio(year.noi_p50, rate_service)
    rate_id = context.calc(
        "startup_stressed_dscr_rate",
        "Projected DSCR with the note rate plus 2.00 percentage points",
        rate_dscr,
        "ratio",
        ["NSP-4.2"],
        stressed_rate_pct=round(stressed_rate, 2),
        annual_debt_service_stressed=rate_service,
        noi_year2_p50=year.noi_p50,
    )
    if rate_dscr is not None and rate_dscr < RATE_STRESS_MIN_DSCR:
        context.find(
            "stress_test_rate",
            "refer",
            ["NSP-4.2", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Rate-stressed projected DSCR is {ratio(rate_dscr)}, below {ratio(RATE_STRESS_MIN_DSCR)}.",
            [rate_id],
            trigger_ids=["R-NSP-07"],
        )
    elif rate_dscr is not None:
        context.passed(
            "stress_test_rate",
            ["NSP-4.2"],
            f"Rate-stressed projected DSCR is {ratio(rate_dscr)} (at least {ratio(RATE_STRESS_MIN_DSCR)}).",
            [rate_id],
        )


def check_projection_assumptions(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-4.3: the projection lists its material assumptions, each with a stated source.

    The source is free text the applicant writes. The policy reads one bit of it: whether it holds any visible
    character (security.normalize.has_visible_text). A source made only of spaces or invisible characters such as a
    zero-width space is not stated. The words themselves are never read.
    """
    assumptions = app.material_assumptions
    if not assumptions:
        context.find(
            "projection_assumptions_incomplete",
            "request_more_information",
            ["NSP-4.3"],
            [SENIOR_UNDERWRITER],
            "The projection lists no material assumptions.",
            ["APP-material_assumptions"],
        )
        return
    unsourced = [item.name for item in assumptions if not has_visible_text(item.source)]
    if unsourced:
        context.find(
            "projection_assumptions_incomplete",
            "request_more_information",
            ["NSP-4.3"],
            [SENIOR_UNDERWRITER],
            f"Material assumption(s) without a stated source: {', '.join(unsourced)}.",
            ["APP-material_assumptions"],
        )
    else:
        context.passed(
            "projection_assumptions",
            ["NSP-4.3"],
            f"The projection lists {len(assumptions)} material assumptions, each with its status and a stated source.",
            ["APP-material_assumptions"],
        )


def record_simulation_outputs(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-4.4: record the simulated probabilities as information only. They never change the recommendation."""
    simulation = app.projections.simulation
    if simulation is None:
        context.skip("simulation_outputs", ["NSP-4.4"], "information only: the plan includes no simulation outputs")
        return
    provenance: dict[str, float | int | str | None] = {
        "simulation_label": simulation.label,
        "simulator_version": simulation.simulator_version,
        "n_paths": simulation.n_paths,
        "seed": simulation.seed,
        "dscr_target": simulation.dscr_target,
    }
    context.calc(
        "startup_p_dscr_ge_target_year2",
        "Simulated probability that year 2 DSCR is at least the target (information only)",
        simulation.p_dscr_ge_target_year2,
        "probability",
        ["NSP-4.4"],
        **provenance,
    )
    context.calc(
        "startup_p_cash_negative_24m",
        "Simulated probability that cash falls below zero within 24 months (information only)",
        simulation.p_cash_negative_24m,
        "probability",
        ["NSP-4.4"],
        **provenance,
    )


def check_working_capital_reserve(app: StartupCreditApplication, service: float, context: RuleContext) -> None:
    """NSP-5.1: reserve of at least 3 months of fixed costs plus debt service at closing."""
    fixed = app.projections.monthly_fixed_costs
    reserve = app.sources_and_uses.uses.working_capital_reserve
    monthly_need = to_decimal(fixed) + to_decimal(service) / 12
    # 3 x (fixed + service / 12), multiplied out first so the cents are exact (service x 3 / 12 = service / 4).
    required = half_up(
        RESERVE_MONTHS_REQUIRED * to_decimal(fixed) + to_decimal(service) * RESERVE_MONTHS_REQUIRED / 12, 2
    )
    required_id = context.calc(
        "startup_required_reserve",
        "Required working-capital reserve (3 months of fixed costs plus debt service)",
        required,
        "usd",
        ["NSP-5.1"],
        monthly_fixed_costs=fixed,
        annual_debt_service=service,
        working_capital_reserve=reserve,
    )
    months_covered = half_up(to_decimal(reserve) / monthly_need, 1)
    months_id = context.calc(
        "startup_reserve_months",
        "Working-capital reserve in months of fixed costs plus debt service",
        months_covered,
        "months",
        ["NSP-5.1"],
        working_capital_reserve=reserve,
        monthly_fixed_costs_plus_debt_service=half_up(monthly_need, 2),
    )
    if reserve >= required:
        context.passed(
            "working_capital_reserve",
            ["NSP-5.1"],
            f"Working-capital reserve {money(reserve)} is at least the required {money(required)} (3 months of "
            "fixed costs plus debt service).",
            [required_id, months_id],
        )
    else:
        context.find(
            "working_capital_reserve_short",
            "refer",
            ["NSP-5.1", "NSP-9.2"],
            [SENIOR_UNDERWRITER],
            f"Working-capital reserve {money(reserve)} is below the required {money(required)}: it covers "
            f"{months_covered:.1f} months of fixed costs plus debt service, and 3 are required.",
            [required_id, months_id],
            trigger_ids=["R-NSP-08"],
        )


def check_material_assumptions(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-6.1: ranks 1-3 are identified, and each top driver is backed; one request per unbacked driver.

    A driver counts as backed when it is VERIFIED, or when it is marked as backed and a supporting document (quote,
    lease or letter of intent, comparable data) is listed in documents_provided.
    """
    if not app.material_assumptions:
        context.find(
            "top_drivers_not_identified",
            "request_more_information",
            ["NSP-6.1"],
            [SENIOR_UNDERWRITER],
            "The plan does not identify its top drivers (the assumptions that move projected net income the most).",
            ["APP-material_assumptions"],
        )
        return
    missing = missing_driver_ranks(app)
    if missing:
        context.find(
            "top_drivers_not_identified",
            "request_more_information",
            ["NSP-6.1"],
            [SENIOR_UNDERWRITER],
            f"The plan does not identify all of its top drivers: no material assumption has tornado rank "
            f"{', '.join(str(rank) for rank in missing)} (ranks 1, 2 and 3 are required).",
            ["APP-material_assumptions"],
        )
    drivers = top_drivers(app)
    evidence = ["APP-material_assumptions", "APP-documents_provided"]
    unbacked = [item for item in drivers if not is_backed(item, app)]
    for item in unbacked:
        if item.supporting_document_provided:
            problem = (
                "is marked as backed, but the documents provided list no quote, lease or letter of intent, or "
                "comparable data"
            )
        else:
            problem = "has no supporting document"
        context.find(
            "material_assumption_unsupported",
            "request_more_information",
            ["NSP-6.1"],
            [SENIOR_UNDERWRITER],
            f"Top driver {item.name} (rank {item.tornado_rank}, status {item.status}) {problem}; request a quote, a "
            "lease or letter of intent, or comparable data with its source.",
            evidence,
        )
    if drivers and not unbacked and not missing:
        context.passed(
            "material_assumptions_supported",
            ["NSP-6.1"],
            f"Every top driver ({', '.join(item.name for item in drivers)}) is backed by a supporting document in the "
            "file or has VERIFIED status.",
            evidence,
        )


# --------------------------------------------------------------------------------------------------------------
# Collateral, guarantees, ownership
# --------------------------------------------------------------------------------------------------------------


def check_startup_collateral(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-7.1 (NBC-5.1): collateral coverage of at least 1.00, required above $250,000."""
    amount = app.loan_request.amount
    if amount <= COLLATERAL_REQUIRED_ABOVE and not app.collateral:
        context.passed(
            "collateral",
            ["NSP-7.1"],
            f"A request of {money(amount)} may be unsecured (not above {money(COLLATERAL_REQUIRED_ABOVE)}).",
            ["APP-requested_amount"],
        )
        return
    coverage = startup_collateral_coverage(app)
    coverage_id = context.calc(
        "startup_collateral_coverage",
        "Collateral coverage (value x advance rate / loan amount)",
        coverage,
        "ratio",
        ["NSP-7.1", "NBC-5.1"],
        loan_amount=amount,
    )
    if coverage >= MIN_COLLATERAL_COVERAGE:
        context.passed(
            "collateral",
            ["NSP-7.1", "NBC-5.1"],
            f"Collateral coverage is {ratio(coverage)} (at least 1.00).",
            [coverage_id],
        )
        return
    refer_to = (
        [SENIOR_UNDERWRITER] if coverage >= COMMITTEE_COLLATERAL_COVERAGE else [SENIOR_UNDERWRITER, CREDIT_COMMITTEE]
    )
    context.find(
        "collateral_coverage_below_minimum",
        "refer",
        ["NSP-7.1", "NBC-5.1", "NSP-9.2"],
        refer_to,
        f"Collateral coverage is {ratio(coverage)} (below 1.00).",
        [coverage_id, "APP-collateral"],
        trigger_ids=["R-NSP-09"],
    )


def check_guarantees(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-7.2: every owner with 20% or more guarantees the loan."""
    needed = [owner for owner in app.owners if owner.ownership_pct >= GUARANTEE_OWNERSHIP_PCT]
    missing = [owner.owner_id for owner in needed if not owner.personal_guarantee]
    if missing:
        context.find(
            "guarantee_missing",
            "request_more_information",
            ["NSP-7.2"],
            [SENIOR_UNDERWRITER],
            f"Owner(s) {', '.join(missing)} own 20% or more but have not signed a guarantee.",
            ["APP-owners"],
        )
    else:
        context.passed(
            "guarantees", ["NSP-7.2"], "Every owner with 20% or more has signed a guarantee.", ["APP-owners"]
        )


def check_startup_ownership(app: StartupCreditApplication, context: RuleContext) -> None:
    """NSP-7.3 (NBC-6.1): ownership adds up to 100% with one control person, else Senior UW and KYC Operations."""
    total = round(sum(owner.ownership_pct for owner in app.owners), 1)
    total_id = context.calc(
        "startup_ownership_total_pct", "Disclosed ownership total (percent)", total, "pct", ["NSP-7.3", "NBC-6.1"]
    )
    controls = [owner for owner in app.owners if owner.is_control_person]
    problems = []
    if abs(total - 100.0) > OWNERSHIP_TOTAL_TOLERANCE_PCT:
        problems.append(f"disclosed ownership adds up to {pct(total)}, not 100%")
    if len(controls) != 1:
        problems.append("the application does not name exactly one control person")
    if problems:
        context.find(
            "ownership_discrepancy",
            "refer",
            ["NSP-7.3", "NBC-6.1", "NBC-7.3"],
            [SENIOR_UNDERWRITER, KYC_OPERATIONS],
            "Ownership discrepancy: " + "; ".join(problems) + ".",
            [total_id, "APP-owners"],
            trigger_ids=["R-NBC-08"],
            blocking=True,
        )
    else:
        context.passed(
            "ownership",
            ["NSP-7.3", "NBC-6.1"],
            f"Ownership adds up to {pct(total)} with one control person.",
            [total_id],
        )


def evaluate_startup_rules(app: StartupCreditApplication, inputs: PolicyInputs, context: RuleContext) -> None:
    """Run every startup program rule that the structured application supports, in a fixed order."""
    del inputs  # sanctions and injection are checked in policy.py; the risk model is not applicable (NSP-1.1)
    check_startup_product_limits(app, context)
    check_startup_status(app, context)
    check_startup_entity(app, context)
    check_startup_industry(app, context)
    check_owner_experience(app, context)
    check_startup_principals(app, context)
    check_startup_documents(app, context)
    total_cost = check_sources_and_uses(app, context)
    check_equity_injection(app, total_cost, context)
    service = check_projected_dscr(app, context)
    check_startup_stress(app, service, context)
    check_projection_assumptions(app, context)
    record_simulation_outputs(app, context)
    check_working_capital_reserve(app, service, context)
    check_material_assumptions(app, context)
    check_startup_collateral(app, context)
    check_guarantees(app, context)
    check_startup_ownership(app, context)
    for rule, sections, reason in NOT_EVALUATED:
        context.skip(rule, sections, reason)
