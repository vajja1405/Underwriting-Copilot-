"""The distributions the simulator samples, each listed in the result with its status and source.

Every uncertain input is a triangular distribution (low, mode, high), the same shape the profiles use for their
ASSUMPTION ranges. An input the founder typed (a rent quote, a price) is a fixed value with status USER_STATED.
The site's demand from SiteIQ is "empirical": its percentiles 0%, 1%, ..., 100% (studio.PERCENTILE_LEVELS), sampled
by linear interpolation between them; its `mode` keeps SiteIQ's most likely value (every rate at its mode), which the
one-forecast case uses.

Sampling uses the inverse-CDF method: each path draws one uniform number u in (0, 1) per input and the value is
quantile(u). Two reasons:
1. The tornado (tornado.py) can set one input to its P10 or P90 while every other input keeps exactly the same
   draws ("holding the seed"), so the swing comes from that input alone.
2. quantile(0.1) and quantile(0.9) are exact, so the tornado's P10 and P90 are not estimated from samples.
"""

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.studio import PERCENTILE_LEVELS
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.profiles.models import Quantity

DistKind = Literal["triangular", "fixed", "empirical"]
# Where an input came from: typed by the founder, a profile or simulator assumption, a published source, public data,
# public data converted with a labeled assumption (DATA_X_ASSUMPTION, e.g. a weekly wage / assumed weekly hours), the
# SYNTHETIC city, or a model estimate (for example SiteIQ's demand range).
InputStatus = Literal[
    "USER_STATED", "ASSUMPTION", "SOURCED", "DATA", "DATA_X_ASSUMPTION", "SYNTHETIC", "MODEL_ESTIMATE"
]
# Data-fabric statuses that carry a usable value (UNKNOWN has none).
STATUS_FROM_FACTOR: dict[str, InputStatus] = {
    "DATA": "DATA",
    "SYNTHETIC": "SYNTHETIC",
    "ASSUMPTION": "ASSUMPTION",
    "DATA_X_ASSUMPTION": "DATA_X_ASSUMPTION",
}


class Distribution(BaseModel):
    """One sampled input: its shape, unit, status and where it came from (shown in every result)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    kind: DistKind
    low: float
    mode: float
    high: float
    status: InputStatus
    source: str = Field(min_length=1, description="Plain-language provenance: which profile field, quote or dataset")
    points: list[float] | None = Field(
        default=None, description="Empirical only: the values at PERCENTILE_LEVELS (0%, 1%, ..., 100%)"
    )

    @model_validator(mode="after")
    def check_shape(self) -> "Distribution":
        """low <= mode <= high; a fixed value has low == mode == high; an empirical one has ordered points."""
        if not self.low <= self.mode <= self.high:
            raise ValueError(f"{self.name}: need low <= mode <= high (got {self.low}, {self.mode}, {self.high})")
        if self.kind == "fixed" and not self.low == self.mode == self.high:
            raise ValueError(f"{self.name}: a fixed value needs low == mode == high")
        if self.kind == "empirical":
            check_points(self.name, self.points, self.low, self.high)
        elif self.points is not None:
            raise ValueError(f"{self.name}: only an empirical distribution has points")
        return self

    @property
    def varies(self) -> bool:
        """True when the input is uncertain (a range), so it can appear in the tornado."""
        return self.high > self.low

    def quantile(self, u: np.ndarray | float) -> np.ndarray:
        """The value at cumulative probability u (vectorized). Fixed values ignore u."""
        u_array = np.asarray(u, dtype=float)
        if self.kind == "empirical" and self.points is not None:
            return np.interp(u_array, PERCENTILE_LEVELS, self.points)
        if not self.varies:
            return np.full(u_array.shape, self.mode, dtype=float)
        return triangular_quantile(u_array, self.low, self.mode, self.high)

    def percentile(self, share: float) -> float:
        """One percentile of the distribution, for example percentile(0.1) is its P10."""
        return float(self.quantile(share))

    def mean(self) -> float:
        """The mean of the distribution ((low + mode + high) / 3 for a triangle).

        Empirical: the mean of the piecewise-linear quantile function (the average of each step's midpoint).
        """
        if self.kind == "empirical" and self.points is not None:
            points = np.asarray(self.points)
            return float(np.mean((points[:-1] + points[1:]) / 2.0))
        return (self.low + self.mode + self.high) / 3.0


def check_points(name: str, points: list[float] | None, low: float, high: float) -> None:
    """Percentile points of an empirical distribution: one per level, non-decreasing, from low to high."""
    if points is None or len(points) != len(PERCENTILE_LEVELS):
        raise ValueError(f"{name}: an empirical distribution needs {len(PERCENTILE_LEVELS)} percentile points")
    if any(later < earlier for earlier, later in zip(points, points[1:], strict=False)):
        raise ValueError(f"{name}: percentile points must not decrease")
    if not (math.isclose(points[0], low, abs_tol=1e-6) and math.isclose(points[-1], high, abs_tol=1e-6)):
        raise ValueError(f"{name}: the 0th and 100th percentiles must equal low and high")


def triangular_quantile(u: np.ndarray, low: float, mode: float, high: float) -> np.ndarray:
    """Inverse CDF of the triangular distribution (low < high).

    Below the mode: low + sqrt(u x (high - low) x (mode - low)).
    Above the mode: high - sqrt((1 - u) x (high - low) x (high - mode)).
    """
    width = high - low
    split = (mode - low) / width
    left = low + np.sqrt(u * width * (mode - low))
    right = high - np.sqrt((1.0 - u) * width * (high - mode))
    return np.where(u < split, left, right)


def triangular(
    name: str, description: str, unit: str, low: float, mode: float, high: float, status: InputStatus, source: str
) -> Distribution:
    """A triangular distribution (a fixed value when low == high)."""
    kind: DistKind = "triangular" if high > low else "fixed"
    return Distribution(
        name=name,
        description=description,
        unit=unit,
        kind=kind,
        low=low,
        mode=mode,
        high=high,
        status=status,
        source=source,
    )


def empirical(
    name: str, description: str, unit: str, points: list[float], mode: float, status: InputStatus, source: str
) -> Distribution:
    """A distribution given by its percentiles 0%, 1%, ..., 100%; mode is the most likely value (one-forecast case)."""
    return Distribution(
        name=name,
        description=description,
        unit=unit,
        kind="empirical",
        low=points[0],
        mode=mode,
        high=points[-1],
        status=status,
        source=source,
        points=list(points),
    )


def fixed(name: str, description: str, unit: str, value: float, status: InputStatus, source: str) -> Distribution:
    """A value that is not sampled (for example a rent quote the founder typed)."""
    return Distribution(
        name=name,
        description=description,
        unit=unit,
        kind="fixed",
        low=value,
        mode=value,
        high=value,
        status=status,
        source=source,
    )


def from_quantity(name: str, description: str, quantity: Quantity, where: str) -> Distribution:
    """A profile or simulator Quantity as a distribution; the source says which field it was and why."""
    status: InputStatus = "SOURCED" if quantity.status == "SOURCED" else "ASSUMPTION"
    detail = quantity.note or ""
    if quantity.source is not None:
        detail = f"{quantity.source.publisher}, {quantity.source.title} ({quantity.source.date}), {quantity.source.url}"
    source = f"{where} ({quantity.status}): {detail}".strip()
    if quantity.value is not None:
        return fixed(name, description, quantity.unit, quantity.value, status, source)
    return triangular(
        name,
        description,
        quantity.unit,
        float(quantity.low),
        float(quantity.mode),
        float(quantity.high),
        status,
        source,
    )


def from_factor_value(name: str, description: str, factor: FactorValue) -> Distribution:
    """A data-fabric FactorValue (local wages, metro rent range) as a distribution.

    A value with low and high becomes a triangle around the value; a single value is fixed. UNKNOWN values cannot
    be used (the caller falls back to the profile's ASSUMPTION range and says so). Assumptions the fabric used to
    derive the value (for example hours per week to turn a weekly wage into an hourly one) are kept in the source.
    """
    if factor.value is None or factor.status not in STATUS_FROM_FACTOR:
        raise ValueError(f"{factor.factor} is UNKNOWN: {factor.reason}")
    status = STATUS_FROM_FACTOR[factor.status]
    where = factor.source.dataset if factor.source is not None else "no dataset"
    source = f"data fabric {factor.factor} ({factor.status}): {factor.reason}; {where}"
    if factor.assumptions:
        source = f"{source}; derived with: {'; '.join(factor.assumptions)}"
    low = factor.low if factor.low is not None else factor.value
    high = factor.high if factor.high is not None else factor.value
    mode = min(max(factor.value, low), high)
    return triangular(name, description, factor.unit, low, mode, high, status, source)


def with_mode(distribution: Distribution, mode: float, reason: str) -> Distribution:
    """A copy of a triangular distribution with a new mode (clamped to its range) and the reason added."""
    clamped = min(max(mode, distribution.low), distribution.high)
    return distribution.model_copy(update={"mode": clamped, "source": f"{distribution.source}; {reason}"})


def annual_to_monthly_hazard(annual_probability: np.ndarray) -> np.ndarray:
    """Monthly probability h such that 1 - (1 - h)^12 equals the annual probability."""
    probability = np.clip(np.asarray(annual_probability, dtype=float), 0.0, 1.0)
    return 1.0 - np.power(1.0 - probability, 1.0 / 12.0)


def event_month(u: np.ndarray, annual_probability: np.ndarray, months: int) -> np.ndarray:
    """The 0-based month of the first event, or -1 when none happens within the horizon (vectorized over paths).

    The month of the first event with a constant monthly hazard h is geometric; its inverse CDF is
    floor(log(1 - u) / log(1 - h)). Holding u fixed, a higher probability can only move the event earlier.
    """
    uniforms = np.asarray(u, dtype=float)
    hazard = np.broadcast_to(annual_to_monthly_hazard(annual_probability), uniforms.shape)
    result = np.full(uniforms.shape, -1, dtype=int)
    positive = hazard > 0
    with np.errstate(divide="ignore"):  # a certain event (hazard 1) divides by log(0) = -inf: month 0
        month = np.floor(np.log1p(-uniforms[positive]) / np.log1p(-hazard[positive]))
    result[positive] = np.where(month < months, month, -1).astype(int)
    return result
