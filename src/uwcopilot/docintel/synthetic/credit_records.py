"""InjectedProblem records for a SYNTHETIC loan packet, built once the document ids are known.

Each builder returns a ProblemRecord: the documents and values involved, the resolving evidence request, the case
values that fill the resolution template, the latent option and the exact latent substitutions (see
problems.make_problem). doc_ids maps document keys to packet doc ids: "loan_application", "profit_and_loss",
"balance_sheet", "debt_schedule", "credit_report", "formation_document", "tax_return_summary",
"bank_statement:YYYY-MM", "photo_id:O1", "collateral_valuation:1".

LATENT OUTCOMES DO NOT FOLLOW THE TEMPLATE PRIORS (read this before using the truths to evaluate resolution or
value-of-information). config/resolution_templates.yaml gives each option a prior, but the generator does NOT draw
the latent option from those priors. The latent option is the one the problem's CAUSE implies, or the option
nearest to the true hidden value, so for several problem types it is the same in every packet:
- missing_pages: the removed month is one of the case's own months (seasonality of +-15%), so it is almost always
  nearest to the median of the others: month_typical;
- missing_document: provided_consistent (no packet carries an "inconsistent" late document); a request above
  $500,000 always adds a cpa_reviewed_financials missing document (no document type can satisfy it), so that
  checklist code follows from the amount alone: report it apart from drawn missing documents;
- each revenue_gap cause maps to one option (non-revenue counted and unsupported -> verified_is_right, unidentified
  -> stated_is_right, timing -> midpoint); name/FEIN typos -> typo_confirmed; prompt_injection -> confirmed.
A resolution or calibration evaluation on these truths therefore measures how well a method recovers the cause,
not how it handles the spread the priors describe. Drawing latents from the priors (and building the hidden month
or claims to match) is left to the Phase 4 simulator.
"""

import statistics

from uwcopilot.agent.applications import MonthlyDeposits
from uwcopilot.agent.credit_calculations import month_eligibility
from uwcopilot.docintel.synthetic.common import money
from uwcopilot.docintel.synthetic.credit_application import (
    application_as_documented,
    month_text,
    monthly_deposits,
    schedule_totals,
)
from uwcopilot.docintel.synthetic.credit_case import CreditCase
from uwcopilot.docintel.synthetic.problems import ProblemPlan, ProblemRecord, nearest_option

ZERO_MONTH = {
    "transfers_between_accounts": 0.0,
    "refunds_and_reversals": 0.0,
    "loan_proceeds": 0.0,
    "owner_contributions": 0.0,
    "one_time_receipts": 0.0,
    "unidentified_deposits": 0.0,
    "cash_deposits": 0.0,
}
MISSING_CODES = {
    "formation_document": ["formation_documents", "beneficial_ownership_certification"],
    "debt_schedule": ["debt_schedule"],
    "photo_id": ["principal_photo_ids"],
    "tax_return_summary": ["business_tax_returns_2_years"],
    "cpa_reviewed_financials": ["cpa_reviewed_financials"],
}
# Application fields computed from the statements that change when a removed statement month comes back.
STATEMENT_DERIVED_FIELDS: tuple[str, ...] = (
    "bank_statements.nsf_events_last_6_months",
    "bank_statements.negative_balance_months_last_12",
    "bank_statements.average_daily_balance_last_3_months",
    "debt.existing_monthly_payment_observed",
    "debt.mca_monthly_remittance",
    "new_loans_last_90_days",
)
IDENTITY_FIELDS = {
    ("name", "formation_document"): "formation.entity_name",
    ("name", "tax_return_summary"): "tax_return.business_name",
    ("name", "profit_and_loss"): "profit_and_loss.business_name",
    ("name", "balance_sheet"): "balance_sheet.business_name",
    ("name", "debt_schedule"): "debt_schedule.business_name",
    ("fein", "formation_document"): "formation.fein",
    ("fein", "tax_return_summary"): "tax_return.fein",
}


def eligible(month: dict) -> float:
    """NBC-3.3 eligible deposits of a MonthlyDeposits dict."""
    return month_eligibility(MonthlyDeposits.model_validate(month), False).eligible


def verified(months: list[dict]) -> float:
    """Sum of eligible deposits (the months are assumed to be the 12 months of a complete window)."""
    return round(sum(eligible(month) for month in months), 2)


def statement_doc_ids(case: CreditCase, doc_ids: dict[str, str]) -> list[str]:
    """Doc ids of the statements present, oldest first."""
    return [doc_ids[f"bank_statement:{month_text(item.month)}"] for item in case.present_statements()]


def revenue_gap(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Stated revenue vs revenue verified from deposits (NBC-3.4)."""
    documented = verified(app["bank_statements"]["months"])
    true_months = [monthly_deposits(item, use_true_categories=True) for item in case.present_statements()]
    true_verified = verified(true_months)
    stated = app["stated_annual_revenue"]
    midpoint = round((stated + documented) / 2, 2)
    latent = {
        "benign_non_revenue_counted": ("verified_is_right", {"stated_annual_revenue": documented}),
        "benign_unidentified_revenue": ("stated_is_right", {"@verified_annual_revenue": true_verified}),
        "benign_timing": ("midpoint", {"stated_annual_revenue": midpoint, "@verified_annual_revenue": midpoint}),
        "unsupported_overstatement": ("verified_is_right", {"stated_annual_revenue": documented}),
    }[plan.cause]
    request = {
        "benign_non_revenue_counted": "revenue_breakdown_by_account",
        "benign_unidentified_revenue": "revenue_breakdown_by_account",
        "benign_timing": "fiscal_year_revenue_reconciliation",
        "unsupported_overstatement": "revenue_source_documentation",
    }[plan.cause]
    values: dict = {doc_ids["loan_application"]: stated}
    for item, doc_id in zip(case.present_statements(), statement_doc_ids(case, doc_ids), strict=True):
        values[doc_id] = item.total_deposits
    values[doc_ids["profit_and_loss"]] = case.pnl.revenue
    fields = ["application.stated_annual_revenue", "statement.total_deposits", "profit_and_loss.revenue"]
    if "tax_return_summary" in doc_ids:
        values[doc_ids["tax_return_summary"]] = case.tax_years[-1].gross_receipts
        fields.append("tax_return.gross_receipts")
    return ProblemRecord(
        doc_ids=list(values),
        fields=fields,
        values_by_doc=values,
        description=f"Stated annual revenue {money(stated)} versus revenue verified from deposits "
        f"{money(documented)}; true cause {plan.cause}.",
        resolving_request=request,
        params={"verified": documented, "stated": stated, "midpoint": midpoint},
        latent_option_id=latent[0],
        latent_substitutions=latent[1],
    )


def dotted(data: dict, path: str) -> object:
    """The value at a plain dotted path ("debt.mca_monthly_remittance") of an application dict."""
    value: object = data
    for part in path.split("."):
        value = value[part]  # type: ignore[index]
    return value


def complete_statement_values(case: CreditCase, app: dict) -> dict:
    """STATEMENT_DERIVED_FIELDS as a reader of all 12 statements would compute them (only the ones that change).

    With a statement month removed, the NSF count, negative months, average daily balance, observed loan and MCA
    payments and new loans are computed from the 11 statements present. The latent world has all 12.
    """
    removed = case.removed_month_index
    case.removed_month_index = None
    try:
        complete = application_as_documented(case)
    finally:
        case.removed_month_index = removed
    changes = {}
    for path in STATEMENT_DERIVED_FIELDS:
        if dotted(complete, path) != dotted(app, path):
            changes[path] = dotted(complete, path)
    return changes


def missing_pages(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """One statement month removed from the packet (NBC-3.2 cannot verify revenue until it arrives).

    Every option (and the latent world) also adds the bank_statements_12_months checklist code: once the month
    arrives the packet holds 12 consecutive statements. The latent world also carries the account-conduct and
    observed-payment figures recomputed over all 12 statements (complete_statement_values).
    """
    index = plan.details["month_index"]
    true_month = monthly_deposits(case.statements[index])
    month = true_month["month"]
    median = statistics.median(eligible(item) for item in app["bank_statements"]["months"])
    candidates = {"month_typical": median, "month_low": 0.5 * median, "month_high": 1.5 * median}
    params = {
        name: {"month": month, "total_deposits": round(value, 2), **ZERO_MONTH}
        for name, value in zip(
            ("month_at_median", "month_at_half_median", "month_at_1_5_median"), candidates.values(), strict=True
        )
    }
    before = doc_ids[f"bank_statement:{month_text(case.statements[index - 1].month)}"]
    after = doc_ids[f"bank_statement:{month_text(case.statements[index + 1].month)}"]
    return ProblemRecord(
        doc_ids=[before, after],
        fields=["statement.period_start"],
        values_by_doc={
            before: month_text(case.statements[index - 1].month),
            after: month_text(case.statements[index + 1].month),
        },
        description=f"The bank statement for {month} is missing between {before} and {after}.",
        resolving_request="missing_statement_month",
        resolving_request_params={"month": month},
        params=params,
        latent_option_id=nearest_option(eligible(true_month), candidates),
        latent_substitutions={
            "+bank_statements.months": true_month,
            "documents_provided+": "bank_statements_12_months",
            **complete_statement_values(case, app),
        },
        extra_option_substitutions={"documents_provided+": "bank_statements_12_months"},
    )


def missing_description(doc_type: str, codes: list[str]) -> str:
    """The problem description of a missing document."""
    if doc_type == "cpa_reviewed_financials":
        return (
            "The packet has no CPA-reviewed financial statements, which NBC-3.1 requires above $500,000 "
            "(cpa_reviewed_financials missing; no packet document type carries them)."
        )
    return f"The packet has no {doc_type.replace('_', ' ')} ({', '.join(codes)} missing)."


def missing_document(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """A required document left out of the packet (NBC-3.1), including the CPA-reviewed financials of a request
    above $500,000 (credit_generate.add_cpa_financials_problem)."""
    codes = MISSING_CODES[plan.details["doc_type"]]
    latent: dict = {"documents_provided+": codes if len(codes) > 1 else codes[0]}
    if plan.details["doc_type"] == "debt_schedule":
        saved = list(case.missing_documents)
        case.missing_documents.remove("debt_schedule")
        kept, _, balance = schedule_totals(case)
        case.missing_documents = saved
        latent.update({"debt.existing_monthly_payment_schedule": kept, "debt.existing_total_debt_balance": balance})
    extra = {"documents_provided+": codes} if len(codes) > 1 else {}
    return ProblemRecord(
        doc_ids=[],
        fields=["documents_provided"],
        values_by_doc={},
        description=missing_description(plan.details["doc_type"], codes),
        resolving_request="missing_document",
        resolving_request_params={"document": codes[0]},
        params={"document": codes[0]},
        latent_option_id="provided_consistent",
        latent_substitutions=latent,
        extra_option_substitutions=extra,
    )


def identity_mismatch(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """A different business name or FEIN on one document (benign typo or unexplained)."""
    kind = "name" if plan.problem_type == "name_mismatch" else "fein"
    doc_type = plan.details["doc_type"]
    application_value = case.legal_name if kind == "name" else case.fein
    odd = doc_ids[doc_type]
    benign = plan.cause == "benign_typo"
    check = "check:name_consistency" if kind == "name" else "check:fein_consistency"
    return ProblemRecord(
        doc_ids=[doc_ids["loan_application"], odd],
        fields=[f"application.{'legal_name' if kind == 'name' else 'fein'}", IDENTITY_FIELDS[(kind, doc_type)]],
        values_by_doc={doc_ids["loan_application"]: application_value, odd: plan.details["value"]},
        description=f"The {kind} on {odd} ({doc_type}) differs from the application; true cause {plan.cause}.",
        resolving_request="corrected_application" if benign else "identity_reverification",
        params={},
        latent_option_id="typo_confirmed" if benign else "identity_issue",
        latent_substitutions={check: "resolved" if benign else "confirmed"},
    )


def owners_per_formation(case: CreditCase, app: dict) -> list[dict]:
    """The application's owners list rewritten to the formation document's members and percentages."""
    by_name = {owner["name"]: owner for owner in app["owners"]}
    return [{**by_name[member.name], "ownership_pct": member.ownership_pct} for member in case.formation_members]


def owner_mismatch(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Formation document ownership differs from the application (NBC-6.1)."""
    owner_id = plan.details["owner_id"]
    application_owner = next(owner for owner in case.owners if owner.owner_id == owner_id)
    member = next((item for item in case.formation_members if item.owner_id == owner_id), None)
    other = owners_per_formation(case, app)
    latent = plan.details["latent"]
    substitutions = {
        "application_correct": {"check:owner_consistency": "resolved"},
        "other_document_correct": {"check:owner_consistency": "resolved", "owners": other},
        "unresolved": {"check:owner_consistency": "confirmed"},
    }[latent]
    formation = doc_ids["formation_document"]
    return ProblemRecord(
        doc_ids=[doc_ids["loan_application"], formation],
        fields=["application.owners.ownership_pct", "formation.members.ownership_pct"],
        values_by_doc={
            doc_ids["loan_application"]: application_owner.ownership_pct,
            formation: None if member is None else member.ownership_pct,
        },
        description=f"Owner {owner_id} ownership differs between the application and the formation document "
        f"({plan.details['variant']}); true cause {plan.cause}.",
        resolving_request="ownership_reconciliation",
        params={"owners_per_other_document": other},
        latent_option_id=latent,
        latent_substitutions=substitutions,
    )


def balance_discontinuity(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """A statement whose opening balance or printed totals do not add up (NBC-9.1 integrity indicator)."""
    index = plan.details["month_index"]
    statement = case.statements[index]
    doc_id = doc_ids[f"bank_statement:{month_text(statement.month)}"]
    if plan.details["variant"] == "opening":
        previous = doc_ids[f"bank_statement:{month_text(case.statements[index - 1].month)}"]
        values = {previous: case.statements[index - 1].closing, doc_id: statement.shown_opening}
        fields = ["statement.closing_balance", "statement.opening_balance"]
    else:
        total = plan.details["total"]
        shown = statement.shown_total_deposits if total == "deposits" else statement.shown_total_withdrawals
        values = {doc_id: shown}
        fields = [f"statement.total_{total}"]
    latent = plan.details["latent"]
    return ProblemRecord(
        doc_ids=list(values),
        fields=fields,
        values_by_doc=values,
        description=f"Statement {month_text(statement.month)} ({doc_id}) does not reconcile "
        f"({plan.details['variant']}).",
        resolving_request="bank_verified_statement",
        params={},
        latent_option_id=latent,
        latent_substitutions={"check:balance_continuity": "resolved" if latent == "bank_confirms" else "confirmed"},
    )


def debt_payment_mismatch(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """The schedule's payment for one lender differs from the payments in the statements (NBC-4.1)."""
    debt = next(item for item in case.loans() if item.lender == plan.details["lender"])
    observed_total = app["debt"]["existing_monthly_payment_observed"]
    scheduled_total = app["debt"]["existing_monthly_payment_schedule"]
    values: dict = {doc_ids["debt_schedule"]: debt.scheduled_payment}
    label = f"ACH PMT {debt.lender.upper()} LOAN"
    for item in case.present_statements():
        paid = round(sum(row.amount for row in item.rows if row.description == label), 2)
        if paid:
            values[doc_ids[f"bank_statement:{month_text(item.month)}"]] = paid
    if plan.details["latent"] == "observed_is_current":
        true_schedule = round(scheduled_total - debt.scheduled_payment + debt.observed_payment, 2)
    else:
        true_schedule = scheduled_total
    return ProblemRecord(
        doc_ids=list(values),
        fields=["debt_schedule.items.monthly_payment", "statement.transactions.withdrawal"],
        values_by_doc=values,
        description=f"Scheduled payment {money(debt.scheduled_payment)} for {debt.lender} differs from the "
        "payments in the statements.",
        resolving_request="debt_schedule_update",
        params={"observed_payment": observed_total, "scheduled_payment": scheduled_total},
        latent_option_id=plan.details["latent"],
        latent_substitutions={"debt.existing_monthly_payment_schedule": true_schedule},
    )


def injection_doc_key(case: CreditCase, plan: ProblemPlan) -> str:
    """The document key where the embedded instruction is printed."""
    location = plan.details["location"]
    if location == "remarks":
        return "loan_application"
    if location == "pnl_notes":
        return "profit_and_loss"
    return f"bank_statement:{month_text(case.statements[plan.details['month_index']].month)}"


def prompt_injection(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """Instruction-like text in a document (NBC-9.2): routed to Fraud Operations."""
    doc_id = doc_ids[injection_doc_key(case, plan)]
    fields = {"remarks": "application.remarks", "memo": "statement.transactions", "pnl_notes": "profit_and_loss"}
    return ProblemRecord(
        doc_ids=[doc_id],
        fields=[fields[plan.details["location"]]],
        values_by_doc={doc_id: plan.details["text"]},
        description=f"Embedded instruction ({plan.details['technique']}) in the {plan.details['location']} "
        f"of {doc_id}.",
        resolving_request="fraud_operations_review",
        params={},
        latent_option_id="confirmed",
        latent_substitutions={"check:embedded_instructions": "confirmed"},
    )


BUILDERS = {
    "revenue_gap": revenue_gap,
    "missing_pages": missing_pages,
    "missing_document": missing_document,
    "name_mismatch": identity_mismatch,
    "fein_mismatch": identity_mismatch,
    "owner_mismatch": owner_mismatch,
    "balance_discontinuity": balance_discontinuity,
    "debt_payment_mismatch": debt_payment_mismatch,
    "prompt_injection": prompt_injection,
}


def credit_record(case: CreditCase, plan: ProblemPlan, app: dict, doc_ids: dict[str, str]) -> ProblemRecord:
    """The ProblemRecord of one applied credit problem."""
    return BUILDERS[plan.problem_type](case, plan, app, doc_ids)
