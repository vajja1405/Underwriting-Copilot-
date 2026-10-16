"""Build one SYNTHETIC loan case end to end (before rendering): draws, problems, statements and financials.

The order matters and is fixed so a seed always gives the same case:
1. identity, loan request, owners, debts (with the minimums the planned problems need);
2. the statement months the statement problems use, the revenue plan, a debt payment mismatch;
3. 12 months of transactions (build_statements), then printed overrides;
4. the requested amount and EBITDA from the target DSCR, the P&L, balance sheet, tax years, collateral;
5. stated revenue, then the document-level problems;
6. a missing_document problem for the CPA-reviewed financial statements when the request is above $500,000
   (NBC-3.1 requires them, and no packet document type carries them), so every required document the packet
   lacks has a problem record that resolves it.
"""

import random
from datetime import date

from faker import Faker

from uwcopilot.agent.policy_credit import CPA_FINANCIALS_ABOVE
from uwcopilot.docintel.synthetic.common import (
    CPA_FIRMS,
    PAYROLL_PROVIDERS,
    UTILITIES,
    add_months,
    cents,
    counterparty,
    log_uniform,
    month_end,
)
from uwcopilot.docintel.synthetic.credit_application import average_monthly
from uwcopilot.docintel.synthetic.credit_build import (
    BENIGN_REMARKS,
    CARD_INDUSTRIES,
    cash_share,
    draw_application_date,
    draw_identity,
    draw_loan_request,
    draw_loans,
    draw_mca,
    draw_owners,
    largest_customer_pct,
)
from uwcopilot.docintel.synthetic.credit_case import CreditCase, Statement
from uwcopilot.docintel.synthetic.credit_finance import (
    build_balance_sheet,
    build_collateral,
    build_pnl,
    build_tax_years,
    draw_dscr_target,
    existing_annual_service,
    solve_amount_and_noi,
)
from uwcopilot.docintel.synthetic.credit_transactions import StatementPlan, build_statements, seasonal_split
from uwcopilot.docintel.synthetic.problems import ProblemPlan, find_plan
from uwcopilot.docintel.synthetic.problems_credit import (
    apply_document_problems,
    apply_statement_problems,
    clean_extras,
    plan_revenue,
    plan_statement_months,
    prepare_debt_mismatch,
    stated_revenue,
)


def statement_months(application_date: date) -> list[date]:
    """The 12 month starts ending with the month before the application month, oldest first."""
    return [add_months(application_date, offset) for offset in range(-12, 0)]


def conduct_plan(rng: random.Random) -> tuple[list[int], list[bool]]:
    """NSF fee rows per month and the months planned to close negative (mostly none; some referral cases)."""
    nsf = [0] * 12
    draw = rng.random()
    if draw < 0.05:
        for _ in range(rng.randint(7, 10)):
            nsf[rng.randint(6, 11)] += 1
    elif draw < 0.15:
        for _ in range(rng.randint(1, 3)):
            nsf[rng.randrange(12)] += 1
    negative = [False] * 12
    draw = rng.random()
    count = rng.randint(3, 4) if draw < 0.04 else (rng.randint(1, 2) if draw < 0.10 else 0)
    for index in rng.sample(range(12), count):
        negative[index] = True
    return nsf, negative


def add_cpa_financials_problem(case: CreditCase, plans: list[ProblemPlan]) -> None:
    """Record the CPA-reviewed financials a request above $500,000 needs (NBC-3.1) as a missing_document problem.

    The packet format has no document type for them, so they are always missing. This problem is a consequence of
    the requested amount, not one of the drawn problems; its details say so ("structural": True).
    """
    if case.amount > CPA_FINANCIALS_ABOVE:
        plans.append(
            ProblemPlan("missing_document", "missing", {"doc_type": "cpa_reviewed_financials", "structural": True})
        )


def unidentified_total(statements: list[Statement]) -> float:
    """Sum of the deposit rows documented as unidentified ("DEPOSIT REF") over the statements."""
    total = 0.0
    for item in statements:
        for row in item.rows:
            if row.kind == "D" and row.category == "unidentified":
                total += row.amount
    return round(total, 2)


def generate_credit_case(rng: random.Random, fake: Faker, number: int, plans: list[ProblemPlan]) -> CreditCase:
    """One SYNTHETIC loan case with its planned problems applied (plans may be updated in place)."""
    packet_id = f"PKT-C-{number:03d}"
    application_date = draw_application_date(rng)
    identity = draw_identity(rng, fake, application_date)
    request = draw_loan_request(rng)
    months = statement_months(application_date)
    verified = round(log_uniform(rng, 300_000, 6_000_000), 2)
    min_owners = 2 if find_plan(plans, "owner_mismatch") else 1
    owners = draw_owners(rng, fake, identity.structure, identity.state, application_date, min_owners)
    min_loans = 1 if find_plan(plans, "debt_payment_mismatch") else 0
    loans = draw_loans(rng, verified, months, application_date, min_loans)
    mca = draw_mca(rng, verified, months)
    debts = loans + ([mca] if mca is not None else [])

    plan_statement_months(rng, plans)
    removed = find_plan(plans, "missing_pages").details["month_index"] if find_plan(plans, "missing_pages") else None
    last4 = f"{rng.randrange(10_000):04d}"
    suppliers = [counterparty(rng) for _ in range(4)]
    revenue = plan_revenue(rng, plans, verified, last4)
    prepare_debt_mismatch(rng, plans, loans, removed)
    eligible = seasonal_split(rng, verified, 12)
    plan = StatementPlan(
        months=months,
        eligible=eligible,
        cash_share=cash_share(rng, identity.industry),
        card_business=identity.industry in CARD_INDUSTRIES,
        card_processor=identity.card_processor,
        customers=[counterparty(rng) for _ in range(rng.randint(4, 8))],
        suppliers=suppliers,
        landlord=counterparty(rng),
        payroll_provider=rng.choice(PAYROLL_PROVIDERS),
        utility=rng.choice(UTILITIES),
        debts=debts,
        nsf_by_month=[0] * 12,
        negative_months=[False] * 12,
        balance_level=cents(verified / 12 * rng.uniform(0.15, 0.9)),
        rent=cents(verified / 12 * rng.uniform(0.04, 0.10)),
        extra_deposits=clean_extras(rng, eligible, last4, suppliers) + revenue.extra_deposits,
    )
    plan.nsf_by_month, plan.negative_months = conduct_plan(rng)
    statements = build_statements(rng, plan)
    apply_statement_problems(rng, plans, statements)

    present = [item for index, item in enumerate(statements) if index != removed]
    schedule_payment = round(sum(debt.scheduled_payment for debt in loans), 2)
    existing = existing_annual_service(
        schedule_payment, average_monthly(present, "loan_payment"), average_monthly(present, "mca_remittance")
    )
    dscr, stress_safe = draw_dscr_target(rng)
    amount, noi = solve_amount_and_noi(request, existing, verified, dscr, stress_safe)
    gap = find_plan(plans, "revenue_gap")
    if gap is not None and gap.cause == "benign_unidentified_revenue":
        pnl_revenue = revenue.true_revenue * (1.0 + rng.uniform(-0.02, 0.03))
        latest_receipts = revenue.true_revenue * (1.0 + rng.uniform(-0.04, 0.04))
    else:
        pnl_revenue = verified * (1.0 + rng.uniform(-0.02, 0.05))
        latest_receipts = verified * (1.0 + rng.uniform(-0.06, 0.06))
    if gap is not None and gap.cause == "benign_timing":
        latest_receipts = verified / (1.0 - gap.details["target_gap"])
    pnl_revenue = cents(pnl_revenue)
    ebitda = noi + max(0.0, pnl_revenue - verified)
    fixed_assets = cents(verified * rng.uniform(0.05, 0.4))
    period_end = month_end(months[-1])
    pnl = build_pnl(rng, period_end, pnl_revenue, ebitda, loans, fixed_assets)
    balance_sheet = build_balance_sheet(rng, period_end, statements[-1].closing, verified, loans, fixed_assets)
    tax_years = build_tax_years(rng, latest_receipts, application_date)
    gross = round(sum(item.total_deposits for item in statements), 2)
    others = [pnl.revenue, tax_years[-1].gross_receipts]
    stated = stated_revenue(rng, plans, verified, revenue.true_revenue, gross, others, unidentified_total(statements))
    if gap is not None and gap.cause == "benign_timing":
        tax_years[-1].gross_receipts = stated

    control = next(owner for owner in owners if owner.is_control_person)
    case = CreditCase(
        number=number,
        packet_id=packet_id,
        application_date=application_date,
        legal_name=identity.legal_name,
        fein=identity.fein,
        structure=identity.structure,
        state=identity.state,
        formation_date=identity.formation_date,
        industry=identity.industry,
        address=identity.address,
        product=request.product,
        amount=amount,
        term_months=request.term_months,
        rate_pct=request.rate_pct,
        rate_type=request.rate_type,
        purpose=request.purpose,
        stated_revenue=stated,
        largest_customer_pct=largest_customer_pct(rng, identity.industry),
        tax_lien=rng.random() < 0.03,
        owners=owners,
        bank=identity.bank,
        bank_layout=identity.bank_layout,
        account_number=identity.account_number,
        statements=statements,
        debts=debts,
        pnl=pnl,
        balance_sheet=balance_sheet,
        tax_years=tax_years,
        collateral=build_collateral(rng, request.product, amount, application_date),
        remarks=rng.choice(BENIGN_REMARKS),
        signer=control.name,
        cpa_firm=rng.choice(CPA_FIRMS),
        removed_month_index=removed,
    )
    applied = apply_document_problems(rng, case, plans)
    plans[:] = applied
    add_cpa_financials_problem(case, plans)
    return case
