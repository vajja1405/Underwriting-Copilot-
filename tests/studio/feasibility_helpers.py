"""Shared builders for the feasibility tests. Every input is SYNTHETIC; the metro is the generated SYNTHETIC city."""

from datetime import date
from typing import Any

from uwcopilot.studio.data.fabric import get_fabric
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility import Loan, Plan, RangeOverride, SiteDemand, load_assumptions
from uwcopilot.studio.feasibility.setup import SimulationSetup, build_setup
from uwcopilot.studio.profiles import Profile, load_profile

METRO = "synthetic_metro"
# Hand-worked values for the deterministic car-wash setup (SYNTHETIC test inputs, not benchmarks).
COST_RATIOS = {
    "cogs": 0.02,
    "utilities": 0.09,
    "supplies": 0.08,
    "marketing": 0.03,
    "insurance": 0.02,
    "maintenance": 0.06,
}


def car_wash_site(**changes: Any) -> SiteDemand:
    """A SYNTHETIC car-wash demand range."""
    fields: dict[str, Any] = {
        "low": 150.0,
        "mode": 250.0,
        "high": 400.0,
        "unit": "cars/day",
        "status": "SYNTHETIC",
        "source": "test range (SYNTHETIC)",
        "competition_percentile": 0.5,
    }
    fields.update(changes)
    return SiteDemand(**fields)


def basic_plan(**changes: Any) -> Plan:
    """A SYNTHETIC car-wash plan."""
    fields: dict[str, Any] = {"opening_date": date(2027, 3, 1), "owner_equity": 700_000.0, "capex": 1_800_000.0}
    fields.update(changes)
    return Plan(**fields)


def basic_loan(**changes: Any) -> Loan:
    """A SYNTHETIC loan (rate from the SYNTHETIC prime rate plus the assumed spread unless given)."""
    fields: dict[str, Any] = {"amount": 1_300_000.0, "term_months": 120}
    fields.update(changes)
    return Loan(**fields)


def synthetic_inputs(profile: Profile) -> dict[str, FactorValue]:
    """The SYNTHETIC metro's inputs (rent range, wages, prime rate) for a profile."""
    return get_fabric(METRO).metro_inputs(profile)


def setup_for(
    profile_id: str = "car_wash",
    site: SiteDemand | None = None,
    plan: Plan | None = None,
    loan: Loan | None = None,
    months: int = 36,
    metro_inputs: dict[str, FactorValue] | None = None,
) -> SimulationSetup:
    """A setup on the SYNTHETIC metro with the basic test inputs."""
    profile = load_profile(profile_id)
    return build_setup(
        profile,
        METRO,
        "SYNTHETIC",
        metro_inputs if metro_inputs is not None else synthetic_inputs(profile),
        site or car_wash_site(),
        plan or basic_plan(),
        loan or basic_loan(),
        load_assumptions(),
        months,
    )


def pinned(value: float) -> RangeOverride:
    """An edited range that pins an input to one value (for hand-worked tests)."""
    return RangeOverride(low=value, mode=value, high=value, note="pinned for a hand-worked test")


def deterministic_overrides(**changes: float) -> dict[str, RangeOverride]:
    """Overrides that pin every sampled car-wash input, with no shocks and no noise."""
    values = {
        "ramp_starting_share": 0.5,
        "ramp_months_to_maturity": 10.0,
        "payroll_burden_share": 0.1,
        "rent_escalation": 0.03,
        "price_elasticity": 0.8,
        "monthly_noise_sd": 0.0,
        "capex_overrun_share": 0.1,
        "competitor_entry_annual_probability": 0.0,
        "competitor_entry_demand_loss": 0.15,
        "rate_shock_annual_probability": 0.0,
        "rate_shock_size_pct_points": 1.0,
        "one_off_shock_annual_probability": 0.0,
        "one_off_shock_revenue_loss_share": 0.3,
        "one_off_shock_repair_cost_share_of_capex": 0.01,
        **{f"cost_ratio_{name}": ratio for name, ratio in COST_RATIOS.items()},
    }
    values.update(changes)
    return {name: pinned(value) for name, value in values.items()}


def deterministic_setup(loan: Loan | None = None, **overrides: float) -> SimulationSetup:
    """A car-wash setup where every input is a single value (demand 200 cars/day, price at the profile mode)."""
    plan = basic_plan(
        opening_date=date(2027, 1, 1),
        price=14.0,
        capacity=1000.0,
        days_open_per_week=7.0,
        monthly_rent=9000.0,
        paid_hours_per_month=1000.0,
        hourly_wage=16.0,
        owner_draw_per_month=500.0,
        assumption_overrides=deterministic_overrides(**overrides),
    )
    site = car_wash_site(low=200.0, mode=200.0, high=200.0)
    return setup_for(site=site, plan=plan, loan=loan or basic_loan(annual_rate_pct=9.0))
