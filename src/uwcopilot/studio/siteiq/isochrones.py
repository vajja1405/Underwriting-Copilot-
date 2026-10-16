"""OpenRouteService drive-time areas (isochrones), used only when ORS_API_KEY is set.

Request (field names checked against the openrouteservice source on 2026-10-12: IsochronesRequest.java and
GeoJSONIsochroneProperties.java in github.com/GIScience/openrouteservice, main branch):

    POST https://api.openrouteservice.org/v2/isochrones/driving-car
    Authorization: <ORS_API_KEY>
    {"locations": [[lon, lat]], "range": [300, 600, 900], "range_type": "time"}

The answer is a GeoJSON FeatureCollection with one Polygon per range; each feature's properties carry "value" (the
range in seconds) and "group_index". A request without the key answered HTTP 401 "Authorization field missing" on
2026-10-12. With the key, one answer was recorded on 2026-10-13 by tests/studio/fixtures/ors/record_isochrones.py
(fixture isochrones_houston_recorded.json); its shape matched this parser, and its "metadata" carries an
"attribution" string and the routing engine's data dates ("engine": osm_date, graph_date). Both are copied into the
SourceRecord of every answer (license_note and vintage). The terms of use themselves are still to verify on
openrouteservice.org.

The key travels only in the Authorization header. PoliteClient redacts it from logs and errors, and it is not part
of the cache key, the stored request or the SourceRecord.
"""

import json
from typing import Any

from uwcopilot.studio.data.cache import request_key
from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

HOST = "api.openrouteservice.org"
ISOCHRONES_URL = f"https://{HOST}/v2/isochrones/driving-car"
SECONDS_PER_MINUTE = 60
SOURCE = SourceInfo(
    source_id="openrouteservice_isochrones",
    publisher="openrouteservice (api.openrouteservice.org)",
    dataset="openrouteservice v2 isochrones, driving-car profile",
    license_note="Terms of use to verify on openrouteservice.org; the answer gave no attribution string",
)


def isochrone_payload(lat: float, lon: float, minutes: tuple[int, ...]) -> dict[str, Any]:
    """The JSON body asking for drive-time areas of the given minutes around one point."""
    if not minutes or any(value <= 0 for value in minutes):
        raise ValueError("drive-time minutes must be positive")
    return {
        "locations": [[round(lon, 6), round(lat, 6)]],
        "range": [value * SECONDS_PER_MINUTE for value in sorted(minutes)],
        "range_type": "time",
    }


def parse_isochrones(data: dict[str, Any]) -> dict[int, list[tuple[float, float]]]:
    """{minutes: outer ring [(lat, lon), ...]} from the GeoJSON answer. Only Polygon features are read."""
    if "features" not in data:
        raise UpstreamError(HOST, str(data.get("error", "answer has no 'features' list")))
    areas: dict[int, list[tuple[float, float]]] = {}
    for feature in data["features"]:
        geometry = feature.get("geometry") or {}
        seconds = (feature.get("properties") or {}).get("value")
        if geometry.get("type") != "Polygon" or seconds is None:
            continue
        outer = geometry.get("coordinates", [[]])[0]
        areas[round(float(seconds) / SECONDS_PER_MINUTE)] = [(float(point[1]), float(point[0])) for point in outer]
    if not areas:
        raise UpstreamError(HOST, "no Polygon feature with a 'value' in the answer")
    return areas


def answer_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """The answer's "metadata" object ({} when it is missing or not an object)."""
    metadata = data.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def answer_attribution(data: dict[str, Any]) -> str | None:
    """The attribution string the answer asks users to show (metadata.attribution), or None."""
    attribution = answer_metadata(data).get("attribution")
    return attribution.strip() if isinstance(attribution, str) and attribution.strip() else None


def answer_vintage(data: dict[str, Any]) -> str:
    """The data dates the answer reports (metadata.engine osm_date and graph_date), for the SourceRecord vintage."""
    found = answer_metadata(data).get("engine")
    engine: dict[str, Any] = found if isinstance(found, dict) else {}
    parts = []
    if isinstance(engine.get("osm_date"), str):
        parts.append(f"OpenStreetMap data of {engine['osm_date'][:10]}")
    if isinstance(engine.get("graph_date"), str):
        parts.append(f"routing graph of {engine['graph_date'][:10]}")
    return "; ".join(parts) if parts else "live routing at the request date (the answer gave no data dates)"


def fetch_isochrones(
    client: PoliteClient, api_key: str, lat: float, lon: float, minutes: tuple[int, ...]
) -> tuple[dict[int, list[tuple[float, float]]], SourceRecord]:
    """Drive-time polygons around the site, with provenance (the key is never stored)."""
    payload = isochrone_payload(lat, lon, minutes)
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    key = request_key("POST", ISOCHRONES_URL, {}, body)
    fetched = client.fetch(
        "POST",
        ISOCHRONES_URL,
        {},
        {},
        body,
        payload,
        {"Authorization": api_key},
        [api_key],
        key,
        True,
    )
    data = fetched.json()
    record = SOURCE.record(fetched, vintage=answer_vintage(data), geography=f"{lat:.5f},{lon:.5f}")
    attribution = answer_attribution(data)
    if attribution is not None:
        note = f"Attribution required by the answer: '{attribution}'. Terms of use to verify on openrouteservice.org"
        record = record.model_copy(update={"license_note": note})
    return parse_isochrones(data), record
