"""The monthly path model: random draws in, monthly P&L and cash per path out (NumPy, vectorized over paths).

One path, month t = 0, 1, ... (t = 0 is the opening month):

    demand(t)     = mature demand x ramp(t) x seasonality(calendar month) x (price / reference price)^(-elasticity)
                    x (1 - competitor loss, from the month a competitor opens) x noise(t)
    volume(t)     = min(demand(t), capacity) x open days in the month      (per-day units)
                  = min(demand(t), capacity)                               (per-month units, e.g. booked nights)
    revenue(t)    = volume(t) x price            (minus the lost share in the month of a one-off event)
    NOI(t)        = revenue(t) x (1 - sum of cost ratios) - hours x wage x (1 + payroll burden)
                    - rent x (1 + escalation)^(whole years open) - repair bill (month of a one-off event)
    debt(t)       = the loan payment (loan.py; re-amortized after a rate shock on a variable-rate loan)
    net income(t) = NOI(t) - debt(t)
    cash(t)       = equity + loan - capex x (1 + overrun) + sum of (net income - owner draw) up to t

ramp(t) = starting share + (1 - starting share) x min(1, t / months to maturity) (linear, a design choice).
noise(t) = exp(sd x z - sd^2 / 2) with z standard normal, so its mean is 1.
Each shock happens at most once per path; its month comes from distributions.event_month.

NOI follows the startup program's definition (NSP-1.2): revenue minus operating expenses, before debt service,
depreciation and owner distributions. Income taxes are not modeled.
"""

from dataclasses import dataclass, field

import numpy as np

from uwcopilot.studio.feasibility.distributions import event_month
from uwcopilot.studio.feasibility.loan import payment_matrix
from uwcopilot.studio.feasibility.setup import SimulationSetup

DAYS_PER_MONTH = 365.25 / 12.0
MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class StressLevers:
    """Levers applied on top of the sampled paths (the lender stress preset uses these; stress.py)."""

    name: str = "base"
    revenue_drop_share: float = 0.0  # revenue falls by this share and NOI by the same dollars (NBC-4.5)
    rate_add_pct: float = 0.0  # points added to the note rate for the whole loan


BASE_CASE = StressLevers()


@dataclass(frozen=True)
class Draws:
    """Every random number of a simulation, drawn once so stress runs and the tornado can reuse them.

    uniforms: one uniform per path for each input distribution (inverse-CDF sampling, distributions.py).
    entry_u, rate_shock_u, one_off_u: one uniform per path for the month of each shock.
    noise_z: standard normals, paths x months, for month-to-month demand noise.
    extra_demand_multiplier: ONLY for validation truths (validation.py): effects the simulator does not model.
    """

    uniforms: dict[str, np.ndarray]
    entry_u: np.ndarray
    rate_shock_u: np.ndarray
    one_off_u: np.ndarray
    noise_z: np.ndarray
    extra_demand_multiplier: np.ndarray | None = field(default=None)

    @property
    def n_paths(self) -> int:
        """Number of paths."""
        return int(self.entry_u.shape[0])


def draw(setup: SimulationSetup, n_paths: int, rng: np.random.Generator) -> Draws:
    """Draw every random number for n_paths paths, always in the same order (so a seed reproduces a run)."""
    uniforms = {name: rng.random(n_paths) for name in setup.distributions}
    entry_u = rng.random(n_paths)
    rate_shock_u = rng.random(n_paths)
    one_off_u = rng.random(n_paths)
    noise_z = rng.standard_normal((n_paths, setup.months))
    return Draws(uniforms, entry_u, rate_shock_u, one_off_u, noise_z)


def input_values(setup: SimulationSetup, draws: Draws) -> dict[str, np.ndarray]:
    """{input name: one value per path}: each distribution's quantile at the path's uniform."""
    return {name: item.quantile(draws.uniforms[name]) for name, item in setup.distributions.items()}


@dataclass(frozen=True)
class PathResults:
    """Monthly results per path (arrays are paths x months, USD unless stated)."""

    volume: np.ndarray  # demand units served per month
    revenue: np.ndarray
    noi: np.ndarray
    debt_service: np.ndarray
    net_income: np.ndarray
    cash: np.ndarray
    starting_cash: np.ndarray  # per path, after the build-out overrun
    competitor_entry_month: np.ndarray  # 0-based month, -1 = none
    rate_shock_month: np.ndarray
    one_off_month: np.ndarray

    @property
    def n_paths(self) -> int:
        """Number of paths."""
        return int(self.revenue.shape[0])


def demand_matrix(setup: SimulationSetup, values: dict[str, np.ndarray], draws: Draws) -> tuple[np.ndarray, np.ndarray]:
    """(demand per path and month in the profile's unit, competitor entry month per path)."""
    months = np.arange(setup.months)
    calendar = (setup.opening_date.month - 1 + months) % MONTHS_PER_YEAR
    season = np.asarray(setup.seasonality)[calendar]
    start = values["ramp_starting_share"][:, None]
    maturity = values["ramp_months_to_maturity"][:, None]
    ramp = start + (1.0 - start) * np.minimum(1.0, months[None, :] / maturity)
    price_effect = (values["price"] / setup.reference_price) ** (-values["price_elasticity"])
    entry = event_month(draws.entry_u, values["competitor_entry_annual_probability"], setup.months)
    after_entry = (entry[:, None] >= 0) & (months[None, :] >= entry[:, None])
    competition = np.where(after_entry, 1.0 - values["competitor_entry_demand_loss"][:, None], 1.0)
    sd = values["monthly_noise_sd"][:, None]
    noise = np.exp(sd * draws.noise_z - 0.5 * sd**2)
    demand = values["mature_demand"][:, None] * ramp * season[None, :] * price_effect[:, None] * competition * noise
    if draws.extra_demand_multiplier is not None:
        demand = demand * draws.extra_demand_multiplier
    return demand, entry


def run_paths(
    setup: SimulationSetup, values: dict[str, np.ndarray], draws: Draws, levers: StressLevers = BASE_CASE
) -> PathResults:
    """Monthly P&L and cash for every path (see the module docstring for the formulas)."""
    months = np.arange(setup.months)
    demand, entry = demand_matrix(setup, values, draws)
    served = np.minimum(demand, values["capacity"][:, None])
    volume = served * (values["days_open_per_week"][:, None] / 7.0 * DAYS_PER_MONTH) if setup.per_day else served
    revenue = volume * values["price"][:, None]

    one_off = event_month(draws.one_off_u, values["one_off_shock_annual_probability"], setup.months)
    hit = months[None, :] == one_off[:, None]
    revenue = np.where(hit, revenue * (1.0 - values["one_off_shock_revenue_loss_share"][:, None]), revenue)
    repair = np.where(hit, values["one_off_shock_repair_cost_share_of_capex"][:, None] * setup.planned_capex, 0.0)

    cost_share = sum(values[f"cost_ratio_{name}"] for name in setup.cost_ratio_names)
    labor = values["paid_hours_per_month"] * values["hourly_wage"] * (1.0 + values["payroll_burden_share"])
    years_open = months // MONTHS_PER_YEAR
    rent = values["monthly_rent"][:, None] * (1.0 + values["rent_escalation"][:, None]) ** years_open[None, :]
    noi = revenue * (1.0 - cost_share[:, None]) - labor[:, None] - rent - repair

    if levers.revenue_drop_share:
        dropped = levers.revenue_drop_share * revenue
        revenue = revenue - dropped
        noi = noi - dropped

    rate_shock = np.full(draws.n_paths, -1, dtype=int)
    shock_size = np.zeros(draws.n_paths)
    if "rate_shock_annual_probability" in values:
        rate_shock = event_month(draws.rate_shock_u, values["rate_shock_annual_probability"], setup.months)
        shock_size = values["rate_shock_size_pct_points"]
    loan = setup.loan
    debt = payment_matrix(
        loan.amount, loan.annual_rate_pct + levers.rate_add_pct, loan.term_months, setup.months, rate_shock, shock_size
    )
    net_income = noi - debt
    starting_cash = setup.owner_equity + loan.amount - setup.planned_capex * (1.0 + values["capex_overrun_share"])
    cash = starting_cash[:, None] + np.cumsum(net_income - setup.owner_draw, axis=1)
    return PathResults(volume, revenue, noi, debt, net_income, cash, starting_cash, entry, rate_shock, one_off)


def single_point_values(setup: SimulationSetup) -> dict[str, np.ndarray]:
    """The 'one forecast' case: every input at its most likely value, shock probabilities 0, no monthly noise."""
    values: dict[str, np.ndarray] = {}
    for name, item in setup.distributions.items():
        quiet = name.endswith("_annual_probability") or name == "monthly_noise_sd"
        values[name] = np.array([0.0 if quiet else item.mode])
    return values


def single_point_run(setup: SimulationSetup, values: dict[str, np.ndarray] | None = None) -> PathResults:
    """Run the 'one forecast' case as a single path (the plan a spreadsheet would show)."""
    half = np.array([0.5])
    draws = Draws(
        uniforms={name: half for name in setup.distributions},
        entry_u=half,
        rate_shock_u=half,
        one_off_u=half,
        noise_z=np.zeros((1, setup.months)),
    )
    return run_paths(setup, values if values is not None else single_point_values(setup), draws)


def simulate_paths(setup: SimulationSetup, n_paths: int, seed: int) -> tuple[Draws, dict[str, np.ndarray], PathResults]:
    """Draw (seeded), sample the inputs and run the base case."""
    draws = draw(setup, n_paths, np.random.default_rng(seed))
    values = input_values(setup, draws)
    return draws, values, run_paths(setup, values, draws)
