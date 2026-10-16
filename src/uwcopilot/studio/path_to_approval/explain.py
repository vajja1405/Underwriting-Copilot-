"""Plain-language text for the path-to-approval result (Studio S5).

Every sentence is filled from numbers the search computed; nothing here adds a number of its own. The text never
says a loan will be approved: Studio prepares and explains, and lenders make their own decisions (rule C).
Probabilities are printed with three decimals (1,000 paths give steps of 0.001) and always carry the label
"simulated, based on stated assumptions" (rule E).

The text describes a scenario, never an instruction: "If you borrowed $106,000 more ..., the simulation gives ...;
in this simulation all three targets would be met." A change is something the founder could choose, explained with
its simulated consequence, its caveats and what would back the changed figure for a lender (evidence_for). It is
not advice (rule C). The wording of the lender's stress follows targets.stress_words: an edited stress is "your
edited revenue stress", never "the lender's".
"""

from uwcopilot.agent.policy_startup import STARTUP_MAX_TERM_MONTHS, STARTUP_MIN_TERM_MONTHS
from uwcopilot.studio import SIMULATION_LABEL
from uwcopilot.studio.path_to_approval.levers import AMOUNT_LEVERS
from uwcopilot.studio.path_to_approval.models import AppliedChange, Outcome, PlanAmounts, PlanCase, Target
from uwcopilot.studio.path_to_approval.space import LeverSpace
from uwcopilot.studio.path_to_approval.targets import stress_words

NOT_AN_APPROVAL = (
    "Reaching these simulated targets does not mean a lender will approve the loan. Lenders make their own decisions."
)
# Plain words for the target names in Outcome.targets_missed (targets.TARGET_NAMES).
TARGET_WORDS: dict[str, str] = {
    "dscr_year2": "the year-2 DSCR target",
    "stressed_dscr_year2": "the revenue-stress target",
    "cash_negative": "the cash target",
}
OWNER_CASH_CAVEAT = (
    "This counts only if you really have this cash: the lender checks owner equity against bank statements "
    "(NSP-3.1, SYNTHETIC program)."
)
BUILD_OUT_CAVEAT = (
    "This holds only if the same business can be built for less (a lower quote, used equipment); a smaller build-out "
    "that also cuts capacity is not modeled."
)
PRICE_CAVEAT = (
    "Demand responds to price through the price elasticity, an ASSUMPTION range, so a price is only as good as a "
    "test with real customers."
)
TERM_CAVEAT = (
    f"The lender decides the term (the SYNTHETIC program allows {STARTUP_MIN_TERM_MONTHS} to "
    f"{STARTUP_MAX_TERM_MONTHS} months, NSP-8.1)."
)
REPAIR_NOTE = (
    "The search does not repair a rule the plan already fails; it only refuses changes that would break such a rule "
    "or move the plan further from it."
)


def dollars(value: float) -> str:
    """$1,234 for whole dollars, $1,234.56 otherwise."""
    if abs(value - round(value)) < 0.005:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def number(value: float) -> str:
    """A plain number without trailing zeros (6, 6.5, 1012.5)."""
    return f"{value:g}"


def own_move(change: AppliedChange) -> float:
    """A dollar lever's own move (own_change, or to - from for an older result without it)."""
    if change.own_change is not None:
        return change.own_change
    return float(change.to_value) - float(change.from_value)


def amount_clause(change: AppliedChange) -> tuple[str, str | None]:
    """One dollar lever's own move as an 'if you ...' clause, and its caveat (the plan line gives the totals)."""
    own = own_move(change)
    delta = dollars(abs(own))
    up = own > 0
    if change.lever == "owner_equity":
        if up:
            return f"put in {delta} more of your own cash and borrowed that much less", OWNER_CASH_CAVEAT
        return f"put in {delta} less of your own cash and borrowed that much more", None
    if change.lever == "loan_amount":
        if up:
            return f"borrowed {delta} more and kept it as cash at opening", None
        return f"borrowed {delta} less, leaving that much less cash in reserve at opening", None
    if change.lever == "build_out_cost":
        if up:
            return f"raised the build-out budget by {delta} and borrowed that much more", None
        return f"brought the build-out and equipment cost down by {delta} and borrowed that much less", BUILD_OUT_CAVEAT
    if up:
        return f"kept {delta} more cash in reserve at opening, funded by your own cash", OWNER_CASH_CAVEAT
    return f"kept {delta} less cash in reserve at opening and put in that much less of your own cash", None


def site_clause(change: AppliedChange, space: LeverSpace) -> tuple[str, str]:
    """The site switch as a clause, with both demand estimates and what happens to the rent as its caveat."""
    candidate = space.candidates[str(change.to_value)]
    current = space.case.site
    clause = f"used your candidate site '{candidate.label}' instead of '{change.from_value}'"
    caveat = (
        f"The site '{candidate.label}' has a most likely demand of {number(round(candidate.site.mode, 1))} "
        f"{space.demand_unit}, against {number(round(current.mode, 1))} (status {candidate.site.status})."
    )
    if candidate.monthly_rent is not None:
        return clause, caveat + f" Its rent quote is {dollars(candidate.monthly_rent)} a month."
    return clause, caveat + " It has no rent quote, so the metro's rent range (ASSUMPTION) is used until you get one."


def change_clause(change: AppliedChange, space: LeverSpace, after: PlanCase) -> tuple[str, str | None]:
    """One change as an 'if you ...' clause and its caveat (None when it needs none)."""
    if change.lever in AMOUNT_LEVERS:
        return amount_clause(change)
    if change.lever == "price":
        start = dollars(float(change.from_value))
        if space.case.plan.price is None:
            start = f"the profile's most likely {start}"
        return (
            f"charged {dollars(float(change.to_value))} per unit ({space.demand_unit}) instead of {start}",
            PRICE_CAVEAT,
        )
    if change.lever == "days_open_per_week":
        hours_before = space.case.plan.paid_hours_per_month or 0.0
        hours_after = after.plan.paid_hours_per_month or 0.0
        clause = (
            f"opened {number(float(change.to_value))} days a week instead of {number(float(change.from_value))}, with "
            f"paid staff hours changing from {number(hours_before)} to {number(hours_after)} a month in proportion"
        )
        return clause, "Staff hours scaling with open days is a design choice of the simulation."
    if change.lever == "loan_term_months":
        clause = (
            f"asked the lender for a {int(float(change.to_value))}-month term instead of "
            f"{int(float(change.from_value))} months"
        )
        return clause, TERM_CAVEAT
    return site_clause(change, space)


def evidence_for(change: AppliedChange, space: LeverSpace, after: PlanCase, plan_after: PlanAmounts) -> str:
    """What would back the changed figure for a lender. Until it exists, the figure is a simulated plan change."""
    lever = change.lever
    if lever in ("owner_equity", "working_capital_reserve"):
        return f"bank statements that show {dollars(plan_after.owner_equity)} of owner cash (NSP-3.1)"
    if lever == "build_out_cost":
        return f"a written quote at or below {dollars(plan_after.build_out_cost)} for the same build-out and equipment"
    if lever == "loan_amount":
        return (
            f"a loan request of {dollars(plan_after.loan_amount)} in your sources and uses (NSP-3.2); the lender "
            "decides the amount"
        )
    if lever == "loan_term_months":
        return f"a request to the lender for a {plan_after.loan_term_months}-month term; the lender decides the term"
    if lever == "price":
        return (
            f"evidence that customers would pay {dollars(float(change.to_value))} per unit (for example a price "
            "test); the demand response to price is an ASSUMPTION"
        )
    if lever == "days_open_per_week":
        hours = number(after.plan.paid_hours_per_month or 0.0)
        return f"a staffing plan for {number(float(change.to_value))} days a week ({hours} paid hours a month)"
    candidate = space.candidates[str(change.to_value)]
    if candidate.monthly_rent is not None:
        return (
            f"the rent quote for '{candidate.label}' ({dollars(candidate.monthly_rent)} a month) in writing, for "
            "example a lease letter of intent"
        )
    return f"a rent quote or lease letter of intent for '{candidate.label}'"


def probability_lines(before: Outcome, after: Outcome, target: Target) -> list[str]:
    """Each target's probability before -> after, with the target."""
    return [
        f"chance that year-2 DSCR is at least {target.dscr_min:.2f}: {before.p_dscr_at_least_target_year2:.3f} -> "
        f"{after.p_dscr_at_least_target_year2:.3f} (target: at least {target.p_dscr_min:.2f})",
        f"chance that year-2 DSCR stays at least {target.stressed_dscr_min:.2f} under {stress_words(target)}: "
        f"{before.p_stressed_dscr_at_least_min_year2:.3f} -> {after.p_stressed_dscr_at_least_min_year2:.3f} "
        f"(target: at least {target.p_stressed_min:.2f})",
        f"chance that cash falls below zero within {target.cash_months} months: "
        f"{before.p_cash_negative_within_months:.3f} -> {after.p_cash_negative_within_months:.3f} "
        f"(target: at most {target.p_cash_negative_max:.2f})",
    ]


def ratio_text(value: float | None) -> str:
    """A ratio with two decimals, or 'none' when it has no denominator."""
    return "none" if value is None else f"{value:.2f}"


def base_case_line(before: Outcome, after: Outcome) -> str:
    """The lender's single-number checks on the year-2 median, before -> after (always the program's own stress)."""
    return (
        "The lender's own check on the year-2 median (NSP-4.1, and NSP-4.2 with the program's 20% revenue stress): "
        f"projected DSCR {ratio_text(before.projected_dscr_base_case)} -> "
        f"{ratio_text(after.projected_dscr_base_case)}, revenue-stressed DSCR "
        f"{ratio_text(before.stressed_dscr_base_case)} -> {ratio_text(after.stressed_dscr_base_case)}."
    )


def moved_text(before: float, after: float) -> str:
    """'$1,000 -> $1,200' when the amount changed, else '$1,200'."""
    if abs(after - before) < 0.005:
        return dollars(after)
    return f"{dollars(before)} -> {dollars(after)}"


def plan_line(before: PlanAmounts, after: PlanAmounts) -> str:
    """The plan's money before -> after (sources = uses), with the owner's share of the project (NSP-3.1)."""
    checks = after.checks
    term = str(after.loan_term_months)
    if after.loan_term_months != before.loan_term_months:
        term = f"{before.loan_term_months} -> {after.loan_term_months}"
    return (
        f"The plan with this change: owner equity {moved_text(before.owner_equity, after.owner_equity)}, loan "
        f"{moved_text(before.loan_amount, after.loan_amount)} over {term} months, build-out "
        f"{moved_text(before.build_out_cost, after.build_out_cost)}, cash reserve at opening "
        f"{moved_text(before.cash_reserve_at_opening, after.cash_reserve_at_opening)}; equity injection "
        f"{checks.equity_injection_pct:.1f}% of the project (the SYNTHETIC program asks for at least "
        f"{checks.equity_minimum_pct:.1f}%, NSP-3.1)."
    )


def joined_clauses(clauses: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if len(clauses) <= 1:
        return "".join(clauses)
    return ", ".join(clauses[:-1]) + " and " + clauses[-1]


def explain_change_set(
    changes: list[AppliedChange],
    space: LeverSpace,
    after_case: PlanCase,
    before: Outcome,
    after: Outcome,
    plan_before: PlanAmounts,
    plan_after: PlanAmounts,
    target: Target,
    program_rules_not_met: list[str] | None = None,
) -> str:
    """The whole explanation of one change-set, as a conditional scenario (see the module docstring)."""
    pairs = [change_clause(change, space, after_case) for change in changes]
    lines = "; ".join(probability_lines(before, after, target))
    parts = [
        f"If you {joined_clauses([clause for clause, _ in pairs])}, the simulation ({SIMULATION_LABEL}; "
        f"{after.n_paths:,} simulated futures, seed {after.seed}) gives: {lines}."
    ]
    if after.meets_target:
        parts.append("In this simulation all three targets would be met.")
    else:
        missed = ", ".join(TARGET_WORDS[name] for name in after.targets_missed)
        parts.append(f"In this simulation these would still be missed: {missed}.")
    parts.extend(caveat for _, caveat in pairs if caveat)
    parts.append(plan_line(plan_before, plan_after))
    if program_rules_not_met:
        parts.append(
            "The changed plan would still not meet these rules of the SYNTHETIC startup program: "
            + "; ".join(program_rules_not_met)
            + ". "
            + REPAIR_NOTE
        )
    parts.append(base_case_line(before, after))
    evidence = [change.evidence_needed for change in changes if change.evidence_needed]
    if evidence:
        parts.append(
            "What would back the changed figures for a lender: "
            + "; ".join(dict.fromkeys(evidence))
            + ". Until then they are plan changes from a simulation, not quotes or verified figures."
        )
    parts.append(NOT_AN_APPROVAL)
    return " ".join(parts)


def unchanged_text(case: PlanCase) -> str:
    """What every change-set leaves alone."""
    facts = case.facts
    debt = (
        f"your other debts ({len(facts.other_debt)} listed, {dollars(facts.other_debt_monthly_payment)} a month)"
        if facts.other_debt
        else "your other debts (none listed)"
    )
    verified = ""
    if facts.verified_fields:
        verified = f", and the figures you marked as verified ({', '.join(facts.verified_fields)})"
    return (
        f"Unchanged: your credit score, your experience, {debt}, the site's demand estimate, every assumption range "
        f"and the note rate{verified}."
    )


def status_message(status: str, n_found: int, budget_exhausted: bool = False, has_closest: bool = True) -> str:
    """The one-line answer at the top of the result.

    "No path" never claims more than the search did: it is a heuristic over a grid of values (pairs are screened),
    so the message says what was tried, and a search that ran out of budget says it stopped early.
    """
    if status == "already_meets_target":
        return (
            "Your plan already meets the three targets (simulated, based on stated assumptions), so the search "
            "suggests no change. " + NOT_AN_APPROVAL
        )
    if status == "paths_found":
        ways = "one change-set" if n_found == 1 else f"{n_found} change-sets"
        order = "" if n_found == 1 else ", smallest first"
        return (
            f"The search found {ways} of your plan choices that would reach the targets (simulated, based on stated "
            f"assumptions){order}. " + NOT_AN_APPROVAL
        )
    closest = (
        "The closest result is shown."
        if has_closest
        else "No change the search tried moved the plan closer to the targets."
    )
    if budget_exhausted:
        return (
            "No path within your bounds found yet: the search stopped early because it reached its budget of "
            "evaluated plan variants before trying every change it normally tries (simulated, based on stated "
            f"assumptions). {closest} A search with a larger budget, or with fewer levers, may still find one."
        )
    return (
        "No path within your bounds: the search did not find a change of one or two plan choices inside your bounds "
        "that reaches all three targets (simulated, based on stated assumptions). It tries a grid of values and "
        "screens pairs, so it is not exhaustive: a combination it did not try could still reach them. "
        f"{closest} A wider bound (only where you could really go further) or a different plan (site, size, concept) "
        "may change the answer."
    )
