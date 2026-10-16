"""The levers of the path-to-approval search: config, bounds, search grid and effort (Studio S5).

A lever is a PLAN CHOICE the founder can make (borrow less, put in more equity, ask for a longer term, ...). The
allow-list is LEVER_NAMES in this module: config/studio/levers.yaml may only describe these levers (the loader
refuses any other name), and each lever's behaviour (which plan fields it writes, what keeps sources equal to uses)
is code in guardrails.py, so a config edit can never turn a fact into a lever.

Bounds: each lever moves between a low and a high value. The defaults come from the YAML ({basis, value} per end:
basis current = current x (1 + value); project_cost = current + value x total project cost; absolute = value) and
are widened to include the current value. The founder can set either end (LeverBounds); a founder bound must
include the current value. The loan never goes below the SYNTHETIC program's minimum amount (NSP-8.1) unless the
plan already asks for less.

Grid: steps_per_side values on each side of the current value, evenly spaced up to the bound and rounded to the
lever's round_to (term: the listed options; days: whole days). Effort of one change = effort_weight x size
(dollars moved / total project cost for dollar levers, the relative change for price, days and term, 1 for a site).
"""

import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.agent.policy_startup import STARTUP_MIN_AMOUNT
from uwcopilot.common.paths import CONFIG_DIR

LEVERS_FILE: Path = CONFIG_DIR / "studio" / "levers.yaml"
LeverKind = Literal["amount", "price", "days", "term", "site"]
# The allow-list (order = the order the search tries them in, which only matters for ties).
LEVER_KINDS: dict[str, LeverKind] = {
    "owner_equity": "amount",
    "loan_amount": "amount",
    "build_out_cost": "amount",
    "working_capital_reserve": "amount",
    "price": "price",
    "days_open_per_week": "days",
    "loan_term_months": "term",
    "site": "site",
}
LEVER_NAMES: tuple[str, ...] = tuple(LEVER_KINDS)
AMOUNT_LEVERS: tuple[str, ...] = tuple(name for name, kind in LEVER_KINDS.items() if kind == "amount")
# What absorbs a dollar lever's change so that sources still equal uses (guardrails.amount_deltas).
BALANCED_BY: dict[str, str] = {
    "owner_equity": "loan_amount",
    "loan_amount": "working_capital_reserve",
    "build_out_cost": "loan_amount",
    "working_capital_reserve": "owner_equity",
}
VALUE_TOLERANCE = 1e-9


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BoundSpec(Frozen):
    """One end of a default bound."""

    basis: Literal["current", "project_cost", "absolute"]
    value: float

    def resolve(self, current: float, project_cost: float) -> float:
        """The bound in the lever's unit for this plan."""
        if self.basis == "current":
            return current * (1.0 + self.value)
        if self.basis == "project_cost":
            return current + self.value * project_cost
        return self.value


class DefaultBounds(Frozen):
    """The default low and high ends of a lever."""

    low: BoundSpec
    high: BoundSpec


class LeverConfig(Frozen):
    """One lever as written in config/studio/levers.yaml (DESIGN_CHOICE values)."""

    label: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    kind: LeverKind
    balanced_by: str | None = None
    default_bounds: DefaultBounds | None = None
    round_to: float | None = Field(default=None, gt=0)
    options: list[int] | None = None
    effort_weight: float = Field(ge=0)
    note: str = Field(min_length=1)


class LeversFile(Frozen):
    """The whole levers file. Lever names must be on the code's allow-list, with the code's kind and balancing."""

    version: str = Field(min_length=1)
    status: Literal["DESIGN_CHOICE"]
    levers: dict[str, LeverConfig]

    @model_validator(mode="after")
    def allowed_levers(self) -> "LeversFile":
        """Only allow-listed levers; each with the kind, balancing item and fields its code expects."""
        unknown = sorted(set(self.levers) - set(LEVER_NAMES))
        if unknown:
            raise ValueError(f"not a plan-choice lever: {unknown}; the allow-list is {list(LEVER_NAMES)}")
        for name, config in self.levers.items():
            if config.kind != LEVER_KINDS[name]:
                raise ValueError(f"lever {name} must have kind {LEVER_KINDS[name]!r}, not {config.kind!r}")
            if config.balanced_by != BALANCED_BY.get(name):
                raise ValueError(f"lever {name} is balanced by {BALANCED_BY.get(name)!r} in code")
            if config.kind not in ("site",) and config.default_bounds is None:
                raise ValueError(f"lever {name} needs default_bounds")
            if config.kind == "term" and not config.options:
                raise ValueError(f"lever {name} needs a list of term options")
            if config.kind in ("amount", "price", "days") and config.round_to is None:
                raise ValueError(f"lever {name} needs round_to")
        return self


class LeverBounds(Frozen):
    """Bounds the founder sets for one lever (absolute values in the lever's unit; None = the default)."""

    low: float | None = None
    high: float | None = None

    @model_validator(mode="after")
    def ordered(self) -> "LeverBounds":
        """low <= high when both are given."""
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"bounds need low <= high (got {self.low}, {self.high})")
        return self


def load_levers(path: Path = LEVERS_FILE) -> dict[str, LeverConfig]:
    """Read and validate the levers file; {lever name: config} in allow-list order."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must hold a YAML mapping")
    parsed = LeversFile.model_validate(data)
    return {name: parsed.levers[name] for name in LEVER_NAMES if name in parsed.levers}


def resolve_bounds(
    name: str,
    config: LeverConfig,
    current: float,
    project_cost: float,
    user: LeverBounds | None = None,
) -> tuple[float, float]:
    """(low, high) for one numeric lever. Founder bounds win over defaults and must include the current value."""
    if config.default_bounds is None:
        raise ValueError(f"lever {name} has no numeric bounds")
    low = config.default_bounds.low.resolve(current, project_cost)
    high = config.default_bounds.high.resolve(current, project_cost)
    low, high = min(low, current), max(high, current)
    if user is not None and user.low is not None:
        if user.low > current + VALUE_TOLERANCE:
            raise ValueError(f"{name}: your low bound {user.low} is above the plan's current value {current}")
        low = user.low
    if user is not None and user.high is not None:
        if user.high < current - VALUE_TOLERANCE:
            raise ValueError(f"{name}: your high bound {user.high} is below the plan's current value {current}")
        high = user.high
    if config.kind == "amount":
        low = max(low, 0.0)
    if name == "loan_amount":
        low = max(low, min(current, float(STARTUP_MIN_AMOUNT)))
    return low, high


def round_to_step(value: float, step: float) -> float:
    """value rounded to the nearest multiple of step (exact decimals for steps like 0.25 and 500)."""
    return round(round(value / step) * step, 6)


def round_inside(raw: float, step: float, low: float, high: float) -> float:
    """raw rounded to a multiple of step that stays inside [low, high] (the bound itself, to the cent, if none)."""
    rounded = round_to_step(raw, step)
    if low <= rounded <= high:
        return rounded
    if rounded > high:
        inside = round(math.floor(high / step) * step, 6)
    else:
        inside = round(math.ceil(low / step) * step, 6)
    if low <= inside <= high:
        return inside
    return round(min(max(raw, low), high), 2)


def side_values(current: float, bound: float, steps: int, step: float) -> list[float]:
    """steps values from current (excluded) towards bound (included), evenly spaced and rounded to step."""
    low, high = min(current, bound), max(current, bound)
    return [round_inside(current + (bound - current) * k / steps, step, low, high) for k in range(1, steps + 1)]


def grid(config: LeverConfig, current: float, low: float, high: float, steps: int) -> list[float]:
    """Every value the search tries for one numeric lever (the current value excluded), sorted."""
    if config.kind == "term":
        values = [float(option) for option in config.options or [] if low <= option <= high]
    elif config.kind == "days":
        values = [float(day) for day in range(math.ceil(low), math.floor(high) + 1)]
    else:
        step = config.round_to or 1.0
        values = side_values(current, low, steps, step) + side_values(current, high, steps, step)
    unique = sorted(set(values))
    return [value for value in unique if abs(value - current) > VALUE_TOLERANCE]


def change_size(config: LeverConfig, current: float, value: float, project_cost: float) -> float:
    """Size of a change: dollars / project cost (amount levers) or the relative change (price, days, term)."""
    if config.kind == "amount":
        return abs(value - current) / project_cost
    if current == 0:
        return abs(value - current)
    return abs(value - current) / abs(current)


def effort(config: LeverConfig, current: float | str, value: float | str, project_cost: float) -> float:
    """effort_weight x size of the change (a site switch has size 1)."""
    if config.kind == "site":
        return config.effort_weight
    return config.effort_weight * change_size(config, float(current), float(value), project_cost)
