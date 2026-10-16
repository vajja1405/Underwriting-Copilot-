"""The HPMS road layer: county downloads page by page, nearest-segment lookups, storage and the per-tract rule."""

import io
import json

import httpx
import numpy as np
import pytest
from layer_helpers import fixture_record, offset, road_layer, straight_road
from studio_helpers import FakeNetwork, fast_policies, make_client

from uwcopilot.studio.data import traffic_hpms
from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.data.geo import point_to_polyline_miles, point_to_segments_miles
from uwcopilot.studio.data.road_layer import RoadLayer

ROADS = [
    straight_road("EAST-WEST", 20000, [(0.3, -2), (0.3, 2)]),
    straight_road("NORTH-SOUTH", 5000, [(-2, 0.4), (-1, 0.4), (2, 0.4)]),
    straight_road("POINT", 900, [(0.1, 0.1)]),
]


def test_vectorized_distance_equals_the_polyline_distance() -> None:
    rng = np.random.default_rng(7)
    for _ in range(50):
        lat, lon = offset(*rng.uniform(-1, 1, 2))
        line = [offset(*rng.uniform(-1, 1, 2)) for _ in range(4)]
        lats, lons = np.array([p[0] for p in line]), np.array([p[1] for p in line])
        pieces = point_to_segments_miles(lat, lon, lats[:-1], lons[:-1], lats[1:], lons[1:])
        assert pieces.min() == pytest.approx(point_to_polyline_miles(lat, lon, line), abs=1e-9)


def test_nearest_segment_and_distance() -> None:
    layer = road_layer(ROADS)
    lat, lon = offset(0, 0)
    index, miles = layer.nearest(lat, lon, 0.5)
    assert layer.segment(index).route_id == "POINT" and miles == pytest.approx(0.1414, abs=1e-3)
    index, miles = layer.nearest(*offset(1.0, 0.35), 0.5)
    assert layer.segment(index).route_id == "NORTH-SOUTH" and miles == pytest.approx(0.05, abs=1e-3)
    assert layer.nearest(*offset(5, 5), 0.5) is None
    assert layer.distance_to(index, *offset(1.0, 0.35)) == pytest.approx(miles)


def test_cutoff_is_inclusive_of_the_search_distance_only() -> None:
    layer = road_layer([straight_road("ONLY", 100, [(0.0, -1), (0.0, 1)])])
    assert layer.nearest(*offset(0.49, 0), 0.5) is not None
    assert layer.nearest(*offset(0.51, 0), 0.5) is None


def test_ties_go_to_the_lower_segment_index() -> None:
    twins = [straight_road("FIRST", 1, [(0.2, -1), (0.2, 1)]), straight_road("SECOND", 2, [(0.2, -1), (0.2, 1)])]
    index, _ = road_layer(twins).nearest(*offset(0, 0), 0.5)
    assert index == 0


def test_save_and_load_round_trip_without_pickles(tmp_path) -> None:
    layer = road_layer(ROADS)
    path = tmp_path / "roads.npz"
    layer.save(path)
    loaded = RoadLayer.load(path)
    assert len(loaded) == 3 and loaded.segment(1) == layer.segment(1)
    assert loaded.segment(2).line == layer.segment(2).line and loaded.segment(0).route_name is None
    buffer = io.BytesIO()
    np.savez_compressed(buffer, aadt=np.array([1]))
    broken = tmp_path / "broken.npz"
    broken.write_bytes(buffer.getvalue())
    with pytest.raises(ValueError, match="missing arrays"):
        RoadLayer.load(broken)


def test_pieces_cover_every_vertex_pair_and_single_points() -> None:
    layer = road_layer(ROADS)
    assert len(layer.pieces["segment"]) == 1 + 2 + 1
    assert layer.pieces["segment"].tolist() == [0, 1, 1, 2]


def test_layer_from_recorded_segments_agrees_with_the_live_lookup_rule() -> None:
    record = fixture_record("hpms/segments_near.json")
    segments = traffic_hpms.parse_segments(json.loads(record["body"]))
    layer = RoadLayer.from_segments(segments, [0] * len(segments))
    lat, lon = 29.760724, -95.369483
    expected, miles = traffic_hpms.nearest_segment(segments, lat, lon)
    index, found = layer.nearest(lat, lon, 0.5)
    assert layer.segment(index).route_id == expected.route_id and found == pytest.approx(miles, abs=1e-9)


def county_network(pages: list[str], count: int) -> FakeNetwork:
    """Recorded pages in order, then an empty page; the count answer is hand-built to match the test."""
    network = FakeNetwork()
    bodies = [fixture_record(name)["body"] for name in pages]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": count})
        page = int(request.url.params["resultOffset"]) // int(request.url.params["resultRecordCount"])
        body = bodies[page] if page < len(bodies) else '{"type": "FeatureCollection", "features": []}'
        return httpx.Response(200, text=body, headers={"content-type": "application/geo+json"})

    network.add(traffic_hpms.layer_url("TX"), handler)
    return network


def test_county_download_follows_pages_until_the_server_stops(tmp_path) -> None:
    network = county_network(["hpms/county_201_page0.json", "hpms/county_201_page1.json"], 10)
    client, _ = make_client(tmp_path, network, policies=fast_policies("geo.dot.gov"))
    result = traffic_hpms.fetch_county_segments(client, "TX", "201", page_size=5)
    assert result.features_read == 10 and len(result.segments) == 10 and len(result.records) == 3
    assert result.page_of_segment == [0] * 5 + [1] * 5
    assert [record.geography for record in result.records][0] == "state TX county 201 page 0"
    params = network.requests[0].url.params
    assert params["orderByFields"] == "objectid" and params["maxAllowableOffset"] == traffic_hpms.SIMPLIFY_DEGREES


def test_more_pages_reads_both_answer_shapes() -> None:
    assert traffic_hpms.more_pages({"exceededTransferLimit": True})
    assert traffic_hpms.more_pages({"properties": {"exceededTransferLimit": True}})
    assert not traffic_hpms.more_pages({"features": []})


def test_county_request_never_asks_for_more_than_the_page_size() -> None:
    params = traffic_hpms.county_segments_request("201", 4000)
    assert params["resultOffset"] == "4000" and params["resultRecordCount"] == str(traffic_hpms.PAGE_SIZE)
    assert params["where"] == "county_id = 201 AND aadt > 0" and "returnCountOnly" not in params
    with pytest.raises(ValueError):
        traffic_hpms.county_segments_request("2O1", 0)


def test_a_partial_county_download_fails_the_roads_step(tmp_path) -> None:
    from uwcopilot.studio.data.snapshot_layers import build_roads_layer
    from uwcopilot.studio.metros import load_metro

    metro = load_metro("houston_tx").model_copy(update={"counties": load_metro("houston_tx").counties[:1]})
    network = county_network(["hpms/county_201_page0.json", "hpms/county_201_page1.json"], 11)
    client, _ = make_client(tmp_path, network, policies=fast_policies("geo.dot.gov"))
    tracts = {"48201100001": {"lat": 29.7547559, "lon": -95.3621919}}
    with pytest.raises(UpstreamError, match="10 segments downloaded but the layer counts 11"):
        build_roads_layer(client, metro, tracts, {"48": "TX"})
