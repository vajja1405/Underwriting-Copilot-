"""The simulator's output models (pydantic), so the API, the package (S6) and the ledger (S4) read one shape.

Every probability in a FeasibilityResult is labeled "simulated, based on stated assumptions" (Studio rule E) and every
result carries the Studio disclaimer (rule C). The full per-path arrays are not part of the result (they are large);
simulate.run_simulation returns them next to the result for code that needs them.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.feasibility.assumptions import DscrTarget
from uwcopilot.studio.feasibility.distributions import Distribution, InputStatus
from uwcopilot.studio.feasibility.inputs import SiteDemand

SIMULATOR_VERSION = "studio-feasibility-1.0"


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MonthBand(Frozen):
    """P10 / P50 / P90 across paths for one month (USD)."""

    month: int = Field(ge=1, description="1 = the opening month")
    calendar_month: str = Field(description="YYYY-MM")
    revenue_p10: float
    revenue_p50: float
    revenue_p90: float
    net_income_p10: float
    net_income_p50: float
    net_income_p90: float
    cash_p10: float
    cash_p50: float
    cash_p90: float


class YearBand(Frozen):
    """P10 / P50 / P90 across paths of one year's totals (USD) and of its DSCR."""

    year: int = Field(ge=1)
    revenue_p10: float
    revenue_p50: float
    revenue_p90: float
    noi_p10: float
    noi_p50: float
    noi_p90: float
    net_income_p10: float
    net_income_p50: float
    net_income_p90: float
    debt_service_p50: float
    dscr_p10: float | None = Field(description="None for a year without 12 scheduled loan payments")
    dscr_p50: float | None
    dscr_p90: float | None


class BreakEven(Frozen):
    """When monthly net income turns non-negative for BREAK_EVEN_RUN months in a row (metrics.py)."""

    definition: str
    p10_month: int | None = Field(description="None when fewer than 10% of paths break even in the horizon")
    p50_month: int | None
    p90_month: int | None
    share_not_within_horizon: float = Field(ge=0, le=1)
    paths_by_month: dict[str, int] = Field(description="{month number or 'not within horizon': number of paths}")


class LoanTerms(Frozen):
    """The loan as simulated, with where the note rate came from."""

    amount: float
    term_months: int
    annual_rate_pct: float
    rate_type: Literal["fixed", "variable"]
    rate_status: InputStatus
    rate_source: str
    monthly_payment: float
    annual_debt_service: float = Field(description="12 level payments (NBC-4.1 / NSP-1.2), before any rate shock")


class TornadoBar(Frozen):
    """One input swung between its P10 and P90 with every other draw held fixed."""

    rank: int = Field(ge=1)
    name: str
    description: str
    unit: str
    status: InputStatus
    input_p10: float
    input_p90: float
    metric_at_p10: float
    metric_at_p90: float
    swing: float = Field(ge=0)
    p_dscr_year2_at_p10: float
    p_dscr_year2_at_p90: float


class Tornado(Frozen):
    """The inputs that move the outcome most, largest swing first."""

    metric: str
    method: str
    base_metric: float
    base_p_dscr_year2: float
    bars: list[TornadoBar]
    top_drivers: list[str] = Field(description="The three inputs with the largest swing (NSP-6.1 'top drivers')")


class StressOutcome(Frozen):
    """One lender stress test, simulated over every path and computed the lender's way on the base case."""

    name: str
    description: str
    applies: bool
    reason: str
    revenue_drop_share: float
    rate_add_pct: float
    min_dscr: float = Field(description="The guideline's stress threshold for this test")
    source: str
    p_dscr_year2_at_least_min: float | None = Field(
        description=f"{SIMULATION_LABEL}; None when the test does not apply"
    )
    base_case_dscr: float | None = Field(description="Year-2 P50 formula of NSP-4.2, as the lender computes it")
    base_case_meets_min: bool | None


class FeasibilityResult(Frozen):
    """Everything the founder sees for one site and plan. Probabilities are simulation estimates, not forecasts."""

    label: Literal["simulated, based on stated assumptions"] = SIMULATION_LABEL
    disclaimer: str = DISCLAIMER
    simulator_version: str = SIMULATOR_VERSION
    data_label: str
    metro_id: str
    metro_mode: str
    metro_mode_reason: str
    profile_id: str
    demand_unit: str
    n_paths: int
    months: int
    seed: int
    opening_date: date
    site_demand: SiteDemand
    loan: LoanTerms
    planned_capex: float
    capex_status: InputStatus
    owner_equity: float
    planned_starting_cash: float
    dscr_target: DscrTarget
    p_dscr_at_least_target: dict[str, float] = Field(
        description="{'year_1': p, 'year_2': p, ...} for every simulated year with 12 scheduled loan payments"
    )
    p_cash_negative_within_24_months: float
    break_even: BreakEven
    monthly: list[MonthBand]
    yearly: list[YearBand]
    distributions: list[Distribution]
    seasonality: list[float]
    seasonality_source: str
    shocks: list[str] = Field(description="How each shock is modeled (at most once per path)")
    projected_dscr_base_case: float | None = Field(
        description="Year-2 P50 NOI / annual debt service: the NSP-4.1 formula the lender applies to the projection"
    )
    tornado: Tornado | None = None
    stress: list[StressOutcome] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
