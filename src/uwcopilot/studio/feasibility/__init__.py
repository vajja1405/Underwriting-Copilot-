"""Site-level Monte Carlo feasibility (Founder Studio S3).

simulate() runs n possible futures (default 1,000 paths x 36 months, seeded) of one business at one site with one
plan and loan, and reports P(DSCR >= target) in years 1 and 2, P(cash < 0 within 24 months), the break-even month
distribution, P10/P50/P90 revenue, net income and cash per month, a tornado of the assumptions that matter most
and the lender stress preset. Every probability is "simulated, based on stated assumptions" (Studio rule E).

Modules: inputs (SiteDemand, Plan, Loan), assumptions (simulator assumptions and the DSCR target), distributions,
setup (where every input comes from), loan, model (the monthly path model), metrics, tornado, stress, result,
simulate, projection (handoff to the lender's projection schema), worlds and validation (synthetic-world checks).
"""

from uwcopilot.studio.feasibility.assumptions import DscrTarget, default_dscr_target, load_assumptions
from uwcopilot.studio.feasibility.inputs import Loan, Plan, RangeOverride, SiteDemand, as_site_demand
from uwcopilot.studio.feasibility.result import SIMULATOR_VERSION, FeasibilityResult
from uwcopilot.studio.feasibility.simulate import run_simulation, simulate
from uwcopilot.studio.feasibility.stress import LENDER_STRESS

__all__ = [
    "LENDER_STRESS",
    "SIMULATOR_VERSION",
    "DscrTarget",
    "FeasibilityResult",
    "Loan",
    "Plan",
    "RangeOverride",
    "SiteDemand",
    "as_site_demand",
    "default_dscr_target",
    "load_assumptions",
    "run_simulation",
    "simulate",
]
