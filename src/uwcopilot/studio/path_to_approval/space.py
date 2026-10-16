"""The lever space of one plan: each lever's current value, bounds and grid, and why a lever is not available.

build_lever_space() is called once per search. It reads the plan's money (sources = uses), decides which levers
apply to this plan, and resolves their bounds. A lever is not available when:
- the founder locked it, or it is balanced by a dollar amount the founder locked (levers.BALANCED_BY: "put in more
  of your own cash" lowers the loan, so a locked loan amount also stops it);
- it would edit a figure the founder marked as verified (FounderFacts.verified_fields);
- it does not apply to this business (opening days for a business whose demand is counted per month);
- it needs an input the plan lacks (opening days need a typed staffing plan, the build-out lever a typed build-out
  cost, the site lever candidates);
- the config does not list it.

A lock freezes the AMOUNT, not only the lever: a locked dollar lever gets the bounds (current, current), and
guardrails.check_amounts enforces every dollar lever's bounds whichever lever moves the amount. So a locked owner
equity cannot grow through the cash-reserve lever either, and a locked cash reserve cannot shrink through "borrow
less". A lock is never weaker than a bound.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from uwcopilot.studio.path_to_approval.errors import GuardrailViolation
from uwcopilot.studio.path_to_approval.levers import (
    AMOUNT_LEVERS,
    BALANCED_BY,
    LEVER_NAMES,
    LeverBounds,
    LeverConfig,
    grid,
    resolve_bounds,
)
from uwcopilot.studio.path_to_approval.models import Candidate, PlanAmounts, PlanCase
from uwcopilot.studio.path_to_approval.program import program_checks
from uwcopilot.studio.profiles.models import Profile

# The plan fields each lever writes (guardrails.check_only_lever_fields_changed enforces this list).
LEVER_WRITES: dict[str, tuple[str, ...]] = {
    "owner_equity": ("plan.owner_equity", "loan.amount"),
    "loan_amount": ("loan.amount",),
    "build_out_cost": ("plan.capex", "loan.amount"),
    "working_capital_reserve": ("plan.owner_equity",),
    "price": ("plan.price",),
    "days_open_per_week": ("plan.days_open_per_week", "plan.paid_hours_per_month"),
    "loan_term_months": ("loan.term_months",),
    "site": ("site", "site_label", "plan.monthly_rent"),
}


@dataclass(frozen=True)
class Amounts:
    """The plan's money. Sources = uses: equity + loan + other debt = build-out + cash reserve at opening."""

    equity: float
    loan: float
    other: float
    capex: float

    @property
    def reserve(self) -> float:
        """Cash at opening (before any build-out overrun): the part of the sources not spent on the build-out."""
        return self.equity + self.loan + self.other - self.capex

    @property
    def project_cost(self) -> float:
        """Total uses: build-out plus cash reserve (= total sources)."""
        return self.capex + self.reserve

    def value_of(self, lever: str) -> float:
        """The amount a dollar lever moves."""
        values = {
            "owner_equity": self.equity,
            "loan_amount": self.loan,
            "build_out_cost": self.capex,
            "working_capital_reserve": self.reserve,
        }
        return values[lever]


@dataclass(frozen=True)
class LeverSpace:
    """Everything the guardrails and the search need to know about the levers of one plan."""

    case: PlanCase
    configs: dict[str, LeverConfig]
    amounts: Amounts
    reference_price: float
    days_mode: float | None
    current: dict[str, float | str]
    bounds: dict[str, tuple[float, float]]
    grids: dict[str, list[float | str]]
    candidates: dict[str, Candidate]
    available: tuple[str, ...]
    not_available: dict[str, str]
    demand_unit: str

    def plan_amounts(self, case: PlanCase) -> PlanAmounts:
        """The money and terms of a plan (this plan or a changed one), with its program checks."""
        amounts = amounts_of(case, self.amounts.capex)
        return PlanAmounts(
            owner_equity=round(amounts.equity, 2),
            loan_amount=round(amounts.loan, 2),
            other_debt_amount=round(amounts.other, 2),
            build_out_cost=round(amounts.capex, 2),
            cash_reserve_at_opening=round(amounts.reserve, 2),
            loan_term_months=case.loan.term_months,
            price=case.plan.price,
            days_open_per_week=case.plan.days_open_per_week,
            site_label=case.site_label,
            checks=program_checks(amounts.equity, amounts.project_cost, amounts.loan, case.loan.term_months),
        )


def amounts_of(case: PlanCase, default_capex: float) -> Amounts:
    """The plan's money; default_capex is used when the founder has not typed a build-out cost."""
    capex = case.plan.capex if case.plan.capex is not None else default_capex
    return Amounts(
        equity=case.plan.owner_equity,
        loan=case.loan.amount,
        other=case.facts.other_debt_amount,
        capex=float(capex),
    )


def check_candidates(case: PlanCase, candidates: Sequence[Candidate], demand_unit: str) -> dict[str, Candidate]:
    """{label: candidate}. Same metro and unit, unique labels, none named like the current site.

    A candidate in another metro raises GuardrailViolation, because the search compares sites of one plan in one
    metro; a mismatch of unit or label is a ValueError.
    """
    found: dict[str, Candidate] = {}
    for candidate in candidates:
        if candidate.metro_id != case.metro_id:
            raise GuardrailViolation(
                f"candidate site {candidate.label!r} is in {candidate.metro_id}, not {case.metro_id}: path to "
                "approval only compares candidate sites in the plan's own metro"
            )
        if candidate.site.unit != demand_unit:
            unit = candidate.site.unit
            raise ValueError(f"candidate {candidate.label!r} has demand in {unit!r}, not {demand_unit!r}")
        if candidate.label == case.site_label or candidate.label in found:
            raise ValueError(f"candidate label {candidate.label!r} is not unique")
        found[candidate.label] = candidate
    return found


def why_not_available(
    name: str,
    case: PlanCase,
    profile: Profile,
    configs: Mapping[str, LeverConfig],
    candidates: Mapping[str, Candidate],
    locked: set[str],
) -> str | None:
    """Why a lever cannot move for this plan, or None when it can."""
    if name not in configs:
        return "not listed in config/studio/levers.yaml"
    if name in locked:
        return "locked by you"
    absorbed_by = BALANCED_BY.get(name)
    if absorbed_by in locked and absorbed_by in AMOUNT_LEVERS:
        label = configs[absorbed_by].label.lower() if absorbed_by in configs else absorbed_by
        return f"it is balanced by the {label}, which you locked (moving it would also move that amount)"
    verified = sorted(set(LEVER_WRITES[name]) & set(case.facts.verified_fields))
    if verified:
        return f"it would edit {', '.join(verified)}, which you marked as verified; verified figures never move"
    if name == "days_open_per_week":
        if not profile.demand_unit.endswith("/day") or profile.hours is None:
            return f"demand of a {profile.id} is not counted per open day ({profile.demand_unit})"
        if case.plan.paid_hours_per_month is None:
            return (
                "enter your staffing plan (paid hours per month) first: staff hours scale with open days, and "
                "without your own number the cost of more open days would be guessed"
            )
    if name == "build_out_cost" and case.plan.capex is None:
        return (
            "enter your build-out quote first: without it the build-out cost is the profile's ASSUMPTION (the sum of "
            "its capex modes), and an assumption is never lowered to pass a target"
        )
    if name == "site" and not candidates:
        return "no other candidate site in this metro was given"
    return None


def current_values(
    case: PlanCase, amounts: Amounts, reference_price: float, days_mode: float | None
) -> dict[str, float | str]:
    """The current value of every lever (dollar amounts, price, days, term and the site's label)."""
    values: dict[str, float | str] = {name: amounts.value_of(name) for name in AMOUNT_LEVERS}
    values["price"] = case.plan.price if case.plan.price is not None else reference_price
    days = case.plan.days_open_per_week if case.plan.days_open_per_week is not None else days_mode
    if days is not None:
        values["days_open_per_week"] = days
    values["loan_term_months"] = float(case.loan.term_months)
    values["site"] = case.site_label
    return values


def build_lever_space(
    case: PlanCase,
    profile: Profile,
    default_capex: float,
    configs: Mapping[str, LeverConfig],
    candidates: Sequence[Candidate] = (),
    bounds: Mapping[str, LeverBounds] | None = None,
    locked: Iterable[str] = (),
    steps_per_side: int = 8,
) -> LeverSpace:
    """Resolve the levers of one plan (see the module docstring).

    default_capex is the build-out cost the simulator uses when the founder typed none (the sum of the profile's
    capex modes, S3 setup.py). Unknown names in bounds or locked are an error.
    """
    user_bounds = dict(bounds or {})
    locked_set = set(locked)
    unknown = sorted((set(user_bounds) | locked_set) - set(LEVER_NAMES))
    if unknown:
        raise ValueError(f"unknown lever(s) {unknown}; levers: {list(LEVER_NAMES)}")
    amounts = amounts_of(case, default_capex)
    if amounts.reserve < 0:
        raise ValueError("the plan's sources do not cover its build-out (cash at opening would be negative)")
    found = check_candidates(case, candidates, profile.demand_unit)
    reference_price = profile.price.point()
    days_mode = profile.hours.days_per_week.point() if profile.hours is not None else None
    current = current_values(case, amounts, reference_price, days_mode)
    resolved: dict[str, tuple[float, float]] = {}
    grids: dict[str, list[float | str]] = {}
    not_available: dict[str, str] = {}
    available = []
    for name in LEVER_NAMES:
        reason = why_not_available(name, case, profile, configs, found, locked_set)
        config = configs.get(name)
        numeric = config is not None and config.kind != "site" and name in current
        if numeric and config is not None:
            value = float(current[name])
            resolved[name] = resolve_bounds(name, config, value, amounts.project_cost, user_bounds.get(name))
            if name in locked_set and name in AMOUNT_LEVERS:
                resolved[name] = (value, value)  # a locked amount stays put, whichever lever would move it
        if reason is not None:
            not_available[name] = reason
            continue
        if name == "site":
            grids[name] = list(found)
        elif config is not None:
            low, high = resolved[name]
            grids[name] = list(grid(config, float(current[name]), low, high, steps_per_side))
        if not grids.get(name):
            not_available[name] = "its bounds leave no other value to try"
            continue
        available.append(name)
    return LeverSpace(
        case=case,
        configs=dict(configs),
        amounts=amounts,
        reference_price=reference_price,
        days_mode=days_mode,
        current=current,
        bounds=resolved,
        grids=grids,
        candidates=found,
        available=tuple(available),
        not_available=not_available,
        demand_unit=profile.demand_unit,
    )
