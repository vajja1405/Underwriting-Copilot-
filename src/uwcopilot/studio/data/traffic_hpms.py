"""FHWA HPMS traffic: annual average daily traffic (AADT) on the road segments near a site. No key needed.

FHWA publishes the Highway Performance Monitoring System (HPMS) public release as one ArcGIS FeatureServer layer per
state and year on geo.dot.gov (for example Hosted/HPMS_FULL_TX_2024; the FHWA HPMS shapefile page links to these
services). Studio asks the layer for segments with AADT > 0 within a small distance of the site, as GeoJSON, and takes
the nearest one. Terms to verify at https://www.fhwa.dot.gov/policyinformation/hpms.cfm.

A state DOT dataset can improve on HPMS where one is documented (traffic_state.py). When no segment is near the site,
traffic is UNKNOWN for that site: drive-by demand is then not estimated from traffic.
"""

from dataclasses import dataclass
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.geo import point_to_polyline_miles
from uwcopilot.studio.data.http import FetchResult, PoliteClient, expect_json
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

HOST = "geo.dot.gov"
DEFAULT_YEAR = 2024
METERS_PER_MILE = 1609.344
OUT_FIELDS = "route_id,routename,aadt,f_system,data_year,begin_point,end_point"
# County downloads (road_layer.py): the fields kept, the page size (the layer's maxRecordCount, read from the layer's
# metadata on 2026-10-13) and a shape simplification of 0.0001 degrees (about 11 m), a design choice that keeps the
# download small; it moves a shape by less than the 0.5-mile search distance cares about.
COUNTY_FIELDS = "route_id,routename,aadt,f_system,data_year"
PAGE_SIZE = 2000
SIMPLIFY_DEGREES = "0.0001"
MAX_PAGES_PER_COUNTY = 1000
SOURCE = SourceInfo(
    source_id="fhwa_hpms",
    publisher="U.S. Federal Highway Administration",
    dataset="Highway Performance Monitoring System (HPMS) public release, state FeatureServer layer",
    license_note="Terms to verify at https://www.fhwa.dot.gov/policyinformation/hpms.cfm (not yet recorded)",
)


@dataclass(frozen=True)
class RoadSegment:
    """One HPMS road segment with its AADT and shape [(lat, lon), ...]."""

    route_id: str
    route_name: str | None
    aadt: int
    functional_class: int | None
    data_year: int | None
    line: list[tuple[float, float]]


@dataclass(frozen=True)
class NearestRoad:
    """The nearest segment to a site and how far it is."""

    segment: RoadSegment
    distance_miles: float
    source: SourceRecord


def layer_url(state: str, year: int = DEFAULT_YEAR) -> str:
    """The FeatureServer query URL for one state's HPMS layer."""
    if len(state) != 2 or not state.isalpha():
        raise ValueError(f"state must be a two-letter postal code (got {state!r})")
    return f"https://{HOST}/server/rest/services/Hosted/HPMS_FULL_{state.upper()}_{year}/FeatureServer/0/query"


def segments_near_request(lat: float, lon: float, radius_miles: float, max_segments: int = 50) -> dict[str, str]:
    """Query parameters for segments with AADT > 0 within radius_miles of a point, as GeoJSON."""
    return {
        "geometry": f"{lon:.6f},{lat:.6f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": f"{radius_miles * METERS_PER_MILE:.0f}",
        "units": "esriSRUnit_Meter",
        "where": "aadt > 0",
        "outFields": OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "geometryPrecision": "6",
        "resultRecordCount": str(max_segments),
        "f": "geojson",
    }


def county_count_request(county_fips: str) -> dict[str, str]:
    """Query parameters counting segments with AADT > 0 in one county (county_id is the 3-digit county code)."""
    if len(county_fips) != 3 or not county_fips.isdigit():
        raise ValueError(f"county FIPS codes have 3 digits (got {county_fips!r})")
    return {"where": f"county_id = {int(county_fips)} AND aadt > 0", "returnCountOnly": "true", "f": "json"}


def line_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """[(lat, lon), ...] of a GeoJSON LineString or MultiLineString (parts are joined)."""
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if kind == "LineString":
        parts = [coordinates]
    elif kind == "MultiLineString":
        parts = coordinates
    else:
        return []
    return [(float(point[1]), float(point[0])) for part in parts for point in part]


def parse_segments(data: dict[str, Any]) -> list[RoadSegment]:
    """RoadSegments from the FeatureServer's GeoJSON answer (features without AADT or shape are skipped)."""
    if "error" in data:
        raise UpstreamError(HOST, str(data["error"]))
    segments: list[RoadSegment] = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        line = line_points(feature.get("geometry") or {})
        if not line or props.get("aadt") in (None, 0):
            continue
        segments.append(
            RoadSegment(
                route_id=str(props.get("route_id")),
                route_name=props.get("routename"),
                aadt=int(props["aadt"]),
                functional_class=props.get("f_system"),
                data_year=props.get("data_year"),
                line=line,
            )
        )
    return segments


def nearest_segment(segments: list[RoadSegment], lat: float, lon: float) -> tuple[RoadSegment, float]:
    """(nearest segment, distance in miles). Raises NotAvailable for an empty list."""
    if not segments:
        raise NotAvailable("no HPMS road segment with traffic counts near this site")
    distances = [point_to_polyline_miles(lat, lon, segment.line) for segment in segments]
    best = min(range(len(segments)), key=lambda index: distances[index])
    return segments[best], distances[best]


def check_arcgis_answer(fetched: FetchResult) -> None:
    """Accept a FeatureServer answer only when it is a JSON object without an 'error' (an http.Accept check).

    ArcGIS reports query errors with HTTP 200 and {"error": ...}, so this runs before the answer is cached.
    """
    data = expect_json(fetched, HOST)
    if not isinstance(data, dict) or "error" in data:
        detail = data.get("error") if isinstance(data, dict) else "not a JSON object"
        raise UpstreamError(HOST, str(detail))


def nearest_road(
    client: PoliteClient, state: str, lat: float, lon: float, radius_miles: float = 0.25, year: int = DEFAULT_YEAR
) -> NearestRoad:
    """The nearest HPMS segment with AADT within radius_miles of the site."""
    params = segments_near_request(lat, lon, radius_miles)
    fetched = client.get(layer_url(state, year), params, accept=check_arcgis_answer)
    segment, distance = nearest_segment(parse_segments(fetched.json()), lat, lon)
    record = SOURCE.record(fetched, vintage=f"HPMS {year}", geography=f"{lat:.5f},{lon:.5f}")
    return NearestRoad(segment=segment, distance_miles=distance, source=record)


def county_segments_request(county_fips: str, offset: int, page_size: int = PAGE_SIZE) -> dict[str, str]:
    """Query parameters for one page of a county's segments with AADT > 0, as GeoJSON, in a stable order."""
    params = county_count_request(county_fips)
    params.pop("returnCountOnly")
    params.update(
        {
            "outFields": COUNTY_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "geometryPrecision": "5",
            "maxAllowableOffset": SIMPLIFY_DEGREES,
            "orderByFields": "objectid",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
            "f": "geojson",
        }
    )
    return params


def more_pages(data: dict[str, Any]) -> bool:
    """True when the server says the page was cut at the record limit (exceededTransferLimit)."""
    flag = data.get("exceededTransferLimit")
    if flag is None:
        flag = (data.get("properties") or {}).get("exceededTransferLimit")
    return bool(flag)


@dataclass(frozen=True)
class CountySegments:
    """Every segment with AADT of one county, the provenance of each page, and the segments skipped (no shape)."""

    state: str
    county_fips: str
    segments: list[RoadSegment]
    page_of_segment: list[int]
    records: list[SourceRecord]
    features_read: int


def fetch_county_segments(
    client: PoliteClient, state: str, county_fips: str, year: int = DEFAULT_YEAR, page_size: int = PAGE_SIZE
) -> CountySegments:
    """Download a county's segments page by page (resultOffset) until the server stops flagging more pages."""
    segments: list[RoadSegment] = []
    page_of_segment: list[int] = []
    records: list[SourceRecord] = []
    features_read = 0
    for page in range(MAX_PAGES_PER_COUNTY):
        params = county_segments_request(county_fips, page * page_size, page_size)
        fetched = client.get(layer_url(state, year), params, accept=check_arcgis_answer)
        data = fetched.json()
        found = parse_segments(data)
        features_read += len(data.get("features", []))
        segments += found
        page_of_segment += [page] * len(found)
        records.append(
            SOURCE.record(fetched, vintage=f"HPMS {year}", geography=f"state {state} county {county_fips} page {page}")
        )
        if not more_pages(data) or not data.get("features"):
            return CountySegments(state, county_fips, segments, page_of_segment, records, features_read)
    raise UpstreamError(HOST, f"more than {MAX_PAGES_PER_COUNTY} pages for county {county_fips}; refused")


def county_segment_count(
    client: PoliteClient, state: str, county_fips: str, year: int = DEFAULT_YEAR
) -> tuple[int, SourceRecord]:
    """How many segments with AADT > 0 the HPMS layer has in one county (used by the coverage report)."""
    fetched = client.get(layer_url(state, year), county_count_request(county_fips), accept=check_arcgis_answer)
    data = fetched.json()
    if "count" not in data:
        raise UpstreamError(HOST, str(data.get("error", "no count in the answer")))
    record = SOURCE.record(fetched, vintage=f"HPMS {year}", geography=f"state {state} county {county_fips}")
    return int(data["count"]), record
