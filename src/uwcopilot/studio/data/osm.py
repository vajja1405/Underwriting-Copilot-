"""OpenStreetMap points of interest via the Overpass API: competitors and anchors by profile tags. No key needed.

The query asks for nodes, ways and relations ("nwr") carrying any of the profile's tag filters within a radius of
the site, and returns each element's centre and tags. Tag filters are validated with the same pattern the profile
model uses (key, key=* or key=value with a restricted character set), so a tag can never inject Overpass QL.

OpenStreetMap data is (c) OpenStreetMap contributors; the license and the attribution wording to show are to verify
at https://www.openstreetmap.org/copyright. overpass-api.de reset connections from this build environment on
2026-10-12 and 2026-10-13, so the tests use a hand-written fixture.

This connector is OPTIONAL. The default place source is the Overture Maps places extract stored in each snapshot
(overture_places.py, places_layer.py): it is reachable from here, one pinned release makes it reproducible, and a
metro-wide extract gives the per-tract counts the metro-wide ranking needs, which per-site Overpass queries cannot.
SnapshotFabric falls back to this connector only for a snapshot without a places layer, and only with a live client;
`make studio-data` checks Overpass only with --with-overpass.
"""

from dataclasses import dataclass, field
from typing import Any

from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.data.geo import haversine_miles
from uwcopilot.studio.data.http import PoliteClient, json_object_check
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord
from uwcopilot.studio.profiles.models import OSM_TAG

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
METERS_PER_MILE = 1609.344
QUERY_TIMEOUT_S = 60
SOURCE = SourceInfo(
    source_id="osm_overpass",
    publisher="OpenStreetMap contributors (via the Overpass API)",
    dataset="OpenStreetMap points of interest",
    license_note="(c) OpenStreetMap contributors; license and attribution wording to verify at "
    "https://www.openstreetmap.org/copyright (not yet recorded)",
)


@dataclass(frozen=True)
class Poi:
    """One point of interest: an id, a position, its tags and (when computed) its distance from a site."""

    poi_id: str
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)
    distance_miles: float | None = None

    @property
    def name(self) -> str | None:
        """The OSM name tag, when present."""
        return self.tags.get("name")


def tag_selector(tag: str) -> str:
    """Overpass selector for one tag filter: 'amenity=cafe' -> [amenity=cafe]; 'office' or 'office=*' -> [office]."""
    if not OSM_TAG.match(tag):
        raise ValueError(f"invalid OpenStreetMap tag filter {tag!r}")
    key, _, value = tag.partition("=")
    if value in ("", "*"):
        return f'["{key}"]'
    return f'["{key}"="{value}"]'


def build_query(lat: float, lon: float, radius_miles: float, tags: list[str]) -> str:
    """Overpass QL asking for every element with any of the tags within radius_miles of (lat, lon)."""
    if not tags:
        raise ValueError("at least one tag filter is needed")
    radius_m = round(radius_miles * METERS_PER_MILE)
    parts = "".join(f"nwr{tag_selector(tag)}(around:{radius_m},{lat:.6f},{lon:.6f});" for tag in tags)
    return f"[out:json][timeout:{QUERY_TIMEOUT_S}];({parts});out center tags;"


def tag_matches(tags: dict[str, str], tag_filter: str) -> bool:
    """True when an element's tags satisfy one filter ('key', 'key=*' or 'key=value')."""
    key, _, value = tag_filter.partition("=")
    if key not in tags:
        return False
    return value in ("", "*") or tags[key] == value


def parse_elements(data: dict[str, Any], lat: float, lon: float) -> list[Poi]:
    """POIs from an Overpass JSON answer, with distances from (lat, lon), nearest first."""
    if "elements" not in data:
        raise UpstreamError("overpass-api.de", "answer has no 'elements' list")
    pois: list[Poi] = []
    for element in data["elements"]:
        point = element if "lat" in element else element.get("center")
        if not point:
            continue
        poi_lat, poi_lon = float(point["lat"]), float(point["lon"])
        pois.append(
            Poi(
                poi_id=f"{element.get('type', 'node')}/{element.get('id')}",
                lat=poi_lat,
                lon=poi_lon,
                tags={str(k): str(v) for k, v in (element.get("tags") or {}).items()},
                distance_miles=haversine_miles(lat, lon, poi_lat, poi_lon),
            )
        )
    pois.sort(key=lambda poi: poi.distance_miles or 0.0)
    return pois


def pois_near(
    client: PoliteClient, lat: float, lon: float, radius_miles: float, tags: list[str]
) -> tuple[list[Poi], SourceRecord]:
    """Every POI with any of the tags within radius_miles of the site, nearest first, with provenance."""
    query = build_query(lat, lon, radius_miles, tags)
    fetched = client.get(OVERPASS_URL, {"data": query}, accept=json_object_check("overpass-api.de", "elements"))
    record = SOURCE.record(
        fetched, vintage="live OpenStreetMap data at the request date", geography=f"{lat:.5f},{lon:.5f}"
    )
    return parse_elements(fetched.json(), lat, lon), record
