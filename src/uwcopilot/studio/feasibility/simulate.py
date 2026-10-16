"""simulate(): one site, one plan, one loan -> a FeasibilityResult (Studio S3).

    from uwcopilot.studio.feasibility import Loan, Plan, SiteDemand, simulate
    result = simulate("car_wash", "synthetic_metro", site_demand, plan, loan, n=1000, months=36, seed=42)
    result.p_dscr_at_least_target["year_2"]    # simulated, based on stated assumptions
    result.tornado.top_drivers                 # the three inputs that move year-2 net income most

Steps: load the profile with the metro's overrides, read the metro inputs from the data fabric (local wages, the
metro rent range, the prime rate), build the setup (setup.py), draw every random number once (seeded), run the base
paths, then the tornado and the lender stress tests on the SAME draws, and summarize (metrics.py).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from uwcopilot.studio.data.fabric import DataFabric, get_fabric
from uwcopilot.studio.feasibility.assumptions import (
    DscrTarget,
    FeasibilityAssumptions,
    default_dscr_target,
    load_assumptions,
)
from uwcopilot.studio.feasibility.inputs import Loan, Plan, as_site_demand
from uwcopilot.studio.feasibility.loan import annual_debt_service, monthly_payment
from uwcopilot.studio.feasibility.metrics import (
    BREAK_EVEN_RUN,
    annual_total,
    bands,
    break_even_month,
    dscr_by_year,
    p_cash_negative,
    p_dscr_at_least,
    percentiles_of,
)
from uwcopilot.studio.feasibility.model import MONTHS_PER_YEAR, Draws, PathResults, simulate_paths
from uwcopilot.studio.feasibility.result import BreakEven, FeasibilityResult, LoanTerms, MonthBand, YearBand
from uwcopilot.studio.feasibility.setup import SimulationSetup, build_setup
from uwcopilot.studio.feasibility.stress import lender_stress, projected_dscr
from uwcopilot.studio.feasibility.tornado import tornado
from uwcopilot.studio.profiles.loader import apply_metro_overrides, load_profile
from uwcopilot.studio.profiles.models import Profile

DEFAULT_SEED = 42
DEFAULT_PATHS = 1000
DEFAULT_MONTHS = 36
SHOCK_NOTES: tuple[str, ...] = (
    "competitor entry: at most once per path; its month follows a constant monthly hazard from the annual "
    "probability; demand is lower by the sampled loss from that month on",
    "rate shock (variable-rate loans only): at most once per path; the remaining balance is re-amortized over the "
    "remaining term at the higher rate",
    "one-off repair, closure or vacancy: at most once per path; that month loses the sampled share of revenue and "
    "pays a repair bill (a share of the planned capex)",
)


@dataclass(frozen=True)
class SimulationRun:
    """A result plus everything that produced it (for the tornado, the validation and tests)."""

    result: FeasibilityResult
    setup: SimulationSetup
    draws: Draws
    values: dict[str, np.ndarray]
    paths: PathResults


def add_months(start: date, months: int) -> str:
    """'YYYY-MM' of the month `months` after start."""
    index = start.year * MONTHS_PER_YEAR + start.month - 1 + months
    return f"{index // MONTHS_PER_YEAR:04d}-{index % MONTHS_PER_YEAR + 1:02d}"


def money(value: float) -> float:
    """Round a dollar amount to the cent for the result."""
    return round(float(value), 2)


def two_decimals(value: float) -> float:
    """A ratio rounded to two decimals (adding 0.0 turns -0.0 into 0.0)."""
    return round(float(value), 2) + 0.0


def month_bands(setup: SimulationSetup, paths: PathResults) -> list[MonthBand]:
    """P10/P50/P90 of revenue, net income and cash for every month."""
    revenue, net, cash = bands(paths.revenue), bands(paths.net_income), bands(paths.cash)
    return [
        MonthBand(
            month=t + 1,
            calendar_month=add_months(setup.opening_date, t),
            revenue_p10=money(revenue[0, t]),
            revenue_p50=money(revenue[1, t]),
            revenue_p90=money(revenue[2, t]),
            net_income_p10=money(net[0, t]),
            net_income_p50=money(net[1, t]),
            net_income_p90=money(net[2, t]),
            cash_p10=money(cash[0, t]),
            cash_p50=money(cash[1, t]),
            cash_p90=money(cash[2, t]),
        )
        for t in range(setup.months)
    ]


def has_full_debt_year(loan_term_months: int, year: int) -> bool:
    """True when all 12 months of the year have a scheduled loan payment (the term reaches the year's end).

    A DSCR is only comparable with the target for such a year: after the loan is repaid there is no debt service,
    and in a partial year the debt service covers only some months.
    """
    return loan_term_months >= MONTHS_PER_YEAR * year


def partial_year_note(loan_term_months: int, year: int) -> str:
    """Why year `year` has no DSCR and no P(DSCR >= target)."""
    paid = min(max(loan_term_months - MONTHS_PER_YEAR * (year - 1), 0), MONTHS_PER_YEAR)
    return (
        f"year {year}: no DSCR and no P(DSCR >= target): the {loan_term_months}-month loan has scheduled payments "
        f"in {paid} of its 12 months (a partial or debt-free year is not comparable with the target)"
    )


def year_band(paths: PathResults, year: int, full_debt_year: bool = True) -> YearBand:
    """P10/P50/P90 of one year's totals across paths (percentiles of the per-path totals).

    The DSCR percentiles are None for a year without 12 scheduled loan payments (has_full_debt_year).
    """
    revenue = percentiles_of(annual_total(paths.revenue, year))
    noi = percentiles_of(annual_total(paths.noi, year))
    net = percentiles_of(annual_total(paths.net_income, year))
    dscr = percentiles_of(dscr_by_year(paths, year)) if full_debt_year else (None, None, None)
    return YearBand(
        year=year,
        revenue_p10=money(revenue[0]),
        revenue_p50=money(revenue[1]),
        revenue_p90=money(revenue[2]),
        noi_p10=money(noi[0]),
        noi_p50=money(noi[1]),
        noi_p90=money(noi[2]),
        net_income_p10=money(net[0]),
        net_income_p50=money(net[1]),
        net_income_p90=money(net[2]),
        debt_service_p50=money(np.median(annual_total(paths.debt_service, year))),
        dscr_p10=None if dscr[0] is None else two_decimals(dscr[0]),
        dscr_p50=None if dscr[1] is None else two_decimals(dscr[1]),
        dscr_p90=None if dscr[2] is None else two_decimals(dscr[2]),
    )


def break_even_summary(paths: PathResults) -> BreakEven:
    """Break-even month percentiles; a percentile is None when that share of paths never breaks even."""
    months = break_even_month(paths.net_income)
    horizon = paths.net_income.shape[1]
    # Paths that never break even sort after every month; a discrete percentile (no interpolation) then says
    # whether that share of paths broke even at all.
    ranked = np.where(months > 0, months, horizon + 1)
    found = [int(np.percentile(ranked, share, method="inverted_cdf")) for share in (10, 50, 90)]
    chosen = [month if month <= horizon else None for month in found]
    values, frequencies = np.unique(months[months > 0], return_counts=True)
    counts = {str(month): int(count) for month, count in zip(values, frequencies, strict=True)}
    counts["not within horizon"] = int(np.sum(months == 0))
    return BreakEven(
        definition=f"first month starting {BREAK_EVEN_RUN} months in a row with net income (NOI - debt service) >= 0",
        p10_month=chosen[0],
        p50_month=chosen[1],
        p90_month=chosen[2],
        share_not_within_horizon=float(np.mean(months == 0)),
        paths_by_month=counts,
    )


def loan_terms(setup: SimulationSetup) -> LoanTerms:
    """The loan as simulated (payment from credit_calculations)."""
    loan = setup.loan
    return LoanTerms(
        amount=loan.amount,
        term_months=loan.term_months,
        annual_rate_pct=loan.annual_rate_pct,
        rate_type=loan.rate_type,
        rate_status=loan.rate_status,
        rate_source=loan.rate_source,
        monthly_payment=money(monthly_payment(loan.amount, loan.annual_rate_pct, loan.term_months)),
        annual_debt_service=money(annual_debt_service(loan.amount, loan.annual_rate_pct, loan.term_months)),
    )


def resolve_profile(profile: Profile | str, metro_id: str) -> Profile:
    """A Profile with the metro's overrides applied (a profile id is loaded first)."""
    if isinstance(profile, str):
        return load_profile(profile, metro_id=metro_id)
    return apply_metro_overrides(profile, metro_id)


def run_simulation(
    profile: Profile | str,
    metro: DataFabric | str,
    site_score: Any,
    plan: Plan,
    loan: Loan,
    n: int = DEFAULT_PATHS,
    months: int = DEFAULT_MONTHS,
    seed: int = DEFAULT_SEED,
    *,
    dscr_target: DscrTarget | None = None,
    assumptions: FeasibilityAssumptions | None = None,
    with_tornado: bool = True,
    with_stress: bool = True,
) -> SimulationRun:
    """simulate(), also returning the setup, the draws and the per-path arrays."""
    if n < 1:
        raise ValueError("simulate at least one path")
    fabric = get_fabric(metro) if isinstance(metro, str) else metro
    chosen = resolve_profile(profile, fabric.metro.id)
    target = dscr_target or default_dscr_target()
    setup = build_setup(
        chosen,
        fabric.metro.id,
        fabric.data_label,
        fabric.metro_inputs(chosen),
        as_site_demand(site_score),
        plan,
        loan,
        assumptions or load_assumptions(),
        months,
    )
    draws, values, paths = simulate_paths(setup, n, seed)
    term = setup.loan.term_months
    years = [year_band(paths, year, has_full_debt_year(term, year)) for year in range(1, months // MONTHS_PER_YEAR + 1)]
    full_years = [year.year for year in years if has_full_debt_year(term, year.year)]
    year_notes = [partial_year_note(term, year.year) for year in years if year.year not in full_years]
    mode, mode_reason = fabric.mode()
    result = FeasibilityResult(
        data_label=setup.data_label,
        metro_id=setup.metro_id,
        metro_mode=mode,
        metro_mode_reason=mode_reason,
        profile_id=setup.profile_id,
        demand_unit=setup.demand_unit,
        n_paths=n,
        months=months,
        seed=seed,
        opening_date=setup.opening_date,
        site_demand=setup.site_demand,
        loan=loan_terms(setup),
        planned_capex=money(setup.planned_capex),
        capex_status=setup.capex_status,
        owner_equity=money(setup.owner_equity),
        planned_starting_cash=money(setup.planned_starting_cash),
        dscr_target=target,
        p_dscr_at_least_target={f"year_{year}": p_dscr_at_least(paths, year, target.value) for year in full_years},
        p_cash_negative_within_24_months=p_cash_negative(paths),
        break_even=break_even_summary(paths),
        monthly=month_bands(setup, paths),
        yearly=years,
        distributions=list(setup.distributions.values()),
        seasonality=list(setup.seasonality),
        seasonality_source=setup.seasonality_source,
        shocks=list(SHOCK_NOTES),
        projected_dscr_base_case=projected_dscr(setup, years[1]),
        tornado=tornado(setup, values, draws, target.value, paths) if with_tornado else None,
        stress=lender_stress(setup, values, draws, years[1]) if with_stress else [],
        notes=list(setup.notes) + year_notes,
    )
    return SimulationRun(result=result, setup=setup, draws=draws, values=values, paths=paths)


def simulate(
    profile: Profile | str,
    metro: DataFabric | str,
    site_score: Any,
    plan: Plan,
    loan: Loan,
    n: int = DEFAULT_PATHS,
    months: int = DEFAULT_MONTHS,
    seed: int = DEFAULT_SEED,
    *,
    dscr_target: DscrTarget | None = None,
    assumptions: FeasibilityAssumptions | None = None,
    with_tornado: bool = True,
    with_stress: bool = True,
) -> FeasibilityResult:
    """Simulate n possible futures of one site and plan (see the module docstring).

    site_score is SiteIQ's output or a SiteDemand (inputs.as_site_demand). metro is a metro id or a data fabric.
    """
    return run_simulation(
        profile,
        metro,
        site_score,
        plan,
        loan,
        n,
        months,
        seed,
        dscr_target=dscr_target,
        assumptions=assumptions,
        with_tornado=with_tornado,
        with_stress=with_stress,
    ).result
