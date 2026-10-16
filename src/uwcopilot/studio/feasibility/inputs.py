"""What the founder and SiteIQ hand to the simulator: the site's demand range, the plan and the loan.

SiteDemand is the ONLY contract between SiteIQ (S2) and the simulator (S3): a low / mode / high demand in the
profile's demand unit plus its provenance, and (from SiteIQ) the demand's percentiles 0-100, which the simulator
samples instead of a triangle, and whether the estimate is complete. as_site_demand() accepts a SiteDemand, a plain
dict, or any object that exposes low, mode, high and unit (directly, or under a `demand` / `demand_estimate`
attribute), so the simulator does not depend on SiteIQ's internal classes.

Plan inputs the founder typed are USER_STATED and fixed. A plan input left empty falls back to the profile's
ASSUMPTION range (or the metro's data) and is sampled; the result lists which one was used.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.studio.feasibility.distributions import check_points

SiteDemandStatus = Literal["DATA", "SYNTHETIC", "ASSUMPTION", "MODEL_ESTIMATE", "USER_STATED"]
OPTIONAL_SITE_FIELDS: tuple[str, ...] = (
    "status",
    "source",
    "coverage_score",
    "competition_percentile",
    "percentiles",
    "complete",
    "missing",
)


class Strict(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteDemand(Strict):
    """Mature demand at the site (after ramp-up, before seasonality), in the profile's demand unit.

    For a per-day unit (covers/day, cars/day, ...) the demand is per OPEN day. competition_percentile is where the
    site's competitor density ranks within the metro (0 = least saturated, 1 = most); it moves the chance that a
    competitor opens (setup.py). coverage_score is SiteIQ's data-coverage score (0-1), shown next to the results.
    """

    low: float = Field(ge=0)
    mode: float = Field(ge=0)
    high: float = Field(ge=0)
    unit: str = Field(min_length=1)
    status: SiteDemandStatus = "MODEL_ESTIMATE"
    source: str = Field(default="SiteIQ demand estimate (S2)", min_length=1)
    coverage_score: float | None = Field(default=None, ge=0, le=1)
    competition_percentile: float | None = Field(default=None, ge=0, le=1)
    percentiles: list[float] | None = Field(
        default=None,
        description="Demand at the percentiles 0%, 1%, ..., 100% (SiteIQ's Monte Carlo over its rate ranges). When "
        "present the simulator samples these; otherwise a triangle over low / mode / high",
    )
    complete: bool = Field(default=True, description="False when SiteIQ left a formula term out (its data UNKNOWN)")
    missing: list[str] = Field(default_factory=list, description="The terms left out and why")

    @model_validator(mode="after")
    def ordered(self) -> "SiteDemand":
        """low <= mode <= high, a positive mode, and percentiles (when given) that run from low to high."""
        if not self.low <= self.mode <= self.high:
            raise ValueError(f"site demand needs low <= mode <= high (got {self.low}, {self.mode}, {self.high})")
        if self.mode <= 0:
            raise ValueError("site demand mode must be positive")
        if self.percentiles is not None:
            check_points("site demand", self.percentiles, self.low, self.high)
        return self


def as_site_demand(value: Any) -> SiteDemand:
    """Turn SiteIQ's output (or a dict) into a SiteDemand. Raises TypeError when no demand range can be found.

    For a SiteIQ-shaped result (an object with a `demand_estimate` or `demand` attribute), the site's
    coverage_score and the percentile of its competition factor are carried over when present.
    """
    if isinstance(value, SiteDemand):
        return value
    if isinstance(value, Mapping):
        return SiteDemand.model_validate(dict(value))
    for attribute in ("demand_estimate", "demand"):
        nested = getattr(value, attribute, None)
        if nested is not None and nested is not value:
            return with_site_context(as_site_demand(nested), value)
    if all(hasattr(value, name) for name in ("low", "mode", "high", "unit")):
        return from_estimate(value)
    raise TypeError(f"cannot read a demand range (low, mode, high, unit) from {type(value).__name__}")


def from_estimate(estimate: Any) -> SiteDemand:
    """A SiteDemand from an object with low, mode, high and unit (for example SiteIQ's DemandEstimate)."""
    if any(getattr(estimate, name) is None for name in ("low", "mode", "high")):
        raise ValueError("the site's demand is UNKNOWN (no estimate); enter an expected demand range instead")
    fields: dict[str, Any] = {name: getattr(estimate, name) for name in ("low", "mode", "high", "unit")}
    for name in OPTIONAL_SITE_FIELDS:
        if getattr(estimate, name, None) is not None:
            fields[name] = getattr(estimate, name)
    basis = getattr(estimate, "basis", None)
    if "status" not in fields and basis == "ASSUMPTION rates only":
        fields["status"] = "ASSUMPTION"
    if "source" not in fields and basis is not None:
        fields["source"] = f"SiteIQ demand estimate (S2), basis: {basis}"
    return SiteDemand.model_validate(fields)


def with_site_context(site: SiteDemand, score: Any) -> SiteDemand:
    """Add the site's coverage score and competition percentile (0-1) from a SiteIQ-shaped result."""
    updates: dict[str, Any] = {}
    coverage = getattr(score, "coverage_score", None)
    if site.coverage_score is None and coverage is not None:
        updates["coverage_score"] = coverage
    if site.competition_percentile is None:
        for factor in getattr(score, "factors", None) or []:
            if getattr(factor, "factor", None) == "competition" and getattr(factor, "percentile", None) is not None:
                updates["competition_percentile"] = factor.percentile / 100.0
    if not updates:
        return site
    return SiteDemand.model_validate({**site.model_dump(), **updates})


class RangeOverride(Strict):
    """A user's edit of one assumption range (Studio rule D: assumptions are user-editable)."""

    low: float
    mode: float
    high: float
    note: str = Field(default="edited by the user", min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> "RangeOverride":
        """low <= mode <= high."""
        if not self.low <= self.mode <= self.high:
            raise ValueError(f"need low <= mode <= high (got {self.low}, {self.mode}, {self.high})")
        return self


class Plan(Strict):
    """The founder's plan. Empty optional fields fall back to the profile ranges (and are sampled)."""

    opening_date: date
    owner_equity: float = Field(ge=0, description="Owner cash put into the project, USD")
    capex: float | None = Field(default=None, gt=0, description="Build-out and equipment, USD; empty = profile range")
    price: float | None = Field(default=None, gt=0, description="Price per demand unit, USD; empty = profile range")
    monthly_rent: float | None = Field(default=None, ge=0, description="Rent quote, USD/month; empty = metro range")
    paid_hours_per_month: float | None = Field(default=None, ge=0, description="Staffing plan; empty = profile range")
    hourly_wage: float | None = Field(default=None, gt=0, description="Planned wage, USD/hour; empty = metro wage")
    capacity: float | None = Field(default=None, gt=0, description="Most demand served, profile demand unit")
    days_open_per_week: float | None = Field(default=None, gt=0, le=7)
    owner_draw_per_month: float = Field(default=0.0, ge=0, description="Cash the owner takes out each month, USD")
    assumption_overrides: dict[str, RangeOverride] = Field(
        default_factory=dict, description="{distribution name: edited range}; unknown names are rejected"
    )


class Loan(Strict):
    """The requested loan. A fully amortizing term loan (the only product the startup program offers).

    annual_rate_pct empty = the metro's prime rate plus the spread (rate_spread_pct, or the ASSUMPTION mode in
    config/studio/feasibility/assumptions.yaml). Prime-based pricing is usually variable, hence the default.
    """

    amount: float = Field(gt=0)
    term_months: int = Field(ge=24, le=360, description="At least 24 so years 1 and 2 both have debt service")
    annual_rate_pct: float | None = Field(default=None, ge=0, le=30)
    rate_spread_pct: float | None = Field(default=None, ge=0, le=15)
    rate_type: Literal["fixed", "variable"] = "variable"
    product: Literal["term_loan"] = "term_loan"
