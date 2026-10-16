"""Guardrails: a change-set may only change plan choices, never a fact (Studio S5, CLAUDE.md rule 8 spirit).

What the search may change (the allow-list, levers.LEVER_NAMES): owner equity, loan amount, build-out cost, cash
reserve at opening, price, days open, loan term and the choice between the founder's own candidate sites.
What it may never change: the owner's credit score and experience, other debt (never hidden, never reduced), the
site's demand estimate, every assumption range, the note rate and rate type, and every figure the founder marked
as verified. apply_changes() turns a change-set into a new PlanCase and then checks field by field that nothing
outside the levers' own fields (space.LEVER_WRITES) differs from the plan; anything else is a GuardrailViolation.

How the dollar levers keep sources equal to uses (levers.BALANCED_BY):
- owner_equity +d: equity +d, loan -d;      - loan_amount +d: loan +d (the cash reserve absorbs it);
- build_out_cost +d: build-out +d, loan +d; - working_capital_reserve +d: equity +d (the founder's own cash).
Deltas of several dollar levers add up; then every amount must be valid (no negative equity or reserve, a positive
loan) and inside its bounds, whichever lever moved it.
"""

from collections.abc import Sequence
from typing import Any

from uwcopilot.studio.feasibility.inputs import Loan, Plan
from uwcopilot.studio.path_to_approval.errors import GuardrailViolation, LeverError, OutOfBounds
from uwcopilot.studio.path_to_approval.levers import AMOUNT_LEVERS, LEVER_NAMES, VALUE_TOLERANCE
from uwcopilot.studio.path_to_approval.models import Change, PlanCase
from uwcopilot.studio.path_to_approval.space import LEVER_WRITES, Amounts, LeverSpace

# Names people (or code) might try to move, with why they are not plan choices.
NOT_A_LEVER: dict[str, str] = {
    "credit_score": "the owner's credit score is a fact; it is never changed or misstated",
    "experience_months": "the owner's experience is a fact; it is never changed or misstated",
    "other_debt": "other debt is a fact; hiding or reducing it would misstate the plan",
    "verified_fields": "which figures are verified is a fact about the documents",
    "annual_rate_pct": "the note rate is the lender's quote (or prime plus a spread), not a founder choice",
    "rate_spread_pct": "the spread over prime is the lender's pricing, not a founder choice",
    "rate_type": "fixed or variable is the lender's product, not a lever of this search",
    "assumption_overrides": "assumption ranges describe the market; they are edited with evidence, never to pass",
    "mature_demand": "the site's demand estimate is data or a model output; choose another candidate site instead",
    "site_demand": "the site's demand estimate is data or a model output; choose another candidate site instead",
    "monthly_rent": "a rent quote is the landlord's figure; choose another candidate site instead",
    "hourly_wage": "the wage comes from the founder's plan or the metro's data, not from this search",
    "capacity": "more capacity costs capital the plan cannot price yet (not modeled as a lever)",
    "opening_date": "the opening date is not a lever of this search",
    "owner_draw_per_month": "the owner's draw is not a lever of this search",
}
GUARDRAIL_RULES: tuple[str, ...] = (
    "Only plan choices move: owner equity, loan amount, build-out cost, cash reserve at opening, price, days open, "
    "loan term and the choice between your own candidate sites in the same metro.",
    "Facts never move: your credit score, your experience, your other debts (never hidden or reduced), the site's "
    "demand estimate, the note rate and every figure you marked as verified.",
    "Assumption ranges never move: they describe the market and are changed with evidence, never to pass a target.",
    "Every change-set is checked field by field; anything outside its levers' own fields that differs from your "
    "plan is refused.",
    "Every amount stays inside your bounds whichever lever moves it, and sources always equal uses.",
    "A change-set may not break a rule of the SYNTHETIC startup program that your plan meets now (equity injection "
    "NSP-3.1, loan amount and term limits NSP-8.1).",
    "Reaching the targets is not an approval: Studio never approves, declines or recommends a lender.",
)


def check_lever(space: LeverSpace, change: Change) -> None:
    """The lever is a plan choice, available for this plan, and its value is allowed. Raises otherwise."""
    name = change.lever
    if name not in LEVER_NAMES:
        reason = NOT_A_LEVER.get(name, "it is not on the allow-list of plan choices")
        raise GuardrailViolation(f"{name!r} is not a plan choice: {reason}. Levers: {', '.join(LEVER_NAMES)}")
    if name in space.not_available:
        reason = space.not_available[name]
        if "verified" in reason:
            raise GuardrailViolation(f"{name} cannot move: {reason}")
        raise LeverError(f"{name} is not available for this plan: {reason}")
    if name == "site":
        if not isinstance(change.value, str) or change.value not in space.candidates:
            raise LeverError(f"site {change.value!r} is not one of your candidate sites {sorted(space.candidates)}")
        return
    if isinstance(change.value, str):
        raise LeverError(f"{name} needs a number, not {change.value!r}")
    low, high = space.bounds[name]
    if not low - VALUE_TOLERANCE <= change.value <= high + VALUE_TOLERANCE:
        raise OutOfBounds(f"{name} = {change.value} is outside its bounds [{low}, {high}]")


def moved(value: float, delta: float) -> float:
    """value + delta rounded to the cent, or value itself (not rounded) when the deltas cancel out."""
    if abs(delta) <= VALUE_TOLERANCE:
        return value
    return round(value + delta, 2)


def amount_deltas(space: LeverSpace, changes: Sequence[Change]) -> Amounts:
    """The plan's money after the dollar levers in the change-set (deltas add up; see the module docstring).

    Every amount a lever moves is rounded to the cent HERE, before any check, so the checks see exactly the amounts
    the plan will carry.
    """
    equity_delta = loan_delta = capex_delta = 0.0
    for change in changes:
        if change.lever not in AMOUNT_LEVERS:
            continue
        delta = float(change.value) - float(space.current[change.lever])
        if change.lever == "owner_equity":
            equity_delta, loan_delta = equity_delta + delta, loan_delta - delta
        elif change.lever == "loan_amount":
            loan_delta += delta
        elif change.lever == "build_out_cost":
            capex_delta, loan_delta = capex_delta + delta, loan_delta + delta
        elif change.lever == "working_capital_reserve":
            equity_delta += delta
    before = space.amounts
    return Amounts(
        equity=moved(before.equity, equity_delta),
        loan=moved(before.loan, loan_delta),
        other=before.other,
        capex=moved(before.capex, capex_delta),
    )


def check_amounts(space: LeverSpace, amounts: Amounts) -> None:
    """Every amount valid and inside its bounds, whichever lever moved it. Raises OutOfBounds.

    The cash check is written exactly as the simulator writes it (setup.build_setup: owner equity, which for the
    simulator includes other debt, plus the loan must cover the build-out), so an allowed change-set can never be
    refused by the simulator over a rounding cent.
    """
    if amounts.loan <= 0:
        raise OutOfBounds(f"the loan would be {amounts.loan:,.2f}; it must stay positive")
    if amounts.equity < 0 or amounts.capex <= 0:
        raise OutOfBounds("equity cannot go below zero, and the build-out must stay positive")
    if (amounts.equity + amounts.other) + amounts.loan < amounts.capex:
        raise OutOfBounds("the sources would not cover the build-out (cash at opening below zero)")
    for name in AMOUNT_LEVERS:
        if name not in space.bounds:
            continue
        low, high = space.bounds[name]
        value = amounts.value_of(name)
        unchanged = abs(value - space.amounts.value_of(name)) <= VALUE_TOLERANCE
        if not unchanged and not low - VALUE_TOLERANCE <= value <= high + VALUE_TOLERANCE:
            raise OutOfBounds(f"{name} would be {value:,.2f}, outside its bounds [{low:,.2f}, {high:,.2f}]")


def changed_plan(space: LeverSpace, changes: Sequence[Change], amounts: Amounts) -> PlanCase:
    """The new PlanCase: the levers' fields set, everything else copied from the plan."""
    case = space.case
    plan_fields: dict[str, Any] = case.plan.model_dump()
    loan_fields: dict[str, Any] = case.loan.model_dump()
    site, site_label = case.site, case.site_label
    before = space.amounts
    if amounts.equity != before.equity:
        plan_fields["owner_equity"] = amounts.equity
    if amounts.loan != before.loan:
        loan_fields["amount"] = amounts.loan
    if amounts.capex != before.capex:
        plan_fields["capex"] = amounts.capex
    for change in changes:
        if change.lever == "price":
            plan_fields["price"] = float(change.value)
        elif change.lever == "loan_term_months":
            loan_fields["term_months"] = int(change.value)
        elif change.lever == "days_open_per_week":
            days = float(change.value)
            current_days = float(space.current["days_open_per_week"])
            plan_fields["days_open_per_week"] = days
            plan_fields["paid_hours_per_month"] = round(case.plan.paid_hours_per_month * days / current_days, 1)
        elif change.lever == "site":
            candidate = space.candidates[str(change.value)]
            site, site_label = candidate.site, candidate.label
            plan_fields["monthly_rent"] = candidate.monthly_rent
    return PlanCase(
        profile_id=case.profile_id,
        metro_id=case.metro_id,
        site_label=site_label,
        site=site,
        plan=Plan.model_validate(plan_fields),
        loan=Loan.model_validate(loan_fields),
        facts=case.facts,
    )


def field_values(case: PlanCase) -> dict[str, Any]:
    """Every field of a plan as {path: value} ("plan.price", "loan.amount", "site", "facts", ...)."""
    values: dict[str, Any] = {
        "profile_id": case.profile_id,
        "metro_id": case.metro_id,
        "site_label": case.site_label,
        "site": case.site.model_dump(),
        "facts": case.facts.model_dump(),
    }
    values.update({f"plan.{name}": value for name, value in case.plan.model_dump().items()})
    values.update({f"loan.{name}": value for name, value in case.loan.model_dump().items()})
    return values


def check_only_lever_fields_changed(before: PlanCase, after: PlanCase, levers: Sequence[str]) -> list[str]:
    """The changed field paths, after checking each is written by one of the levers and none is verified."""
    old, new = field_values(before), field_values(after)
    changed = [path for path in old if old[path] != new[path]]
    allowed = {path for lever in levers for path in LEVER_WRITES.get(lever, ())}
    outside = [path for path in changed if path not in allowed]
    if outside:
        raise GuardrailViolation(f"the change-set {list(levers)} would also edit {outside}, which are not its levers")
    verified = sorted(set(changed) & set(before.facts.verified_fields))
    if verified:
        raise GuardrailViolation(f"the change-set {list(levers)} would edit verified figure(s) {verified}")
    return changed


def apply_changes(space: LeverSpace, changes: Sequence[Change]) -> PlanCase:
    """The plan with the change-set applied, after every guardrail check (see the module docstring)."""
    names = [change.lever for change in changes]
    if len(set(names)) != len(names):
        raise LeverError(f"each lever may appear once in a change-set (got {names})")
    for change in changes:
        check_lever(space, change)
    amounts = amount_deltas(space, changes)
    check_amounts(space, amounts)
    after = changed_plan(space, changes, amounts)
    check_only_lever_fields_changed(space.case, after, names)
    return after
