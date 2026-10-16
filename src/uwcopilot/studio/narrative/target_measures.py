"""Which simulated probability answers each Studio target, so a check never compares a target with a probability
that was measured for another threshold.

The founder may edit the targets (the web form's TargetsIn -> path_to_approval.custom_target), and the edited target
travels in PathResult.target. The two engines measure different things:
- the S3 simulation (SIM.*) measures P(year-2 DSCR >= the simulation's own DSCR target), P(year-2 DSCR >= the NSP-4.2
  minimum under the NSP-4.2 revenue stress) and P(cash < 0 within 24 months);
- the S5 path search (PATH.before.*) measures the same three for exactly the target it was given.
For each of the three targets the check uses:
1. the SIM.* claim when the target asks what the simulation measured (always true for the default targets);
2. else the PATH.before.* claim when the ledger holds a path search (it was run for this target);
3. else an UNKNOWN claim (TARGET.measured.<name>) that says why; the check is then not evaluated.
"""

import math
from dataclasses import dataclass

from uwcopilot.evidence.ledger_models import Claim, unknown_claim
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.path_to_approval.models import Target

SIMULATED_CASH_MONTHS = 24
REVENUE_STRESS = "revenue_stress"
NOT_MEASURED_REASON = (
    "the simulation measured this chance for {measured}, the target asks for {asked}, and no path search for this "
    "target is in the ledger"
)


@dataclass(frozen=True)
class TargetMeasures:
    """The probability claim each target's check compares (dscr, stress, cash), and any UNKNOWN claims made."""

    dscr: str
    stress: str
    cash: str
    unknown: tuple[Claim, ...] = ()


def same(first: float, second: float) -> bool:
    """True when two thresholds are equal up to floating-point noise."""
    return math.isclose(first, second, rel_tol=0.0, abs_tol=1e-9)


def simulated_stress(target: Target, result: FeasibilityResult) -> bool:
    """True when the simulation's revenue stress uses the target's revenue cut and stressed minimum."""
    for outcome in result.stress:
        if outcome.name != REVENUE_STRESS or not outcome.applies or outcome.p_dscr_year2_at_least_min is None:
            continue
        return same(outcome.revenue_drop_share, target.stress_revenue_share) and same(
            outcome.min_dscr, target.stressed_dscr_min
        )
    return False


def choose(
    name: str,
    simulated_id: str | None,
    path_id: str,
    claims: dict[str, Claim],
    measured: str,
    asked: str,
) -> tuple[str, Claim | None]:
    """(claim id the check compares, a new UNKNOWN claim or None) for one target (module docstring, steps 1-3)."""
    if simulated_id is not None and simulated_id in claims:
        return simulated_id, None
    if path_id in claims:
        return path_id, None
    claim_id = ids.safe_id("TARGET", "measured", name)
    reason = NOT_MEASURED_REASON.format(measured=measured, asked=asked)
    label = f"Simulated chance for the {name} target as edited"
    return claim_id, unknown_claim(claim_id, label, "PERCENT", "pct1", reason, kind="model", ref=claim_id)


def target_measures(target: Target, result: FeasibilityResult, claims: dict[str, Claim]) -> TargetMeasures:
    """Pick the probability claim of each target (module docstring). claims: every claim built so far, by id."""
    dscr_matches = same(target.dscr_min, result.dscr_target.value)
    stress_matches = simulated_stress(target, result)
    cash_matches = target.cash_months == SIMULATED_CASH_MONTHS
    dscr = choose(
        "dscr",
        ids.SIM_P_DSCR_Y2 if dscr_matches else None,
        ids.PATH_BEFORE_P_DSCR,
        claims,
        f"a DSCR of {result.dscr_target.value:g}",
        f"{target.dscr_min:g}",
    )
    stress = choose(
        "stress",
        ids.SIM_P_STRESSED_Y2 if stress_matches else None,
        ids.PATH_BEFORE_P_STRESSED,
        claims,
        "the program's revenue stress",
        f"a {target.stress_revenue_share:g} revenue cut and a stressed DSCR of {target.stressed_dscr_min:g}",
    )
    cash = choose(
        "cash",
        ids.SIM_P_CASH if cash_matches else None,
        ids.PATH_BEFORE_P_CASH,
        claims,
        f"{SIMULATED_CASH_MONTHS} months",
        f"{target.cash_months} months",
    )
    unknown = tuple(claim for _, claim in (dscr, stress, cash) if claim is not None)
    return TargetMeasures(dscr=dscr[0], stress=stress[0], cash=cash[0], unknown=unknown)
