"""Build the simulation setup: every input the simulator samples, with its status and source, in a fixed order.

Where each input comes from (first match wins):
- a value the founder typed in the Plan or Loan -> fixed, USER_STATED;
- an edited range in Plan.assumption_overrides -> a triangle, USER_STATED;
- metro data from the data fabric (local wages, the metro rent range, the prime rate) -> DATA or SYNTHETIC;
- the business-type profile or config/studio/feasibility/assumptions.yaml -> an ASSUMPTION range.
A metro value that is UNKNOWN is never filled silently: the profile range is used and a note says so.

The order of SimulationSetup.distributions is fixed (insertion order), so the same seed always gives the same draws.
"""

from dataclasses import dataclass
from datetime import date

from uwcopilot.risk.scenario import OverrideError
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility.assumptions import FeasibilityAssumptions
from uwcopilot.studio.feasibility.distributions import (
    Distribution,
    InputStatus,
    empirical,
    fixed,
    from_factor_value,
    from_quantity,
    triangular,
    with_mode,
)
from uwcopilot.studio.feasibility.inputs import Loan, Plan, SiteDemand
from uwcopilot.studio.profiles.models import Profile, Quantity

MIN_MONTHS = 24  # year-2 DSCR and the 24-month cash check need at least two years
SHARE_PREFIXES: tuple[str, ...] = ("cost_ratio_",)
SHARE_SUFFIXES: tuple[str, ...] = ("_share", "_probability", "_loss")


@dataclass(frozen=True)
class LoanSetup:
    """The loan terms the simulator uses, with where the rate came from."""

    amount: float
    term_months: int
    annual_rate_pct: float
    rate_type: str
    rate_status: InputStatus
    rate_source: str


@dataclass(frozen=True)
class SimulationSetup:
    """Everything one simulation needs except the random draws."""

    profile_id: str
    metro_id: str
    data_label: str
    demand_unit: str
    per_day: bool
    months: int
    opening_date: date
    seasonality: tuple[float, ...]
    seasonality_source: str
    reference_price: float
    cost_ratio_names: tuple[str, ...]
    distributions: dict[str, Distribution]
    loan: LoanSetup
    planned_capex: float
    capex_status: InputStatus
    owner_equity: float
    owner_draw: float
    site_demand: SiteDemand
    notes: tuple[str, ...]

    @property
    def planned_starting_cash(self) -> float:
        """Cash at opening if the build-out costs exactly the plan: equity + loan - capex."""
        return self.owner_equity + self.loan.amount - self.planned_capex


def profile_input(
    name: str, description: str, user_value: float | None, quantity: Quantity, where: str, unit: str | None = None
) -> Distribution:
    """The founder's value (fixed, USER_STATED) or the profile's range."""
    if user_value is not None:
        return fixed(name, description, unit or quantity.unit, user_value, "USER_STATED", "typed by the founder")
    return from_quantity(name, description, quantity, where)


def metro_or_profile(
    name: str, description: str, factor: FactorValue | None, quantity: Quantity, where: str, notes: list[str]
) -> Distribution:
    """Metro data when the fabric has a value; otherwise the profile range, with a note saying why."""
    if factor is not None and factor.value is not None and factor.status != "UNKNOWN":
        return from_factor_value(name, description, factor)
    reason = factor.reason if factor is not None else "not provided by the data fabric"
    notes.append(f"{name}: metro value UNKNOWN ({reason}); the profile's ASSUMPTION range is used instead")
    return from_quantity(name, description, quantity, where)


def mature_demand(site: SiteDemand) -> Distribution:
    """The site's demand: SiteIQ's percentiles when given (sampled as they are), otherwise a triangle."""
    description = "Demand at the site once ramp-up is over (before seasonality)"
    if site.percentiles is not None:
        source = f"{site.source}; sampled from its percentiles 0-100 (rates drawn independently, not all at one end)"
        return empirical("mature_demand", description, site.unit, site.percentiles, site.mode, site.status, source)
    return triangular("mature_demand", description, site.unit, site.low, site.mode, site.high, site.status, site.source)


def demand_notes(site: SiteDemand) -> list[str]:
    """A note when SiteIQ's estimate is incomplete: a left-out term counts as zero, so demand is understated."""
    if site.complete:
        return []
    missing = "; ".join(site.missing) or "a term was left out"
    return [
        f"site demand is INCOMPLETE ({missing}): the left-out part counts as zero, so demand and every probability "
        "below are understated; enter your own expected demand range to replace it"
    ]


def demand_inputs(profile: Profile, site: SiteDemand, plan: Plan) -> list[Distribution]:
    """Mature demand, capacity, open days, price and its elasticity reference."""
    where = f"profile {profile.id}"
    items = [
        mature_demand(site),
        profile_input(
            "capacity",
            "Most demand the business can serve",
            plan.capacity,
            profile.capacity.max_demand,
            f"{where} capacity.max_demand",
        ),
    ]
    if profile.demand_unit.endswith("/day"):
        if profile.hours is not None:
            items.append(
                profile_input(
                    "days_open_per_week",
                    "Days open per week",
                    plan.days_open_per_week,
                    profile.hours.days_per_week,
                    f"{where} hours.days_per_week",
                )
            )
        else:
            value = plan.days_open_per_week if plan.days_open_per_week is not None else 7.0
            status: InputStatus = "USER_STATED" if plan.days_open_per_week is not None else "ASSUMPTION"
            items.append(
                fixed("days_open_per_week", "Days open per week", "days/week", value, status, f"{where} has no hours")
            )
    items.append(profile_input("price", "Price per demand unit", plan.price, profile.price, f"{where} price"))
    return items


def operating_inputs(
    profile: Profile, plan: Plan, metro_inputs: dict[str, FactorValue], notes: list[str]
) -> list[Distribution]:
    """Ramp-up, cost ratios, labor and rent."""
    where = f"profile {profile.id}"
    items = [
        from_quantity(
            "ramp_starting_share", "Share of mature demand in the opening month", profile.ramp_up.starting_share, where
        ),
        from_quantity(
            "ramp_months_to_maturity", "Months until demand reaches maturity", profile.ramp_up.months_to_maturity, where
        ),
    ]
    for name, ratio in profile.cost_ratios.items():
        items.append(from_quantity(f"cost_ratio_{name}", f"{name} cost as a share of revenue", ratio, where))
    items.append(
        profile_input(
            "paid_hours_per_month",
            "Paid staff hours per month",
            plan.paid_hours_per_month,
            profile.labor.paid_hours_per_month,
            f"{where} labor.paid_hours_per_month",
        )
    )
    if plan.hourly_wage is not None:
        items.append(fixed("hourly_wage", "Hourly wage", "USD/hour", plan.hourly_wage, "USER_STATED", "the founder"))
    else:
        items.append(
            metro_or_profile(
                "hourly_wage",
                "Hourly wage",
                metro_inputs.get("local_wages"),
                profile.labor.hourly_wage,
                f"{where} labor.hourly_wage",
                notes,
            )
        )
    if plan.monthly_rent is not None:
        items.append(
            fixed("monthly_rent", "Rent in the first year", "USD/month", plan.monthly_rent, "USER_STATED", "quote")
        )
    else:
        items.append(
            metro_or_profile(
                "monthly_rent",
                "Rent in the first year",
                metro_inputs.get("commercial_rent"),
                profile.rent.monthly_rent,
                f"{where} rent.monthly_rent",
                notes,
            )
        )
    items.append(
        from_quantity("rent_escalation", "Rent increase each year", profile.rent.annual_escalation, f"{where} rent")
    )
    return items


def risk_inputs(
    assumptions: FeasibilityAssumptions, site: SiteDemand, loan: Loan, notes: list[str]
) -> list[Distribution]:
    """Elasticity, noise, overruns, payroll burden and the three shocks (simulator assumptions)."""
    where = "config/studio/feasibility/assumptions.yaml"
    items = [
        from_quantity("price_elasticity", "Price elasticity of demand", assumptions.price_elasticity, where),
        from_quantity("monthly_noise_sd", "Month-to-month demand noise", assumptions.monthly_noise_sd, where),
        from_quantity("payroll_burden_share", "Payroll taxes and benefits", assumptions.payroll_burden_share, where),
        from_quantity("capex_overrun_share", "Build-out overrun", assumptions.capex_overrun_share, where),
    ]
    entry = from_quantity(
        "competitor_entry_annual_probability",
        "Chance per year that a competitor opens nearby",
        assumptions.competitor_entry_annual_probability,
        where,
    )
    if site.competition_percentile is not None:
        mode = entry.low + (entry.high - entry.low) * (1.0 - site.competition_percentile)
        entry = with_mode(
            entry,
            mode,
            f"mode set from the site's competition percentile {site.competition_percentile:.2f} "
            "(a less saturated area is assumed to attract more entrants; a design choice)",
        )
    else:
        notes.append("competition percentile UNKNOWN: the competitor-entry probability uses its ASSUMPTION mode")
    items.append(entry)
    items.append(
        from_quantity(
            "competitor_entry_demand_loss",
            "Demand lost after a competitor opens",
            assumptions.competitor_entry_demand_loss,
            where,
        )
    )
    if loan.rate_type == "variable":
        items.append(
            from_quantity(
                "rate_shock_annual_probability",
                "Chance per year that the note rate rises",
                assumptions.rate_shock_annual_probability,
                where,
            )
        )
        items.append(
            from_quantity(
                "rate_shock_size_pct_points", "Size of a rate rise", assumptions.rate_shock_size_pct_points, where
            )
        )
    else:
        notes.append("rate shocks: not applicable to a fixed-rate loan")
    items.append(
        from_quantity(
            "one_off_shock_annual_probability",
            "Chance per year of a one-off repair, closure or vacancy",
            assumptions.one_off_shock_annual_probability,
            where,
        )
    )
    items.append(
        from_quantity(
            "one_off_shock_revenue_loss_share",
            "Revenue lost in the month of a one-off event",
            assumptions.one_off_shock_revenue_loss_share,
            where,
        )
    )
    items.append(
        from_quantity(
            "one_off_shock_repair_cost_share_of_capex",
            "Repair bill of a one-off event, as a share of capex",
            assumptions.one_off_shock_repair_cost_share_of_capex,
            where,
        )
    )
    return items


def apply_overrides(distributions: dict[str, Distribution], plan: Plan) -> dict[str, Distribution]:
    """Replace ranges the user edited. An unknown name is an OverrideError (the risk/scenario.py rule)."""
    unknown = sorted(set(plan.assumption_overrides) - set(distributions))
    if unknown:
        raise OverrideError(f"unknown assumption(s) {unknown}; known: {sorted(distributions)}")
    updated = dict(distributions)
    for name, edit in plan.assumption_overrides.items():
        current = distributions[name]
        updated[name] = triangular(
            name, current.description, current.unit, edit.low, edit.mode, edit.high, "USER_STATED", edit.note
        )
    return updated


def check_bounds(distributions: dict[str, Distribution]) -> None:
    """No input is negative; shares and probabilities stay within 0..1. Raises OverrideError otherwise."""
    for name, item in distributions.items():
        if item.low < 0:
            raise OverrideError(f"{name} cannot be negative (low {item.low})")
        is_share = name.startswith(SHARE_PREFIXES) or name.endswith(SHARE_SUFFIXES)
        if is_share and item.high > 1:
            raise OverrideError(f"{name} is a share or probability and cannot exceed 1 (high {item.high})")
    if distributions["ramp_months_to_maturity"].low <= 0:
        raise OverrideError("ramp_months_to_maturity must be positive")


def loan_setup(loan: Loan, metro_inputs: dict[str, FactorValue], assumptions: FeasibilityAssumptions) -> LoanSetup:
    """The note rate: the founder's quote, or the metro's prime rate plus a stated spread."""
    if loan.annual_rate_pct is not None:
        return LoanSetup(
            loan.amount,
            loan.term_months,
            loan.annual_rate_pct,
            loan.rate_type,
            "USER_STATED",
            "rate typed by the founder",
        )
    prime = metro_inputs.get("prime_rate")
    if prime is None or prime.value is None:
        reason = prime.reason if prime is not None else "no prime rate from the data fabric"
        raise ValueError(f"no prime rate for this metro ({reason}); enter the lender's quoted rate (annual_rate_pct)")
    if loan.rate_spread_pct is not None:
        spread, spread_note = loan.rate_spread_pct, "spread typed by the founder"
    else:
        spread = assumptions.rate_spread_over_prime_pct.point()
        spread_note = f"spread is the ASSUMPTION mode ({assumptions.rate_spread_over_prime_pct.note})"
    source = f"prime rate {prime.value}% ({prime.status}: {prime.reason}) + {spread} points ({spread_note})"
    status: InputStatus = "USER_STATED" if loan.rate_spread_pct is not None else "ASSUMPTION"
    if prime.status == "SYNTHETIC":
        status = "SYNTHETIC"
    return LoanSetup(loan.amount, loan.term_months, round(prime.value + spread, 4), loan.rate_type, status, source)


def seasonality_source(profile: Profile) -> str:
    """Where the seasonality curve came from (the profile, after any metro override)."""
    curve = profile.seasonality
    return f"profile {profile.id} seasonality ({curve.status}): {curve.note}"


def build_setup(
    profile: Profile,
    metro_id: str,
    data_label: str,
    metro_inputs: dict[str, FactorValue],
    site: SiteDemand,
    plan: Plan,
    loan: Loan,
    assumptions: FeasibilityAssumptions,
    months: int = 36,
) -> SimulationSetup:
    """Collect every input of one simulation. Raises ValueError for an inconsistent plan."""
    if months < MIN_MONTHS:
        raise ValueError(f"simulate at least {MIN_MONTHS} months (year-2 DSCR and the 24-month cash check)")
    if site.unit != profile.demand_unit:
        raise ValueError(f"site demand is in {site.unit!r} but {profile.id} uses {profile.demand_unit!r}")
    notes: list[str] = demand_notes(site)
    items = demand_inputs(profile, site, plan)
    items += operating_inputs(profile, plan, metro_inputs, notes)
    items += risk_inputs(assumptions, site, loan, notes)
    distributions = apply_overrides({item.name: item for item in items}, plan)
    check_bounds(distributions)
    if plan.capex is not None:
        capex, capex_status = plan.capex, "USER_STATED"
    else:
        capex, capex_status = sum(item.cost.point() for item in profile.capex), "ASSUMPTION"
        notes.append("capex not given: the sum of the profile's capex modes (ASSUMPTION) is used")
    loan_terms = loan_setup(loan, metro_inputs, assumptions)
    if plan.owner_equity + loan.amount < capex:
        raise ValueError("owner equity plus the loan do not cover the planned capex (starting cash would be negative)")
    if site.coverage_score is not None:
        notes.append(f"site data-coverage score {site.coverage_score:.2f} (from SiteIQ)")
    return SimulationSetup(
        profile_id=profile.id,
        metro_id=metro_id,
        data_label=data_label,
        demand_unit=profile.demand_unit,
        per_day=profile.demand_unit.endswith("/day"),
        months=months,
        opening_date=plan.opening_date,
        seasonality=tuple(profile.seasonality.values),
        seasonality_source=seasonality_source(profile),
        reference_price=profile.price.point(),
        cost_ratio_names=tuple(profile.cost_ratios),
        distributions=distributions,
        loan=loan_terms,
        planned_capex=float(capex),
        capex_status=capex_status,
        owner_equity=plan.owner_equity,
        owner_draw=plan.owner_draw_per_month,
        site_demand=site,
        notes=tuple(notes),
    )
