"""The data fabric: ONE interface that SiteIQ (S2) and the feasibility simulator (S3) read metro data through.

    from uwcopilot.studio.data.fabric import get_fabric
    fabric = get_fabric("synthetic_metro")          # or a real metro id
    fabric.mode()                                    # ("synthetic" | "full" | "limited", plain-language reason)
    fabric.tract_ids()                               # every tract in the metro
    fabric.tract("SYN000042")                        # TractRecord: centroid, land area, district
    fabric.tract_features("SYN000042")               # {factor: FactorValue} for tract-level factors
    fabric.tract_table()                             # {geoid: {factor: value or None}} for metro-wide percentiles
    fabric.tract_at(lat, lon)                        # tract id of a site (or None outside the metro)
    fabric.tracts_within(lat, lon, radius_miles)     # [(geoid, distance)] for trade-area sums
    fabric.sites_near(lat, lon, radius_miles, tags)  # PoiSearch: POIs matching OSM tag filters, nearest first
    fabric.nearest_road(lat, lon)                    # RoadResult: nearest segment with AADT
    fabric.str_comparables(lat, lon, radius_miles)   # ListingSearch: short-term rental comparables
    fabric.metro_inputs(profile)                     # {commercial_rent, local_wages, prime_rate: FactorValue}
    fabric.coverage(profile)                         # [FactorCoverage]: per factor, data or not, share of tracts

The rules every implementation follows (Studio addendum rules D, E and G):
- a value is never filled silently: no data -> status UNKNOWN with a reason; an author's assumption -> ASSUMPTION;
- every value carries a status (DATA, SYNTHETIC, ASSUMPTION or UNKNOWN), a plain-language reason and, when it came
  from a dataset, a SourceRecord (provenance.py);
- the hidden true demand of the SYNTHETIC city is never reachable through the fabric (validation code reads it from
  synthetic_city directly).

Where the code lives: the interface and value types in fabric_base.py, and two implementations:
- SyntheticFabric (fabric_synthetic.py): the generated SYNTHETIC city (synthetic_city.py). Mode "synthetic". Used by
  the app, the tests and the S2/S3 validations until real snapshots exist.
- SnapshotFabric (fabric_snapshot.py): a real metro read from its newest snapshot under
  data/studio/snapshots/<metro id>/ (written by `make studio-data METRO=<id>`, see snapshot.py). Without a snapshot
  every factor is UNKNOWN and the metro runs in limited mode. Places (Overture Maps) and roads (HPMS) are answered from
  the layers the snapshot stores; only when a snapshot lacks them, and a PoliteClient is passed in (`live=`), are they
  looked up live per site (OpenStreetMap Overpass, HPMS FeatureServer). City license data (city_open_data.py) is a
  per-site enhancement that S2 can call directly; the fabric does not wrap it yet.
"""

from functools import lru_cache

from uwcopilot.studio.data.fabric_base import (
    ROAD_SEARCH_MILES,
    TRACT_FACTORS,
    DataFabric,
    FactorCoverage,
    FactorStatus,
    FactorValue,
    ListingSearch,
    PoiSearch,
    RoadResult,
    TractRecord,
    coverage_share,
    required_factors,
)
from uwcopilot.studio.data.fabric_snapshot import Snapshot, SnapshotFabric, latest_snapshot
from uwcopilot.studio.data.fabric_synthetic import SyntheticFabric, synthetic_record
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.synthetic_city import SyntheticCity, generate_city
from uwcopilot.studio.metros.loader import load_metro
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.loader import load_all_profiles

__all__ = [
    "DataFabric",
    "FactorCoverage",
    "FactorStatus",
    "FactorValue",
    "ListingSearch",
    "PoiSearch",
    "ROAD_SEARCH_MILES",
    "RoadResult",
    "Snapshot",
    "SnapshotFabric",
    "SyntheticFabric",
    "TRACT_FACTORS",
    "TractRecord",
    "cached_city",
    "coverage_share",
    "get_fabric",
    "latest_snapshot",
    "required_factors",
    "synthetic_city_for",
    "synthetic_record",
]


@lru_cache(maxsize=8)
def cached_city(metro_id: str, seed: int, n_tracts: int, lat: float, lon: float, extent: float) -> SyntheticCity:
    """Generate (once per process) the SYNTHETIC city for these settings."""
    return generate_city(metro_id, seed, n_tracts, lat, lon, extent, load_all_profiles())


def synthetic_city_for(metro: MetroConfig, seed: int | None = None) -> SyntheticCity:
    """The SYNTHETIC city of a SYNTHETIC metro config (optionally with another seed, for validation runs)."""
    settings = metro.synthetic
    if settings is None:
        raise ValueError(f"{metro.id} is not a SYNTHETIC metro")
    chosen = settings.seed if seed is None else seed
    return cached_city(
        metro.id, chosen, settings.n_tracts, settings.origin_lat, settings.origin_lon, settings.extent_miles
    )


def get_fabric(metro_id: str, live: PoliteClient | None = None, seed: int | None = None) -> DataFabric:
    """The fabric for a metro id: SyntheticFabric for the SYNTHETIC metro, SnapshotFabric for a real one."""
    metro = load_metro(metro_id)
    if metro.data_label == "SYNTHETIC":
        return SyntheticFabric(metro=metro, city=synthetic_city_for(metro, seed))
    return SnapshotFabric(metro=metro, snapshot=latest_snapshot(metro_id, pinned=metro.snapshot_date), live=live)
