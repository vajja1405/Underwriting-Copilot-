"""Hand a simulation to the lender side in the lender's own schema (agent/applications.py).

The SYNTHETIC startup program (corpus/programs/northwind_startup_program.md) asks for a 36-month projection
summarized per year as P10 / P50 / P90 revenue and NOI (NSP-1.2, ProjectionYear) and records two simulation outputs
as information only (NSP-4.4, SimulationOutputs). Building those objects here, with the lender's own validators,
means a founder package (S6) can never carry a projection the lender copilot would reject as malformed.
"""

from uwcopilot.agent.applications import ProjectionYear, SimulationOutputs
from uwcopilot.studio.feasibility.result import FeasibilityResult

PROJECTION_YEARS = 3


def projection_years(result: FeasibilityResult) -> list[ProjectionYear]:
    """Years 1, 2 and 3 as the lender's ProjectionYear (needs a simulation of at least 36 months)."""
    if len(result.yearly) < PROJECTION_YEARS:
        raise ValueError(f"the lender's projection needs {PROJECTION_YEARS} years; simulate at least 36 months")
    return [
        ProjectionYear(
            year=band.year,
            revenue_p10=band.revenue_p10,
            revenue_p50=band.revenue_p50,
            revenue_p90=band.revenue_p90,
            noi_p10=band.noi_p10,
            noi_p50=band.noi_p50,
            noi_p90=band.noi_p90,
        )
        for band in result.yearly[:PROJECTION_YEARS]
    ]


def simulation_outputs(result: FeasibilityResult) -> SimulationOutputs:
    """The NSP-4.4 information-only outputs: P(year-2 DSCR >= target) and P(cash < 0 within 24 months)."""
    return SimulationOutputs(
        simulator_version=result.simulator_version,
        n_paths=result.n_paths,
        seed=result.seed,
        dscr_target=result.dscr_target.value,
        p_dscr_ge_target_year2=result.p_dscr_at_least_target["year_2"],
        p_cash_negative_24m=result.p_cash_negative_within_24_months,
    )
