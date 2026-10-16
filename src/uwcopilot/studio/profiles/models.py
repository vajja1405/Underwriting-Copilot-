"""Pydantic models for a business-type profile (config/studio/business_types/<id>.yaml).

The central rule (Studio addendum rule D): every numeric field is a Quantity or a Curve that says where it came
from.

- status SOURCED: the number comes from a primary source somebody opened. It must carry source{publisher, title,
  date, url, page_or_table}.
- status ASSUMPTION: the project author's assumption. It must be a range (low <= mode <= high, low < high) and carry a
  note saying which source should replace it. It is user-editable in the app.

The location-factor weights and trade-area rings are not facts about the world; they are design choices of the
scoring method and are labeled DESIGN_CHOICE (see DesignParameters).
"""

import re
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uwcopilot.studio.profiles.factors import DEMAND_FORMULAS, LOCATION_FACTORS

Status = Literal["SOURCED", "ASSUMPTION"]
PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
NAICS_CODE = re.compile(r"^\d{2,6}$")
# An OpenStreetMap tag filter: "key", "key=*" or "key=value". Only these characters are allowed, so a tag can never
# inject Overpass QL (the tags are config, but the rule keeps the connector safe if tags ever come from a user).
OSM_TAG = re.compile(r"^[a-z][a-z0-9_:]*(=(\*|[A-Za-z0-9_:\-]+))?$")
REQUIRED_COST_RATIOS: tuple[str, ...] = ("cogs", "utilities", "supplies", "marketing", "insurance", "maintenance")
MONTHS_PER_YEAR = 12
WEIGHT_SUM_TOLERANCE = 1e-6
SEASONALITY_MEAN_TOLERANCE = 0.01


class StrictModel(BaseModel):
    """Base model: unknown keys are an error, so a typo in YAML never passes silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(StrictModel):
    """A primary source somebody opened. Every field is required."""

    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    date: str = Field(min_length=1, description="Publication or release date as printed on the source")
    url: str = Field(min_length=1)
    page_or_table: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def url_is_http(cls, value: str) -> str:
        """A source URL must be an http(s) link someone can open."""
        if not value.startswith(("https://", "http://")):
            raise ValueError(f"source url must start with http:// or https:// (got {value!r})")
        return value


def check_provenance(status: str, source: Source | None, note: str | None) -> None:
    """SOURCED needs a source; ASSUMPTION needs a note naming the source that should replace it."""
    if status == "SOURCED" and source is None:
        raise ValueError("a SOURCED field must carry a source {publisher, title, date, url, page_or_table}")
    if status == "ASSUMPTION" and not (note and note.strip()):
        raise ValueError("an ASSUMPTION field must carry a note saying which source should replace it")


class Quantity(StrictModel):
    """One numeric field: a single value or a low/mode/high range, with its unit and provenance."""

    value: float | None = None
    low: float | None = None
    mode: float | None = None
    high: float | None = None
    unit: str = Field(min_length=1)
    status: Status
    source: Source | None = None
    note: str | None = None

    @model_validator(mode="after")
    def check_shape(self) -> "Quantity":
        """Either value alone or all of low/mode/high (low <= mode <= high); ASSUMPTIONs must be ranges."""
        range_parts = (self.low, self.mode, self.high)
        has_range = all(part is not None for part in range_parts)
        if any(part is not None for part in range_parts) and not has_range:
            raise ValueError("a range needs all three of low, mode and high")
        if self.value is not None and has_range:
            raise ValueError("give either value or low/mode/high, not both")
        if self.value is None and not has_range:
            raise ValueError("give value or low/mode/high")
        if has_range and not (self.low <= self.mode <= self.high):
            raise ValueError(f"need low <= mode <= high (got {self.low}, {self.mode}, {self.high})")
        if self.status == "ASSUMPTION" and not (has_range and self.low < self.high):
            raise ValueError("an ASSUMPTION must be a range with low < high, never a single number")
        check_provenance(self.status, self.source, self.note)
        return self

    def point(self) -> float:
        """The single best value: value, or the mode of the range."""
        return self.value if self.value is not None else float(self.mode)

    def bounds(self) -> tuple[float, float]:
        """(low, high); a single value gives (value, value)."""
        if self.value is not None:
            return self.value, self.value
        return float(self.low), float(self.high)


class Curve(StrictModel):
    """A list of values with one provenance (for example 12 monthly seasonality factors)."""

    values: list[float] = Field(min_length=1)
    unit: str = Field(min_length=1)
    status: Status
    source: Source | None = None
    note: str | None = None

    @model_validator(mode="after")
    def check_provenance_fields(self) -> "Curve":
        """Same provenance rule as Quantity."""
        check_provenance(self.status, self.source, self.note)
        return self


class NaicsVerification(StrictModel):
    """Where a NAICS code and its title were checked: a published code list, with its URL, date and SHA-256.

    Written from the output of scripts/studio_verify_naics.py (results/studio_naics_check.json). It confirms that
    the code exists in the list with this title; whether the code is the right one for the business type is still
    the project author's choice.
    """

    publisher: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    retrieved_at_utc: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_title: str = Field(min_length=1, description="The list's title text for the code, as published")


class NaicsCode(StrictModel):
    """A NAICS industry code used to query Business Patterns and BLS wages.

    verified is true only when the code and its title were found in a published code list (verification says
    which one). www.census.gov/naics answered 403 from the build environment (2026-10-12 and 2026-10-13), so the
    list used is the BLS QCEW industry titles file.
    """

    code: str
    title: str = Field(min_length=1)
    verified: bool = False
    verification: NaicsVerification | None = None
    note: str | None = None

    @field_validator("code")
    @classmethod
    def code_is_digits(cls, value: str) -> str:
        """NAICS codes are 2 to 6 digits."""
        if not NAICS_CODE.match(value):
            raise ValueError(f"NAICS code must be 2-6 digits (got {value!r})")
        return value

    @model_validator(mode="after")
    def verified_needs_a_record(self) -> "NaicsCode":
        """verified: true needs the verification record; a record without verified: true is a contradiction."""
        if self.verified and self.verification is None:
            raise ValueError(f"NAICS {self.code}: verified is true but there is no verification record")
        if self.verification is not None and not self.verified:
            raise ValueError(f"NAICS {self.code}: a verification record is present but verified is false")
        return self


class DemandModel(StrictModel):
    """Which demand formula the type uses (profiles.factors.DEMAND_FORMULAS) and the rates it needs."""

    formula: str
    inputs: dict[str, Quantity]

    @model_validator(mode="after")
    def inputs_match_formula(self) -> "DemandModel":
        """The formula must exist and the inputs must be exactly the ones it needs."""
        if self.formula not in DEMAND_FORMULAS:
            raise ValueError(f"unknown demand formula {self.formula!r}; known: {sorted(DEMAND_FORMULAS)}")
        required = set(DEMAND_FORMULAS[self.formula].required_inputs)
        given = set(self.inputs)
        if given != required:
            raise ValueError(
                f"formula {self.formula!r} needs inputs {sorted(required)}; "
                f"missing {sorted(required - given)}, unexpected {sorted(given - required)}"
            )
        return self


class Capacity(StrictModel):
    """The most demand the business can serve, in the profile's demand unit, and what limits it."""

    max_demand: Quantity
    basis: str = Field(min_length=1, description="What limits capacity, e.g. 'seats x table turns'")


class OperatingHours(StrictModel):
    """Opening hours."""

    hours_per_day: Quantity
    days_per_week: Quantity


class Labor(StrictModel):
    """Labor cost per month = paid hours per month x hourly wage (the wage is replaced by BLS data per metro)."""

    paid_hours_per_month: Quantity
    hourly_wage: Quantity


class Rent(StrictModel):
    """Rent: the founder's quote when there is one, else the metro ASSUMPTION range (no free commercial-rent source)."""

    handling: Literal["founder_quote_else_metro_range"]
    monthly_rent: Quantity
    annual_escalation: Quantity


class CapexItem(StrictModel):
    """One build-out or equipment line."""

    name: str = Field(min_length=1)
    cost: Quantity


class RampUp(StrictModel):
    """How demand grows after opening: starting share of mature demand and months until maturity."""

    starting_share: Quantity
    months_to_maturity: Quantity


class FactorWeight(StrictModel):
    """How much one location factor counts in the site score, and which direction is good."""

    weight: float = Field(gt=0, le=1)
    direction: Literal["higher_is_better", "lower_is_better"]


class DesignParameters(StrictModel):
    """Scoring-method choices (not facts about the world): factor weights and trade-area rings."""

    status: Literal["DESIGN_CHOICE"]
    note: str = Field(min_length=1)
    location_weights: dict[str, FactorWeight]
    trade_area_rings_miles: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def check_weights_and_rings(self) -> "DesignParameters":
        """Weights name known factors and sum to 1; rings are positive and increasing."""
        unknown = sorted(set(self.location_weights) - set(LOCATION_FACTORS))
        if unknown:
            raise ValueError(f"unknown location factors {unknown}; known: {sorted(LOCATION_FACTORS)}")
        total = sum(item.weight for item in self.location_weights.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"location weights must sum to 1 (got {total})")
        rings = self.trade_area_rings_miles
        if any(ring <= 0 for ring in rings) or any(b <= a for a, b in zip(rings, rings[1:], strict=False)):
            raise ValueError(f"trade-area rings must be positive and increasing (got {rings})")
        return self


class Profile(StrictModel):
    """One business type. See config/studio/business_types/*.yaml and docs/studio/business_types.md."""

    id: str
    display_name: str = Field(min_length=1)
    naics: list[NaicsCode] = Field(min_length=1)
    demand_unit: str = Field(min_length=1)
    demand: DemandModel
    price: Quantity
    capacity: Capacity
    # None for types without opening hours (a short-term rental is "open" whenever it is booked).
    hours: OperatingHours | None = None
    cost_ratios: dict[str, Quantity]
    labor: Labor
    rent: Rent
    capex: list[CapexItem] = Field(min_length=1)
    seasonality: Curve
    ramp_up: RampUp
    design: DesignParameters
    competitor_tags: list[str] = Field(min_length=1)
    anchor_tags: list[str] = Field(min_length=1)
    key_risks: list[str] = Field(min_length=1)
    # {metro id: {dotted field path: replacement Quantity or Curve}}; see loader.apply_metro_overrides.
    metro_overrides: dict[str, dict[str, Quantity | Curve]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, value: str) -> str:
        """Profile ids are lowercase snake_case (they are also file names)."""
        if not PROFILE_ID.match(value):
            raise ValueError(f"profile id must be lowercase snake_case (got {value!r})")
        return value

    @field_validator("competitor_tags", "anchor_tags")
    @classmethod
    def tags_are_safe(cls, tags: list[str]) -> list[str]:
        """Every tag is 'key', 'key=*' or 'key=value' with a restricted character set."""
        for tag in tags:
            if not OSM_TAG.match(tag):
                raise ValueError(f"invalid OpenStreetMap tag filter {tag!r} (use key, key=* or key=value)")
        return tags

    @model_validator(mode="after")
    def check_profile(self) -> "Profile":
        """Cross-field rules: demand unit, cost ratios, seasonality shape, demand factors weighted."""
        formula = DEMAND_FORMULAS[self.demand.formula]
        if formula.demand_unit != self.demand_unit:
            raise ValueError(
                f"demand_unit {self.demand_unit!r} does not match formula {formula.name!r} ({formula.demand_unit!r})"
            )
        missing = sorted(set(REQUIRED_COST_RATIOS) - set(self.cost_ratios))
        if missing:
            raise ValueError(f"cost_ratios is missing {missing}")
        for name, ratio in self.cost_ratios.items():
            low, high = ratio.bounds()
            if low < 0 or high > 1:
                raise ValueError(f"cost ratio {name!r} must be a share of revenue between 0 and 1")
        if len(self.seasonality.values) != MONTHS_PER_YEAR:
            raise ValueError("seasonality needs 12 monthly values")
        if any(value <= 0 for value in self.seasonality.values):
            raise ValueError("seasonality values must be positive")
        mean = sum(self.seasonality.values) / MONTHS_PER_YEAR
        if abs(mean - 1.0) > SEASONALITY_MEAN_TOLERANCE:
            raise ValueError(f"seasonality values must average 1.0 (got {mean:.4f})")
        return self


def iter_numeric_fields(node: Any, path: str = "") -> Iterator[tuple[str, Quantity | Curve]]:
    """Yield (dotted path, field) for every Quantity and Curve inside a profile (or any part of one).

    Used by tests (no SOURCED field without a source), by the coverage report and later by the ledger (S4).
    """
    if isinstance(node, Quantity | Curve):
        yield path, node
        return
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            child_path = f"{path}.{name}" if path else name
            yield from iter_numeric_fields(getattr(node, name), child_path)
    elif isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from iter_numeric_fields(value, child_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_numeric_fields(value, f"{path}[{index}]")
