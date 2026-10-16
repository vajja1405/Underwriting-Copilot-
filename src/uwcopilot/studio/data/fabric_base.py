"""The data fabric's interface (DataFabric) and the value types it returns. See fabric.py for the overview."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.studio.data.geo import haversine_miles_array
from uwcopilot.studio.data.osm import Poi
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.data.str_comps import StrListing
from uwcopilot.studio.data.traffic_hpms import RoadSegment
from uwcopilot.studio.metros.models import MetroConfig, MetroMode
from uwcopilot.studio.profiles.factors import DEMAND_FORMULAS, LOCATION_FACTORS
from uwcopilot.studio.profiles.models import Profile

# DATA_X_ASSUMPTION: a public data value converted with a labeled assumption (for example a QCEW weekly wage divided
# by assumed weekly hours). The data part is real, but the value and its range depend on the assumption, so it is
# never shown as plain DATA (Studio rule D) and does not count as data coverage.
FactorStatus = Literal["DATA", "SYNTHETIC", "ASSUMPTION", "DATA_X_ASSUMPTION", "UNKNOWN"]
TRACT_FACTORS: tuple[str, ...] = tuple(name for name, item in LOCATION_FACTORS.items() if item.scope == "tract")
# A site is "on" a road when the nearest segment with traffic counts is within this distance (a design choice).
ROAD_SEARCH_MILES = 0.5


class FactorValue(BaseModel):
    """One factor's value with its status, reason and provenance. value None means UNKNOWN."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str
    value: float | None = None
    low: float | None = None
    high: float | None = None
    unit: str
    status: FactorStatus
    reason: str = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list, description="Assumptions used to derive the value")
    source: SourceRecord | None = None

    @property
    def known(self) -> bool:
        """True when a value is present (any status except UNKNOWN)."""
        return self.value is not None


class TractRecord(BaseModel):
    """Where a tract is. data_label says whether it is a real tract (PUBLIC) or generated (SYNTHETIC)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    geoid: str
    lat: float
    lon: float
    land_area_sq_miles: float | None
    data_label: Literal["PUBLIC", "SYNTHETIC"]
    district: str | None = None


@dataclass(frozen=True)
class PoiSearch:
    """POIs found near a site. status UNKNOWN means the layer is missing, which is different from 'none found'."""

    pois: list[Poi]
    status: FactorStatus
    reason: str
    source: SourceRecord | None = None


@dataclass(frozen=True)
class RoadResult:
    """The nearest road segment with traffic counts, or UNKNOWN with the reason."""

    segment: RoadSegment | None
    distance_miles: float | None
    status: FactorStatus
    reason: str
    source: SourceRecord | None = None


@dataclass(frozen=True)
class ListingSearch:
    """Short-term rental comparables near a site, or UNKNOWN with the reason."""

    listings: list[StrListing]
    status: FactorStatus
    reason: str
    source: SourceRecord | None = None


ShareBasis = Literal["tracts", "whole metro"]


def share_basis_for(scope: str, real_metro: bool) -> ShareBasis:
    """What a factor's coverage share is measured over, from its scope (see FactorCoverage.share_basis).

    Tract, road and POI layers are measured over tracts: a layer that can only be looked up live for one site has
    no per-tract values, so its tract share is 0 (it is flagged live_per_site instead). real_metro is kept so callers
    state which kind of metro they describe.
    """
    if scope in ("tract", "road_layer", "poi_layer"):
        return "tracts"
    return "whole metro"


class FactorCoverage(BaseModel):
    """Whether one factor has data in this metro, from where, how fresh, and for what share of tracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str
    scope: str
    weight: float = Field(ge=0, description="The profile's weight for this factor (0 for demand-only inputs)")
    status: FactorStatus
    share_of_tracts: float = Field(ge=0, le=1, description="Share covered (0 if none); share_basis says of what")
    share_basis: ShareBasis = Field(
        default="tracts",
        description="What the share is measured over: tracts, or the whole metro (a metro-level value that exists "
        "or not)",
    )
    live_per_site: bool = Field(
        default=False,
        description="True when the layer has no per-tract values but can be looked up live for one site (a real "
        "metro's road or POI layer). Such a layer is UNKNOWN for ranking tracts and counts 0 in the coverage share",
    )
    definition_share: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Share of the factor's definition the data can measure: for a place factor, the share of its "
        "tag filters that the places layer maps (Overture has no category for some OpenStreetMap tags). The "
        "coverage share counts share_of_tracts x definition_share",
    )
    source: str | None
    freshness: str | None
    reason: str


def required_factors(profile: Profile) -> dict[str, float]:
    """{factor: weight} for the profile's weighted factors plus the data factors its demand formula reads."""
    factors = {name: item.weight for name, item in profile.design.location_weights.items()}
    for name in DEMAND_FORMULAS[profile.demand.formula].data_factors:
        factors.setdefault(name, 0.0)
    return factors


def coverage_share(rows: list[FactorCoverage]) -> float:
    """Weighted share of the profile's factors backed by data (DATA or SYNTHETIC); ASSUMPTION and UNKNOWN count 0.

    A factor backed by data counts share_of_tracts x definition_share (a place factor whose tag filters are only
    partly mapped counts that part). Demand-only inputs (weight 0) count with the average weight, so a missing
    demand input still lowers the score.
    """
    if not rows:
        return 0.0
    positive = [row.weight for row in rows if row.weight > 0]
    fallback = sum(positive) / len(positive) if positive else 1.0
    weights = [row.weight if row.weight > 0 else fallback for row in rows]
    covered = [
        row.share_of_tracts * row.definition_share if row.status in ("DATA", "SYNTHETIC") else 0.0 for row in rows
    ]
    return float(sum(w * c for w, c in zip(weights, covered, strict=True)) / sum(weights))


class DataFabric(ABC):
    """The interface. See the module docstring for the method list and the rules."""

    metro: MetroConfig

    @property
    def data_label(self) -> Literal["PUBLIC", "SYNTHETIC"]:
        """PUBLIC for a real metro, SYNTHETIC for the generated one."""
        return self.metro.data_label

    @property
    def point_layers_local(self) -> bool:
        """True when sites_near and nearest_road answer without the network (so SiteIQ can measure them at every
        tract centroid of the metro for its reference distributions). Implementations override it."""
        return False

    @abstractmethod
    def mode(self, profiles: dict[str, Profile] | None = None) -> tuple[MetroMode, str]:
        """(full | limited | synthetic, reason)."""

    @abstractmethod
    def tract_ids(self) -> list[str]:
        """Every tract id of the metro (empty when no tract layer exists)."""

    @abstractmethod
    def tract(self, geoid: str) -> TractRecord:
        """A tract's position; KeyError for an unknown id."""

    @abstractmethod
    def tract_features(self, geoid: str) -> dict[str, FactorValue]:
        """Tract-level factors (TRACT_FACTORS) for one tract, each with status, reason and provenance."""

    @abstractmethod
    def tract_table(self) -> dict[str, dict[str, float | None]]:
        """{geoid: {tract factor: value or None}} for every tract (for percentile ranks within the metro)."""

    @abstractmethod
    def sites_near(self, lat: float, lon: float, radius_miles: float, tags: list[str]) -> PoiSearch:
        """POIs matching any OSM tag filter within radius_miles, nearest first."""

    @abstractmethod
    def nearest_road(self, lat: float, lon: float) -> RoadResult:
        """The nearest road segment with AADT within ROAD_SEARCH_MILES."""

    @abstractmethod
    def str_comparables(self, lat: float, lon: float, radius_miles: float) -> ListingSearch:
        """Short-term rental comparables within radius_miles."""

    @abstractmethod
    def metro_inputs(self, profile: Profile) -> dict[str, FactorValue]:
        """Metro-level inputs for the simulator: commercial_rent, local_wages and prime_rate."""

    @abstractmethod
    def coverage(self, profile: Profile) -> list[FactorCoverage]:
        """One FactorCoverage per required factor of the profile."""

    def tract_centroids(self) -> tuple[list[str], np.ndarray, np.ndarray]:
        """(ids, latitudes, longitudes) of every tract, for vectorized distance searches."""
        ids = self.tract_ids()
        records = [self.tract(geoid) for geoid in ids]
        return ids, np.array([item.lat for item in records]), np.array([item.lon for item in records])

    def tracts_within(self, lat: float, lon: float, radius_miles: float) -> list[tuple[str, float]]:
        """[(geoid, distance in miles)] of tracts whose centroid is within radius_miles, nearest first."""
        ids, lats, lons = self.tract_centroids()
        if not ids:
            return []
        distances = haversine_miles_array(lat, lon, lats, lons)
        order = np.argsort(distances)
        return [(ids[i], float(distances[i])) for i in order if distances[i] <= radius_miles]

    def tract_at(self, lat: float, lon: float, max_miles: float = 3.0) -> str | None:
        """The tract whose centroid is nearest the site (None when none is within max_miles).

        Nearest centroid is an approximation of "the tract containing the point"; for a real address the Census
        Geocoder gives the exact tract (geocode.tract_for_point).
        """
        nearest = self.tracts_within(lat, lon, max_miles)
        return nearest[0][0] if nearest else None
