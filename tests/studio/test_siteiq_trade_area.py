"""SiteIQ trade areas: scaled rings, ring and polygon membership, drive-time areas from OpenRouteService."""

import json

import httpx
import numpy as np
import pytest
from studio_helpers import FakeNetwork, fast_policies, load_fixture, make_client

from uwcopilot.studio.data import keys
from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.profiles import load_profile
from uwcopilot.studio.siteiq.enhancements import demand_area, license_context
from uwcopilot.studio.siteiq.isochrones import (
    ISOCHRONES_URL,
    SOURCE,
    answer_attribution,
    answer_vintage,
    fetch_isochrones,
    isochrone_payload,
    parse_isochrones,
)
from uwcopilot.studio.siteiq.trade_area import (
    DRIVE_MINUTES,
    circle_polygon,
    drive_time_area,
    miles_label,
    near_and_market,
    points_in_polygon,
    ring_area,
    scaled_rings,
)

FIXTURE = "ors/isochrones_handwritten.json"
RECORDED = "ors/isochrones_houston_recorded.json"
SITE = (10.0, -140.0)
FAKE_KEY = "test-ors-key-123"


def test_rings_are_scaled_by_the_metro_multiplier() -> None:
    profile = load_profile("car_wash")
    houston = load_metro("houston_tx")
    rings = scaled_rings(profile, houston)
    expected = [ring * houston.trade_area.ring_multiplier for ring in profile.design.trade_area_rings_miles]
    assert rings == pytest.approx(expected)
    assert near_and_market(rings) == (rings[0], rings[len(rings) // 2])
    assert near_and_market((0.5, 1.0)) == (0.5, 1.0)


def test_ring_membership_and_labels() -> None:
    area = ring_area(*SITE, 1.0)
    assert area.label == "within 1 mile" and miles_label(2.5) == "within 2.5 miles"
    lats = np.array([10.0, 10.01, 10.05])
    lons = np.array([-140.0, -140.0, -140.0])
    assert area.contains(lats, lons).tolist() == [True, True, False]  # 0.01 deg lat ~ 0.69 mi; 0.05 ~ 3.5 mi


def test_points_in_polygon_square() -> None:
    square = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
    lats = np.array([0.5, 1.5, 0.5, -0.1])
    lons = np.array([0.5, 0.5, 1.5, 0.5])
    assert points_in_polygon(lats, lons, square).tolist() == [True, False, False, False]


def test_drive_time_area_and_circle() -> None:
    polygon = [(9.98, -140.02), (9.98, -139.98), (10.02, -139.98), (10.02, -140.02)]
    area = drive_time_area(*SITE, 10, polygon)
    assert area.kind == "drive_time" and area.label == "within a 10-minute drive"
    assert area.contains(np.array([10.0, 10.03]), np.array([-140.0, -140.0])).tolist() == [True, False]
    with pytest.raises(ValueError):
        drive_time_area(*SITE, 10, polygon[:2])
    ring = circle_polygon(*SITE, 1.0, vertices=12)
    assert len(ring) == 13 and ring[0] == ring[-1]


def test_isochrone_payload_and_parsing() -> None:
    payload = isochrone_payload(*SITE, (10, 5))
    assert payload == {"locations": [[-140.0, 10.0]], "range": [300, 600], "range_type": "time"}
    with pytest.raises(ValueError):
        isochrone_payload(*SITE, ())
    areas = parse_isochrones(json.loads(load_fixture(FIXTURE)["body"]))
    assert sorted(areas) == [5, 10, 15]
    assert all(len(points) >= 4 for points in areas.values())
    with pytest.raises(UpstreamError):
        parse_isochrones({"error": {"code": 2000}})


def recorded_site() -> tuple[float, float]:
    """(lat, lon) the recorded answer was requested for (the Census Geocoder's point for a public building)."""
    params = load_fixture("geocoder/coordinates.json")["params"]
    return float(params["y"]), float(params["x"])


def test_recorded_answer_matches_the_request_and_the_parser() -> None:
    record = load_fixture(RECORDED)
    assert record["fixture_origin"].startswith("recorded from the live API on ")
    assert record["params"]["json"] == isochrone_payload(*recorded_site(), DRIVE_MINUTES)
    areas = parse_isochrones(json.loads(record["body"]))
    assert sorted(areas) == sorted(DRIVE_MINUTES)
    for points in areas.values():
        assert len(points) >= 4 and points[0] == points[-1]
    lat, lon = recorded_site()
    for minutes in DRIVE_MINUTES:
        area = drive_time_area(lat, lon, minutes, areas[minutes])
        assert area.contains(np.array([lat]), np.array([lon])).tolist() == [True]


def test_attribution_and_data_dates_come_from_the_answer(tmp_path) -> None:
    body = json.loads(load_fixture(RECORDED)["body"])
    engine = body["metadata"]["engine"]
    assert answer_attribution(body) == body["metadata"]["attribution"]
    assert answer_vintage(body) == (
        f"OpenStreetMap data of {engine['osm_date'][:10]}; routing graph of {engine['graph_date'][:10]}"
    )
    synthetic = json.loads(load_fixture(FIXTURE)["body"])
    assert answer_attribution(synthetic) is None and "no data dates" in answer_vintage(synthetic)
    network = FakeNetwork()
    network.add_fixture(ISOCHRONES_URL, RECORDED)
    client, _ = make_client(tmp_path, network, policies=fast_policies("api.openrouteservice.org"))
    _, source = fetch_isochrones(client, FAKE_KEY, *recorded_site(), DRIVE_MINUTES)
    assert body["metadata"]["attribution"] in source.license_note and source.vintage == answer_vintage(body)
    network = FakeNetwork()
    network.add_fixture(ISOCHRONES_URL, FIXTURE)
    client, _ = make_client(tmp_path / "b", network, policies=fast_policies("api.openrouteservice.org"))
    _, source = fetch_isochrones(client, FAKE_KEY, *SITE, DRIVE_MINUTES)
    assert source.license_note == SOURCE.license_note


def test_fetch_sends_the_key_only_in_the_header(tmp_path) -> None:
    network = FakeNetwork()
    seen: list[httpx.Request] = []
    record = load_fixture(FIXTURE)

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=record["body"].encode("utf-8"))

    network.add(ISOCHRONES_URL, answer)
    client, _ = make_client(tmp_path, network, policies=fast_policies("api.openrouteservice.org"))
    polygons, source = fetch_isochrones(client, FAKE_KEY, *SITE, (5, 10, 15))
    assert sorted(polygons) == [5, 10, 15]
    assert seen[0].method == "POST" and seen[0].headers["Authorization"] == FAKE_KEY
    assert FAKE_KEY not in str(seen[0].url) and FAKE_KEY not in seen[0].content.decode("utf-8")
    assert FAKE_KEY not in source.model_dump_json()
    for path in (tmp_path / "cache").rglob("*"):
        if path.is_file():
            assert FAKE_KEY.encode("utf-8") not in path.read_bytes()


def test_demand_area_falls_back_to_rings_and_records_why(tmp_path, monkeypatch) -> None:
    metro = load_metro("synthetic_metro")
    rings = (0.5, 1.0, 3.0)
    area, info = demand_area(metro, rings, *SITE, None)
    assert area.kind == "ring" and info.demand_method == "rings" and "live client" in info.reason
    monkeypatch.setattr(keys, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    client, _ = make_client(tmp_path, FakeNetwork())
    area, info = demand_area(metro, rings, *SITE, client)
    assert area.kind == "ring" and "ORS_API_KEY is not set" in info.reason
    monkeypatch.setenv("ORS_API_KEY", FAKE_KEY)
    failing = FakeNetwork()
    failing.add(ISOCHRONES_URL, lambda request: httpx.Response(403, content=b"forbidden"))
    client, _ = make_client(tmp_path / "b", failing, policies=fast_policies("api.openrouteservice.org"))
    area, info = demand_area(metro, rings, *SITE, client)
    assert area.kind == "ring" and "OpenRouteService request failed" in info.reason
    assert FAKE_KEY not in info.model_dump_json()


def test_demand_area_uses_drive_time_with_a_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(keys, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setenv("ORS_API_KEY", FAKE_KEY)
    network = FakeNetwork()
    network.add_fixture(ISOCHRONES_URL, FIXTURE)
    client, _ = make_client(tmp_path, network, policies=fast_policies("api.openrouteservice.org"))
    area, info = demand_area(load_metro("synthetic_metro"), (0.5, 1.0, 3.0), *SITE, client)
    assert area.kind == "drive_time" and info.demand_method == "drive_time"
    assert info.drive_minutes == [5, 10, 15] and info.source.source_id == "openrouteservice_isochrones"
    assert info.scoring_method == "rings"


def test_license_context_is_shown_not_scored(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(
        "https://data.cityofchicago.org/resource/r5kz-chrr.json", "city_open_data/chicago_licenses_handwritten.json"
    )
    client, _ = make_client(tmp_path, network, policies=fast_policies("data.cityofchicago.org"))
    metro = load_metro("chicago_il_in")
    items = license_context(metro, load_profile("car_wash"), *SITE, 1.0, client)
    assert len(items) == 1 and items[0].name == "chicago_business_licenses"
    assert (
        items[0].status == "DATA" and items[0].value == 1.0 and items[0].source.source_id == "chicago_business_licenses"
    )
    offline = license_context(metro, load_profile("car_wash"), *SITE, 1.0, None)
    assert offline[0].status == "UNKNOWN" and "live" in offline[0].reason
    no_category = license_context(metro, load_profile("restaurant_cafe"), *SITE, 1.0, client)
    assert no_category[0].status == "UNKNOWN" and no_category[0].value is None
    houston = license_context(load_metro("houston_tx"), load_profile("car_wash"), *SITE, 1.0, client)
    assert houston[0].status == "UNKNOWN" and "houston" in houston[0].reason
