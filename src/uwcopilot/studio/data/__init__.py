"""Studio data: public-data connectors, the response cache, provenance records, the SYNTHETIC city and the fabric.

Start with fabric.get_fabric(metro_id): it is the one interface SiteIQ (S2) and the simulator (S3) read from.

Modules:
- fabric.py            the DataFabric interface (SyntheticFabric, SnapshotFabric) and FactorValue / coverage types;
- synthetic_city.py    the seeded SYNTHETIC city with hidden true demand (for validation only);
- snapshot.py          `make studio-data METRO=<id>`: downloads a real metro's snapshot;
- http.py, cache.py    the polite, caching HTTP client every connector uses;
- provenance.py        SourceRecord: where each value came from;
- keys.py, errors.py   API keys (never logged) and the errors connectors raise;
- geo.py               distances in miles;
- connectors: geocode, census_acs, census_geography, business_patterns, wages, traffic_hpms, lodes, overture_places,
  osm (optional), rates, hud_fmr (national layer); traffic_state, city_open_data, str_comps (metro enhancements);
- snapshot layers: snapshot_layers.py builds them, places_layer.py and road_layer.py read them, snapshot_files.py
  checks the stored files, snapshot_coverage.py counts their coverage; overture_config.py and duckdb_http.py serve the
  Overture extract.
"""

from uwcopilot.studio.data.errors import (
    HostUnreachable,
    MissingApiKey,
    NotAvailable,
    StudioDataError,
    UpstreamError,
)
from uwcopilot.studio.data.fabric import (
    DataFabric,
    FactorCoverage,
    FactorValue,
    ListingSearch,
    PoiSearch,
    RoadResult,
    SnapshotFabric,
    SyntheticFabric,
    TractRecord,
    coverage_share,
    get_fabric,
)
from uwcopilot.studio.data.provenance import SourceRecord

__all__ = [
    "DataFabric",
    "FactorCoverage",
    "FactorValue",
    "HostUnreachable",
    "ListingSearch",
    "MissingApiKey",
    "NotAvailable",
    "PoiSearch",
    "RoadResult",
    "SnapshotFabric",
    "SourceRecord",
    "StudioDataError",
    "SyntheticFabric",
    "TractRecord",
    "UpstreamError",
    "coverage_share",
    "get_fabric",
]
