"""Pydantic models for SiteIQ's inputs and outputs (Studio S2).

Every output says where its numbers came from:
- a factor carries its raw value, its status (DATA, SYNTHETIC, ASSUMPTION, UNKNOWN or FOUNDER_INPUT), the
  SourceRecords it was computed from, its normalized score with the method stated, its weight and a plain reason;
- the demand estimate lists every input (data values and profile rates) with its status and source;
- every result carries the data-coverage score, notes for ASSUMPTION and UNKNOWN inputs, the metro mode and the
  Studio disclaimer.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.studio import DISCLAIMER
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.metros.models import MetroMode

# FOUNDER_INPUT: a value the founder typed in for this site (for example a rent quote). It is not checked by Studio.
# SOURCED: a profile rate taken from a cited publication (shown as SOURCED, never as tract DATA).
SiteStatus = Literal["DATA", "SYNTHETIC", "ASSUMPTION", "SOURCED", "UNKNOWN", "FOUNDER_INPUT"]
# Statuses that count as "backed by data" for the coverage score and for the overall score.
COVERED_STATUSES: tuple[str, ...] = ("DATA", "SYNTHETIC", "FOUNDER_INPUT")
TradeAreaMethod = Literal["rings", "drive_time"]
# Name and version of the scoring method; stored with every result so a score can be traced to the code that made it.
SCORING_METHOD = "siteiq-v1: percentile rank within the metro per factor, weighted mean of the scored factors"


class Frozen(BaseModel):
    """Base model: unknown keys are an error and results cannot be changed after they are built."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteInputs(Frozen):
    """What the founder tells SiteIQ about one candidate site (all optional)."""

    label: str | None = Field(default=None, max_length=200, description="The founder's name for the site")
    rent_quote_usd_per_month: float | None = Field(default=None, gt=0, description="A rent quote for this site")


class Budget(Frozen):
    """The founder's budget limits, used by suggest_areas to check the metro's ASSUMPTION ranges."""

    max_monthly_rent_usd: float | None = Field(default=None, gt=0)
    max_build_out_usd: float | None = Field(default=None, gt=0)


class TradeAreaInfo(Frozen):
    """How the trade area was drawn. Scores always use distance rings; the demand estimate may use drive time."""

    scoring_method: Literal["rings"] = "rings"
    demand_method: TradeAreaMethod
    rings_miles: list[float]
    near_miles: float = Field(description="Ring for counts of nearby places (competitors, anchors, comparables)")
    market_miles: float = Field(description="Ring for population sums and tourism anchors")
    ring_multiplier: float
    drive_minutes: list[int] = Field(default_factory=list)
    demand_area_label: str
    reason: str
    source: SourceRecord | None = None


class FactorScore(Frozen):
    """One location factor of one site: raw value, normalized score, weight and a plain-language reason."""

    factor: str
    description: str
    unit: str
    raw_value: float | None
    status: SiteStatus
    area: str = Field(description="Where the value was measured, e.g. 'within 1.5 miles'")
    sources: list[SourceRecord] = Field(default_factory=list)
    normalized_score: float | None = Field(default=None, ge=0, le=100)
    percentile: float | None = Field(default=None, ge=0, le=100)
    normalization: str
    reference_size: int = Field(ge=0, description="How many metro locations the value was ranked against")
    weight: float = Field(ge=0, le=1)
    direction: Literal["higher_is_better", "lower_is_better"]
    used_in_overall: bool
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class DemandInput(Frozen):
    """One input of the demand estimate: a measured data value or a profile rate."""

    name: str
    role: Literal["data", "rate", "context"]
    value: float | None = None
    low: float | None = None
    high: float | None = None
    unit: str
    status: SiteStatus
    reason: str
    profile_field: str | None = Field(default=None, description="Dotted path of the rate in the profile YAML")
    sources: list[SourceRecord] = Field(default_factory=list)


class DemandEstimate(Frozen):
    """Demand in the profile's unit (low / mode / high) with every input listed. None values mean UNKNOWN."""

    formula: str
    formula_description: str
    unit: str
    low: float | None = Field(description="Every rate at its low end at once: a hard bound, not a percentile")
    mode: float | None = Field(description="Every rate at its mode")
    high: float | None = Field(description="Every rate at its high end at once: a hard bound, not a percentile")
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    percentiles: list[float] | None = Field(
        default=None,
        description="Percentiles 0, 1, ..., 100 (studio.PERCENTILE_LEVELS) of the demand with each rate sampled "
        "independently from its range; what the simulator samples (sample_method says how they were computed)",
    )
    sample_method: str | None = None
    basis: Literal["data x ASSUMPTION rates", "ASSUMPTION rates only", "UNKNOWN"]
    complete: bool = Field(description="False when a term of the formula was left out because its data is UNKNOWN")
    missing: list[str] = Field(default_factory=list)
    method: str
    area: str
    inputs: list[DemandInput]
    capacity_note: str | None = None


class ContextItem(Frozen):
    """A metro enhancement shown next to the score but not used in it (no metro-wide reference exists for it)."""

    name: str
    value: float | None
    status: SiteStatus
    reason: str
    source: SourceRecord | None = None


class SiteScore(Frozen):
    """The full SiteIQ result for one site."""

    scoring_method: str = SCORING_METHOD
    profile_id: str
    metro_id: str
    metro_name: str
    data_label: Literal["PUBLIC", "SYNTHETIC"]
    mode: MetroMode
    mode_reason: str
    lat: float
    lon: float
    label: str | None = None
    tract_geoid: str | None
    district: str | None = None
    trade_area: TradeAreaInfo
    factors: list[FactorScore]
    overall_score: float | None = Field(ge=0, le=100, description="Weighted mean of the scored factors (0-100)")
    scored_weight_share: float = Field(ge=0, le=1, description="Share of the profile's weight that entered overall")
    coverage_score: float = Field(ge=0, le=1, description="Weighted share of required factors backed by data")
    demand: DemandEstimate
    metro_inputs: dict[str, FactorValue]
    context: list[ContextItem] = Field(default_factory=list)
    notes: list[str]
    disclaimer: str = DISCLAIMER


class SuggestedArea(Frozen):
    """One suggested census tract with its score, its strongest reasons and the factor scores behind them."""

    rank: int = Field(ge=1)
    geoid: str
    lat: float
    lon: float
    district: str | None = None
    overall_score: float = Field(ge=0, le=100)
    coverage_score: float = Field(ge=0, le=1)
    reasons: list[str]
    watch_out: str | None = None
    factor_scores: dict[str, float | None]
    demand_mode: float | None
    demand_unit: str


class Exclusion(Frozen):
    """How many tracts were left out for one reason (required data missing, too little data to score)."""

    reason: str
    count: int = Field(ge=0)


class BudgetCheck(Frozen):
    """What the budget could and could not be checked against."""

    filters_tracts: bool
    notes: list[str]


class Suggestions(Frozen):
    """The result of suggest_areas: the top tracts, what was excluded and why, and a GeoJSON map layer."""

    scoring_method: str = SCORING_METHOD
    profile_id: str
    metro_id: str
    metro_name: str
    data_label: Literal["PUBLIC", "SYNTHETIC"]
    mode: MetroMode
    mode_reason: str
    n_tracts: int
    n_ranked: int
    areas: list[SuggestedArea]
    excluded: list[Exclusion]
    budget: BudgetCheck
    geojson: dict[str, Any]
    notes: list[str]
    disclaimer: str = DISCLAIMER
