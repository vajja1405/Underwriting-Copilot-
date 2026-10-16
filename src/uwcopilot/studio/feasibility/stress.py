"""The "lender stress" preset: the founder sees the same tests the lender copilot will run.

The levers and thresholds are imported from agent/policy_credit.py (SYNTHETIC Northwind guideline NBC-4.5, applied to
startup loans by NSP-4.2 of the SYNTHETIC startup program), never retyped:
- revenue stress: revenue lower by REVENUE_STRESS_SHARE and NOI lower by the same dollars (costs do not shrink),
  compared with REVENUE_STRESS_MIN_DSCR;
- rate stress (variable-rate loans only): RATE_STRESS_ADD_PCT points on the note rate, compared with
  RATE_STRESS_MIN_DSCR.

Each test is reported two ways:
1. simulated: the share of paths whose year-2 DSCR meets the stress threshold with the lever applied to every
   month of every path (same draws as the base run);
2. the lender's way: the NSP-4.2 formula on the year-2 P50 (base case), computed with the startup program's own
   arithmetic (policy_startup.program_ratio and revenue_stressed_noi: exact decimals, rounded half up), so the
   founder's number and the lender's number agree to the cent. The simulator has no other debt (seller notes), so
   the projected annual debt service is the startup loan's alone.
Studio explains these tests; it never approves or declines (Studio rule C).
"""

from dataclasses import dataclass

import numpy as np

from uwcopilot.agent.policy_credit import (
    RATE_STRESS_ADD_PCT,
    RATE_STRESS_MIN_DSCR,
    REVENUE_STRESS_MIN_DSCR,
    REVENUE_STRESS_SHARE,
)
from uwcopilot.agent.policy_startup import program_ratio, revenue_stressed_noi
from uwcopilot.studio.feasibility.loan import annual_debt_service
from uwcopilot.studio.feasibility.metrics import dscr_by_year, share_at_least
from uwcopilot.studio.feasibility.model import Draws, StressLevers, run_paths
from uwcopilot.studio.feasibility.result import StressOutcome, YearBand
from uwcopilot.studio.feasibility.setup import SimulationSetup

STRESS_SOURCE = (
    "SYNTHETIC Northwind guideline NBC-4.5 (applied to startup loans by NSP-4.2 of the SYNTHETIC startup program); "
    "levers and thresholds imported from agent/policy_credit.py"
)
STRESS_YEAR = 2


@dataclass(frozen=True)
class StressTest:
    """One lender stress test: its levers, its threshold and whether it needs a variable rate."""

    name: str
    description: str
    levers: StressLevers
    min_dscr: float
    variable_rate_only: bool


LENDER_STRESS: tuple[StressTest, ...] = (
    StressTest(
        name="revenue_stress",
        description=f"Revenue {REVENUE_STRESS_SHARE:.0%} lower, NOI lower by the same dollars",
        levers=StressLevers("revenue_stress", revenue_drop_share=REVENUE_STRESS_SHARE),
        min_dscr=REVENUE_STRESS_MIN_DSCR,
        variable_rate_only=False,
    ),
    StressTest(
        name="rate_stress",
        description=f"Note rate {RATE_STRESS_ADD_PCT:.2f} points higher for the whole loan",
        levers=StressLevers("rate_stress", rate_add_pct=RATE_STRESS_ADD_PCT),
        min_dscr=RATE_STRESS_MIN_DSCR,
        variable_rate_only=True,
    ),
)


def lender_annual_debt_service(setup: SimulationSetup, rate_add_pct: float = 0.0) -> float:
    """12 level payments at the note rate (plus any stress points), to the cent, as policy_startup.py rounds it."""
    loan = setup.loan
    return round(annual_debt_service(loan.amount, loan.annual_rate_pct + rate_add_pct, loan.term_months), 2)


def projected_dscr(setup: SimulationSetup, year: YearBand) -> float | None:
    """NSP-4.1: year-2 P50 NOI / projected annual debt service (the program's half-up rounding)."""
    return program_ratio(year.noi_p50, lender_annual_debt_service(setup))


def base_case_stressed_dscr(test: StressTest, setup: SimulationSetup, year: YearBand) -> float | None:
    """The NSP-4.2 formula on the year-2 P50, the way the lender computes it (policy_startup.py)."""
    if test.levers.revenue_drop_share:
        stressed_noi = revenue_stressed_noi(year.noi_p50, year.revenue_p50)
        return program_ratio(stressed_noi, lender_annual_debt_service(setup))
    return program_ratio(year.noi_p50, lender_annual_debt_service(setup, test.levers.rate_add_pct))


def run_stress_test(
    test: StressTest, setup: SimulationSetup, values: dict[str, np.ndarray], draws: Draws, year: YearBand
) -> StressOutcome:
    """Run one stress test (simulated and the lender's way), or say why it does not apply."""
    if test.variable_rate_only and setup.loan.rate_type != "variable":
        return StressOutcome(
            name=test.name,
            description=test.description,
            applies=False,
            reason="fixed-rate loan: the guideline applies the rate stress to variable-rate loans only",
            revenue_drop_share=test.levers.revenue_drop_share,
            rate_add_pct=test.levers.rate_add_pct,
            min_dscr=test.min_dscr,
            source=STRESS_SOURCE,
            p_dscr_year2_at_least_min=None,
            base_case_dscr=None,
            base_case_meets_min=None,
        )
    stressed = run_paths(setup, values, draws, test.levers)
    base_case = base_case_stressed_dscr(test, setup, year)
    return StressOutcome(
        name=test.name,
        description=test.description,
        applies=True,
        reason="applied to every month of every simulated path, and to the year-2 P50 the lender's way",
        revenue_drop_share=test.levers.revenue_drop_share,
        rate_add_pct=test.levers.rate_add_pct,
        min_dscr=test.min_dscr,
        source=STRESS_SOURCE,
        p_dscr_year2_at_least_min=share_at_least(dscr_by_year(stressed, STRESS_YEAR), test.min_dscr),
        base_case_dscr=base_case,
        base_case_meets_min=None if base_case is None else base_case >= test.min_dscr,
    )


def lender_stress(
    setup: SimulationSetup, values: dict[str, np.ndarray], draws: Draws, year: YearBand
) -> list[StressOutcome]:
    """Every test of the lender stress preset."""
    return [run_stress_test(test, setup, values, draws, year) for test in LENDER_STRESS]
