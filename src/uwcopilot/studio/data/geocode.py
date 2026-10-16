"""Census Geocoder: an address (or a map pin) to latitude/longitude and the census tract GEOID. No key needed.

Two requests, both documented at https://geocoding.geo.census.gov/geocoder/ (terms to verify there):
- geographies/onelineaddress: a one-line address -> the matched address, its coordinates and its tract;
- geographies/coordinates: a latitude/longitude (the founder's pin) -> its tract.

Benchmark and vintage are the geocoder's "current" ones; the response echoes the names it used and the connector
records them as the vintage. An address with no match raises NotAvailable: the site is never placed by guessing.
"""

from dataclasses import dataclass
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.http import PoliteClient, json_object_check
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

BASE_URL = "https://geocoding.geo.census.gov/geocoder/geographies"
BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"
HOST = "geocoding.geo.census.gov"
ACCEPT = json_object_check(HOST, "result")  # an error page is never cached
TRACT_LAYER = "Census Tracts"
SOURCE = SourceInfo(
    source_id="census_geocoder",
    publisher="U.S. Census Bureau",
    dataset="Census Geocoder (geographies)",
    license_note="Terms to verify at https://geocoding.geo.census.gov/geocoder/ (not yet recorded)",
)


@dataclass(frozen=True)
class GeocodeMatch:
    """Where an address or a pin is, and its census tract."""

    lat: float
    lon: float
    tract_geoid: str
    state_fips: str
    county_fips: str
    matched_address: str | None
    source: SourceRecord


def address_request(address: str) -> tuple[str, dict[str, str]]:
    """(url, params) for a one-line address lookup with the tract layer."""
    params = {
        "address": address,
        "benchmark": BENCHMARK,
        "vintage": VINTAGE,
        "layers": TRACT_LAYER,
        "format": "json",
    }
    return f"{BASE_URL}/onelineaddress", params


def coordinates_request(lat: float, lon: float) -> tuple[str, dict[str, str]]:
    """(url, params) for a coordinates lookup (x is longitude, y is latitude)."""
    params = {
        "x": f"{lon:.6f}",
        "y": f"{lat:.6f}",
        "benchmark": BENCHMARK,
        "vintage": VINTAGE,
        "layers": TRACT_LAYER,
        "format": "json",
    }
    return f"{BASE_URL}/coordinates", params


def tract_from_geographies(geographies: dict[str, Any]) -> dict[str, Any]:
    """The single census tract entry in a 'geographies' block; raises NotAvailable when there is none."""
    tracts = geographies.get(TRACT_LAYER) or []
    if not tracts:
        raise NotAvailable("the Census Geocoder returned no census tract for this location")
    return tracts[0]


def vintage_of(result: dict[str, Any]) -> str:
    """'benchmark / vintage' as echoed by the geocoder."""
    echoed = result.get("input", {})
    benchmark = echoed.get("benchmark", {}).get("benchmarkName", BENCHMARK)
    vintage = echoed.get("vintage", {}).get("vintageName", VINTAGE)
    return f"{benchmark} / {vintage}"


def parse_address_response(data: dict[str, Any]) -> tuple[float, float, dict[str, Any], str, str]:
    """(lat, lon, tract entry, matched address, vintage) of the first address match."""
    result = data.get("result")
    if not isinstance(result, dict):
        raise UpstreamError("geocoding.geo.census.gov", "response has no 'result' object")
    matches = result.get("addressMatches") or []
    if not matches:
        raise NotAvailable("the Census Geocoder found no match for this address; place the site on the map instead")
    first = matches[0]
    tract = tract_from_geographies(first.get("geographies", {}))
    coordinates = first["coordinates"]
    return float(coordinates["y"]), float(coordinates["x"]), tract, first.get("matchedAddress", ""), vintage_of(result)


def parse_coordinates_response(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """(tract entry, vintage) for a coordinates lookup."""
    result = data.get("result")
    if not isinstance(result, dict):
        raise UpstreamError("geocoding.geo.census.gov", "response has no 'result' object")
    return tract_from_geographies(result.get("geographies", {})), vintage_of(result)


def geocode_address(client: PoliteClient, address: str) -> GeocodeMatch:
    """Look up a one-line address. Raises NotAvailable when the geocoder has no match."""
    url, params = address_request(address)
    fetched = client.get(url, params, accept=ACCEPT)
    lat, lon, tract, matched, vintage = parse_address_response(fetched.json())
    return GeocodeMatch(
        lat=lat,
        lon=lon,
        tract_geoid=str(tract["GEOID"]),
        state_fips=str(tract["STATE"]),
        county_fips=str(tract["COUNTY"]),
        matched_address=matched,
        source=SOURCE.record(fetched, vintage=vintage, geography=str(tract["GEOID"])),
    )


def tract_for_point(client: PoliteClient, lat: float, lon: float) -> GeocodeMatch:
    """The census tract containing a latitude/longitude pin."""
    url, params = coordinates_request(lat, lon)
    fetched = client.get(url, params, accept=ACCEPT)
    tract, vintage = parse_coordinates_response(fetched.json())
    return GeocodeMatch(
        lat=lat,
        lon=lon,
        tract_geoid=str(tract["GEOID"]),
        state_fips=str(tract["STATE"]),
        county_fips=str(tract["COUNTY"]),
        matched_address=None,
        source=SOURCE.record(fetched, vintage=vintage, geography=str(tract["GEOID"])),
    )
