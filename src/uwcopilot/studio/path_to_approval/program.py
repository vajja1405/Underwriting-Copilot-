"""Startup-program checks that a change-set must not break or make worse (Studio S5).

The three targets are simulated probabilities. A change can reach them and still break a plain rule of the SYNTHETIC
startup program: borrowing more lowers the owner's share of the project, and a longer term can leave the program's
range. The checks:

- NSP-3.1 equity injection: owner cash equity / total project cost >= 20.0% (program_pct: one decimal, half up);
- NSP-8.1 product limits: loan amount within the startup term loan's limits;
- NSP-8.1 product limits: loan term within the startup term loan's range.

The search refuses a change-set (refusals) that
1. breaks a check the plan meets now (newly_broken), or
2. moves the plan further from a check it already fails (moved_further): a loan already above the maximum may not
   grow, a loan already below the minimum may not shrink, an equity share already below 20.0% may not fall, and a
   term already outside the range may not move further out.
A change-set may leave a check failed that the plan already fails (the search does not try to repair it), but then
the change-set says so (ChangeSet.program_rules_not_met and its explanation), and the evaluation reports the paths
that leave every check met separately.

The thresholds are imported from agent/policy_startup.py, never retyped. These checks are information for the
founder: Studio never approves or declines (rule C), and the lender runs every rule itself.
"""

from uwcopilot.agent.policy_startup import (
    MIN_EQUITY_INJECTION_PCT,
    STARTUP_MAX_AMOUNT,
    STARTUP_MAX_TERM_MONTHS,
    STARTUP_MIN_AMOUNT,
    STARTUP_MIN_TERM_MONTHS,
    program_pct,
)
from uwcopilot.studio.path_to_approval.models import PlanAmounts, ProgramChecks

CHECK_NAMES: tuple[str, ...] = ("meets_equity_minimum", "loan_within_limits", "term_within_limits")
CHECK_TEXT: dict[str, str] = {
    "meets_equity_minimum": f"equity injection of at least {MIN_EQUITY_INJECTION_PCT:.1f}% (NSP-3.1)",
    "loan_within_limits": f"loan amount from ${STARTUP_MIN_AMOUNT:,} to ${STARTUP_MAX_AMOUNT:,} (NSP-8.1)",
    "term_within_limits": f"loan term from {STARTUP_MIN_TERM_MONTHS} to {STARTUP_MAX_TERM_MONTHS} months (NSP-8.1)",
}


def program_checks(owner_equity: float, project_cost: float, loan_amount: float, term_months: int) -> ProgramChecks:
    """The three checks for one plan's money and terms (project cost = build-out + cash reserve = all sources)."""
    share = program_pct(owner_equity, project_cost) if project_cost > 0 else 0.0
    return ProgramChecks(
        equity_injection_pct=share,
        equity_minimum_pct=MIN_EQUITY_INJECTION_PCT,
        meets_equity_minimum=share >= MIN_EQUITY_INJECTION_PCT,
        loan_within_limits=STARTUP_MIN_AMOUNT <= loan_amount <= STARTUP_MAX_AMOUNT,
        term_within_limits=STARTUP_MIN_TERM_MONTHS <= term_months <= STARTUP_MAX_TERM_MONTHS,
    )


def newly_broken(before: ProgramChecks, after: ProgramChecks) -> list[str]:
    """The checks the plan met before and would fail after the change (CHECK_NAMES order)."""
    return [name for name in CHECK_NAMES if getattr(before, name) and not getattr(after, name)]


def further_outside(before: float, after: float, low: float, high: float) -> bool:
    """True when a value already outside [low, high] moves further away from the range."""
    if before > high:
        return after > before
    if before < low:
        return after < before
    return False


def moved_further(before: PlanAmounts, after: PlanAmounts) -> list[str]:
    """The checks the plan already fails and the change would move further from (CHECK_NAMES order)."""
    worse = []
    if not before.checks.meets_equity_minimum:
        if after.checks.equity_injection_pct < before.checks.equity_injection_pct:
            worse.append("meets_equity_minimum")
    if not before.checks.loan_within_limits:
        if further_outside(before.loan_amount, after.loan_amount, STARTUP_MIN_AMOUNT, STARTUP_MAX_AMOUNT):
            worse.append("loan_within_limits")
    if not before.checks.term_within_limits:
        term_before, term_after = before.loan_term_months, after.loan_term_months
        if further_outside(term_before, term_after, STARTUP_MIN_TERM_MONTHS, STARTUP_MAX_TERM_MONTHS):
            worse.append("term_within_limits")
    return worse


def refusals(before: PlanAmounts, after: PlanAmounts) -> list[str]:
    """Why the search refuses a change-set: checks it newly breaks, then checks it moves further from."""
    return newly_broken(before.checks, after.checks) + moved_further(before, after)


def failed_checks(checks: ProgramChecks) -> list[str]:
    """The checks a plan fails, in words."""
    return [CHECK_TEXT[name] for name in CHECK_NAMES if not getattr(checks, name)]
