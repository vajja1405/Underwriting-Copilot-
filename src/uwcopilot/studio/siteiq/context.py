"""MetroContext: one metro's data fabric plus the things SiteIQ computes once per metro and reuses.

OWN_TRACT_MAX_MILES (metros/rings.py) is how far a site may be from its nearest tract centroid.

Scoring a site ranks its values against every tract location in the metro (reference.py), so the tract table, the
road lookups and the per-profile reference distributions are computed once and kept here. Build one context per
metro and pass it to score_site / suggest_areas; metro_context() keeps one per (metro id, seed) for offline use.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from uwcopilot.studio.data.fabric import TRACT_FACTORS, DataFabric, RoadResult, get_fabric
from uwcopilot.studio.data.geo import haversine_miles_array
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.metros.loader import load_metro
from uwcopilot.studio.metros.models import MetroConfig, MetroMode
from uwcopilot.studio.metros.rings import OWN_TRACT_MAX_MILES


class SiteOutsideMetro(ValueError):
    """The site is not near any tract of the metro, so it cannot be scored there."""


@dataclass(frozen=True)
class TractLayer:
    """Every tract's centroid and tract-level values as arrays (NaN where the value is UNKNOWN)."""

    ids: list[str]
    lats: np.ndarray
    lons: np.ndarray
    values: dict[str, np.ndarray]

    def __len__(self) -> int:
        return len(self.ids)


def build_tract_layer(fabric: DataFabric) -> TractLayer:
    """Read the fabric's tract table once into arrays (None becomes NaN, never a number)."""
    ids, lats, lons = fabric.tract_centroids()
    table = fabric.tract_table()
    values: dict[str, np.ndarray] = {}
    for factor in TRACT_FACTORS:
        column = [table[geoid].get(factor) for geoid in ids]
        values[factor] = np.array([np.nan if value is None else float(value) for value in column], dtype=float)
    return TractLayer(ids=ids, lats=np.asarray(lats, dtype=float), lons=np.asarray(lons, dtype=float), values=values)


@dataclass
class MetroContext:
    """A metro's fabric and SiteIQ's per-metro caches (tract arrays, road lookups, reference distributions)."""

    metro: MetroConfig
    fabric: DataFabric
    live: PoliteClient | None = None
    tracts: TractLayer = field(init=False)
    roads: dict[tuple[float, float], RoadResult] = field(default_factory=dict)
    references: dict[Any, Any] = field(default_factory=dict)
    cached_mode: tuple[MetroMode, str] | None = None

    def __post_init__(self) -> None:
        self.tracts = build_tract_layer(self.fabric)

    def mode(self) -> tuple[MetroMode, str]:
        """The metro's mode (full, limited or synthetic) and reason, computed once."""
        if self.cached_mode is None:
            self.cached_mode = self.fabric.mode()
        return self.cached_mode

    @property
    def point_layers_local(self) -> bool:
        """True when POI and road lookups are answered without the network: the SYNTHETIC city, a snapshot that
        stores its places and road layers, or a real metro with no live client (lookups are then UNKNOWN)."""
        return self.fabric.point_layers_local

    def own_tract(self, lat: float, lon: float) -> int | None:
        """Index of the tract whose centroid is nearest the site, or None when none is within OWN_TRACT_MAX_MILES."""
        if len(self.tracts) == 0:
            return None
        distances = haversine_miles_array(lat, lon, self.tracts.lats, self.tracts.lons)
        nearest = int(np.argmin(distances))
        return nearest if distances[nearest] <= OWN_TRACT_MAX_MILES else None

    def nearest_road(self, lat: float, lon: float) -> RoadResult:
        """The fabric's nearest road, remembered per position (it does not depend on the business type)."""
        position = (round(lat, 6), round(lon, 6))
        if position not in self.roads:
            self.roads[position] = self.fabric.nearest_road(lat, lon)
        return self.roads[position]


def build_context(metro_id: str, live: PoliteClient | None = None, seed: int | None = None) -> MetroContext:
    """A new context for a metro (seed only matters for the SYNTHETIC metro)."""
    metro = load_metro(metro_id)
    return MetroContext(metro=metro, fabric=get_fabric(metro_id, live=live, seed=seed), live=live)


@lru_cache(maxsize=8)
def metro_context(metro_id: str, seed: int | None = None) -> MetroContext:
    """The offline context of a metro, built once per process (no live lookups)."""
    return build_context(metro_id, live=None, seed=seed)
