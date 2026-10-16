"""SYNTHETIC founder plans for the path-to-approval evaluation (scripts/eval_studio_paths.py) and the tests.

Nothing here describes a real business. Every constant below is a generator parameter chosen by the project author
(a design choice, not data and not a benchmark).

How one plan is made (draws always in the same order, so a seed reproduces it):
1. The plan and loan come from the S3 validation generator, unchanged (feasibility.worlds.draw_plan): build-out
   cost, loan, owner equity, a cash reserve, term, fixed or variable rate, and sometimes a typed price or rent quote.
2. With probability STAFFING_PLAN_SHARE the founder also typed a staffing plan (the profile's most likely paid hours
   and, for a business counted per open day, its most likely open days), which makes the "days open" lever usable.
3. The plan is anchored so that its ONE-FORECAST lender-stressed year-2 DSCR equals a drawn target (bisection). The
   one-forecast case has every input at its most likely value, no shocks and no noise; the stress is NSP-4.2's:
   revenue 20% lower and NOI lower by the same dollars. The target is uniform on STRESSED_DSCR_TARGET = (0.6, 2.6),
   which starts below the NSP-4.2 threshold of 1.00 (the binding test of the SYNTHETIC startup program) and reaches
   well past it, so the plans span "clearly short", "near the line" and "comfortably past the line" of the one
   number a spreadsheet would show. The two anchoring levers are the S3 worlds' own, in the
   same order: (a) "demand": the site's most likely demand; (b) "rent_quote": when even the profile's most likely
   capacity cannot reach the target, demand stays at that capacity and the founder gets a rent quote between 0 and
   the metro rent that reaches it; (c) "none": when even a zero rent cannot, the plan keeps the capacity and its
   rent, and is kept and counted as not anchored (never dropped). "none" means the one-forecast plan cannot reach
   the drawn stress target at any demand or rent, which happens when the business keeps too little NOI per revenue
   dollar for the 20% revenue stress.
4. The plan gets N_CANDIDATES other candidate sites in the same metro: the anchored demand times a uniform
   CANDIDATE_DEMAND_FACTOR, the same low/high spread, a random competition percentile and, with probability
   CANDIDATE_RENT_QUOTE_SHARE, a rent quote (the metro rent value, or the profile's rent mode when the metro has
   none, times worlds.RENT_FACTOR: the same rule as worlds.draw_plan).

with_synthetic_facts() gives a plan SYNTHETIC founder facts for the evaluation's guardrail check (the headline plans
carry none, like the S3 worlds): a credit score, months of experience, sometimes a seller note (other debt), and some
plan figures marked as verified. The search must leave every one of them untouched.

Why anchor on the stressed DSCR and not the plain one: the S3 worlds anchor the plain one-forecast DSCR on 0.6-2.4,
which is right for checking S3's calibration, but for a business keeping 30 cents of NOI per revenue dollar the
stress needs a plain DSCR near 3 (targets.py), so almost every S3 world is far from the binding test and the search
would only ever answer "no path". Why the range reaches 2.6 and not just 1.4: the simulated year-2 median is usually
well below the one-forecast case (ramp-up, shocks, right-skewed cost ranges and operating leverage), so a plan that
only just passes the stress in a spreadsheet rarely passes it on 80% of simulated futures. The path-found rate depends
on this choice; the results file reports it by target band (TARGET_BANDS) so a reader can see how.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np

from uwcopilot.agent.policy_credit import REVENUE_STRESS_SHARE
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility.assumptions import FeasibilityAssumptions
from uwcopilot.studio.feasibility.inputs import Plan, SiteDemand
from uwcopilot.studio.feasibility.metrics import annual_total
from uwcopilot.studio.feasibility.model import single_point_run, single_point_values
from uwcopilot.studio.feasibility.setup import SimulationSetup, build_setup
from uwcopilot.studio.feasibility.worlds import (
    BISECTION_STEPS,
    DEMAND_HIGH_FACTOR,
    DEMAND_LOW_FACTOR,
    RENT_FACTOR,
    draw_plan,
    uniform,
)
from uwcopilot.studio.path_to_approval.models import Candidate, FounderFacts, OtherDebt, PlanCase
from uwcopilot.studio.profiles.models import Profile

# Generator parameters (SYNTHETIC; design choices, not data).
STRESSED_DSCR_TARGET = (0.6, 2.6)
TARGET_BANDS: tuple[tuple[float, float], ...] = ((0.6, 1.1), (1.1, 1.6), (1.6, 2.1), (2.1, 2.6))
STAFFING_PLAN_SHARE = 0.5
N_CANDIDATES = 2
CANDIDATE_DEMAND_FACTOR = (0.75, 1.3)
CANDIDATE_RENT_QUOTE_SHARE = 0.5
ANCHOR_YEAR = 2
SOURCE = "SYNTHETIC evaluation plan (src/uwcopilot/studio/path_to_approval/synthetic_plans.py)"
SITE_LABELS: tuple[str, ...] = ("candidate A", "candidate B", "candidate C")

# Founder facts for the guardrail check (with_synthetic_facts; SYNTHETIC generator parameters, design choices).
FACTS_STREAM = 7  # keeps the facts' random draws apart from the plan's own
FACTS_CREDIT_SCORE = (600, 800)
FACTS_EXPERIENCE_MONTHS = (0, 120)
FACTS_OTHER_DEBT_SHARE = 0.5
FACTS_OTHER_DEBT_OF_LOAN = (0.02, 0.10)
FACTS_OTHER_DEBT_MONTHLY_RATE = 0.02  # monthly payment per dollar of proceeds
FACTS_VERIFIED_SHARE = 0.25

AnchorResult = Literal["demand", "rent_quote", "none"]


@dataclass(frozen=True)
class SyntheticPlan:
    """One SYNTHETIC plan with its other candidate sites and how it was anchored."""

    case: PlanCase
    candidates: tuple[Candidate, ...]
    target_stressed_dscr: float
    anchored_by: AnchorResult
    staffing_plan: bool

    @property
    def band(self) -> str:
        """The TARGET_BANDS band of the drawn target, as "[low, high)" (the last band includes its top)."""
        return band_label(self.target_stressed_dscr)


def band_label(value: float) -> str:
    """The TARGET_BANDS band of a stressed-DSCR target."""
    for index, (low, high) in enumerate(TARGET_BANDS):
        last = index == len(TARGET_BANDS) - 1
        if low <= value < high or (last and value == high):
            return f"[{low:.1f}, {high:.1f}{']' if last else ')'}"
    raise ValueError(f"stressed-DSCR target {value} is outside {STRESSED_DSCR_TARGET}")


def plan_seed(base_seed: int, metro_index: int, profile_index: int, plan_index: int) -> int:
    """A reproducible, independent seed for one (metro, business type, plan)."""
    sequence = np.random.SeedSequence([base_seed, metro_index, profile_index, plan_index])
    return int(sequence.generate_state(1)[0])


def stressed_single_point_dscr(setup: SimulationSetup, demand: float, rent: float | None = None) -> float:
    """Year-2 one-forecast DSCR after the NSP-4.2 revenue stress, with the most likely demand set to `demand`.

    rent, when given, replaces the most likely monthly rent (the rent-quote lever).
    """
    values = single_point_values(setup)
    values["mature_demand"] = np.array([demand])
    if rent is not None:
        values["monthly_rent"] = np.array([rent])
    paths = single_point_run(setup, values)
    noi = annual_total(paths.noi, ANCHOR_YEAR)[0]
    revenue = annual_total(paths.revenue, ANCHOR_YEAR)[0]
    service = annual_total(paths.debt_service, ANCHOR_YEAR)[0]
    return float((noi - REVENUE_STRESS_SHARE * revenue) / service)


def anchor_stressed(setup: SimulationSetup, target: float, ceiling: float) -> tuple[float, bool]:
    """(demand, reached): the demand in (0, ceiling] whose stressed one-forecast DSCR equals target, by bisection.

    The stressed DSCR rises with demand while each served unit adds price x (1 - cost ratios - stress share) > 0;
    above capacity it stays flat. When the ceiling cannot reach the target, return (ceiling, False).
    """
    if stressed_single_point_dscr(setup, ceiling) < target:
        return ceiling, False
    low, high = 0.0, ceiling
    for _ in range(BISECTION_STEPS):
        middle = (low + high) / 2.0
        if stressed_single_point_dscr(setup, middle) < target:
            low = middle
        else:
            high = middle
    return high, True


def anchor_stressed_rent(setup: SimulationSetup, target: float, demand: float, ceiling: float) -> tuple[float, bool]:
    """(rent, reached): the monthly rent in [0, ceiling] whose stressed one-forecast DSCR at `demand` equals target.

    The stressed DSCR falls as rent rises (rent is a fixed cost). When even a zero rent cannot reach the target,
    return (ceiling, False).
    """
    if stressed_single_point_dscr(setup, demand, rent=0.0) < target:
        return ceiling, False
    low, high = 0.0, ceiling
    for _ in range(BISECTION_STEPS):
        middle = (low + high) / 2.0
        if stressed_single_point_dscr(setup, demand, rent=middle) >= target:
            low = middle
        else:
            high = middle
    return low, True


def with_staffing_plan(profile: Profile, plan: Plan) -> Plan:
    """The plan with the profile's most likely paid hours (and open days, for a per-day business) typed in."""
    updates: dict[str, float] = {"paid_hours_per_month": round(profile.labor.paid_hours_per_month.point(), 1)}
    if profile.demand_unit.endswith("/day") and profile.hours is not None:
        updates["days_open_per_week"] = float(round(profile.hours.days_per_week.point()))
    return plan.model_copy(update=updates)


def site_range(
    demand: float, low_factor: float, high_factor: float, unit: str, label: str, competition: float
) -> SiteDemand:
    """A SYNTHETIC demand range around a most likely demand."""
    return SiteDemand(
        low=demand * low_factor,
        mode=demand,
        high=demand * high_factor,
        unit=unit,
        status="SYNTHETIC",
        source=f"{SOURCE}, {label}",
        competition_percentile=competition,
    )


def candidate_sites(
    rng: np.random.Generator,
    metro_id: str,
    demand: float,
    factors: tuple[float, float],
    unit: str,
    metro_rent: float,
) -> tuple[Candidate, ...]:
    """N_CANDIDATES other SYNTHETIC candidate sites in the same metro (see the module docstring)."""
    items = []
    for label in SITE_LABELS[1 : N_CANDIDATES + 1]:
        mode = demand * uniform(rng, CANDIDATE_DEMAND_FACTOR)
        competition = float(rng.uniform())
        quoted = bool(rng.uniform() < CANDIDATE_RENT_QUOTE_SHARE)
        rent_draw = uniform(rng, RENT_FACTOR)
        site = site_range(mode, factors[0], factors[1], unit, label, competition)
        rent = round(metro_rent * rent_draw, 2) if quoted else None
        items.append(Candidate(label=label, metro_id=metro_id, site=site, monthly_rent=rent))
    return tuple(items)


def generate_plan(
    profile: Profile,
    metro_id: str,
    data_label: str,
    metro_inputs: dict[str, FactorValue],
    assumptions: FeasibilityAssumptions,
    seed: int,
    months: int = 36,
    quoted_rate_pct: float | None = None,
) -> SyntheticPlan:
    """One SYNTHETIC plan with its candidate sites (see the module docstring).

    quoted_rate_pct, when given, becomes the loan's annual rate before the plan is simulated. Callers pass it for
    a metro that has no prime rate here, which would otherwise have no note rate to simulate with.
    """
    rng = np.random.default_rng(seed)
    plan, loan = draw_plan(profile, metro_inputs, rng)
    if quoted_rate_pct is not None and loan.annual_rate_pct is None:
        loan = loan.model_copy(update={"annual_rate_pct": quoted_rate_pct})
    staffing = bool(rng.uniform() < STAFFING_PLAN_SHARE)
    if staffing:
        plan = with_staffing_plan(profile, plan)
    target = uniform(rng, STRESSED_DSCR_TARGET)
    factors = (uniform(rng, DEMAND_LOW_FACTOR), uniform(rng, DEMAND_HIGH_FACTOR))
    competition = float(rng.uniform())
    ceiling = profile.capacity.max_demand.point()
    unit = profile.demand_unit
    placeholder = SiteDemand(low=ceiling, mode=ceiling, high=ceiling, unit=unit, status="SYNTHETIC")
    setup = build_setup(profile, metro_id, data_label, metro_inputs, placeholder, plan, loan, assumptions, months)
    demand, reached = anchor_stressed(setup, target, ceiling)
    anchored_by: AnchorResult = "demand"
    if not reached:
        rent, reached = anchor_stressed_rent(setup, target, demand, setup.distributions["monthly_rent"].mode)
        anchored_by = "rent_quote" if reached else "none"
        if reached:
            plan = plan.model_copy(update={"monthly_rent": round(rent, 2)})
    site = site_range(demand, factors[0], factors[1], unit, SITE_LABELS[0], competition)
    metro_rent = metro_inputs["commercial_rent"].value or profile.rent.monthly_rent.point()
    candidates = candidate_sites(rng, metro_id, demand, factors, unit, metro_rent)
    case = PlanCase(
        profile_id=profile.id, metro_id=metro_id, site_label=SITE_LABELS[0], site=site, plan=plan, loan=loan
    )
    return SyntheticPlan(case, candidates, target, anchored_by, staffing)


def typed_fields(case: PlanCase) -> list[str]:
    """The verifiable plan figures this plan actually holds (an empty figure cannot be verified)."""
    plan = case.plan
    optional = {
        "plan.capex": plan.capex,
        "plan.price": plan.price,
        "plan.monthly_rent": plan.monthly_rent,
        "plan.days_open_per_week": plan.days_open_per_week,
        "plan.paid_hours_per_month": plan.paid_hours_per_month,
    }
    always = ["plan.owner_equity", "loan.amount", "loan.term_months", "site"]
    return always + [name for name, value in optional.items() if value is not None]


def with_synthetic_facts(case: PlanCase, seed: int) -> PlanCase:
    """The plan with SYNTHETIC founder facts drawn from the seed (see the module docstring).

    A credit score and experience in months (uniform whole numbers), with probability FACTS_OTHER_DEBT_SHARE a
    seller note whose proceeds are a uniform share of the loan and whose monthly payment is
    FACTS_OTHER_DEBT_MONTHLY_RATE of them, and each typed plan figure marked as verified with probability
    FACTS_VERIFIED_SHARE.
    """
    rng = np.random.default_rng(np.random.SeedSequence([seed, FACTS_STREAM]))
    credit_score = int(rng.integers(FACTS_CREDIT_SCORE[0], FACTS_CREDIT_SCORE[1] + 1))
    experience = int(rng.integers(FACTS_EXPERIENCE_MONTHS[0], FACTS_EXPERIENCE_MONTHS[1] + 1))
    other_debt = []
    if rng.uniform() < FACTS_OTHER_DEBT_SHARE:
        amount = round(case.loan.amount * uniform(rng, FACTS_OTHER_DEBT_OF_LOAN), 2)
        payment = round(amount * FACTS_OTHER_DEBT_MONTHLY_RATE, 2)
        other_debt.append(OtherDebt(name="seller note (SYNTHETIC)", amount=amount, monthly_payment=payment))
    verified = [name for name in typed_fields(case) if rng.uniform() < FACTS_VERIFIED_SHARE]
    facts = FounderFacts(
        credit_score=credit_score, experience_months=experience, other_debt=other_debt, verified_fields=verified
    )
    return case.model_copy(update={"facts": facts})
