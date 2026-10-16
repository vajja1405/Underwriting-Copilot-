"""SnapshotFabric with stored layers: local lookups, coverage accounting, and SiteIQ measuring like the snapshot."""

import numpy as np
import pytest
from layer_helpers import HandPlace, offset, places_layer, straight_road, write_hand_snapshot

from uwcopilot.studio.data.fabric import SnapshotFabric, coverage_share, latest_snapshot
from uwcopilot.studio.data.fabric_base import FactorCoverage
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.lodes import WATER_ONLY_BASIS, WORKERS_BASIS_FIELD
from uwcopilot.studio.data.snapshot_coverage import RING_SUM_FACTORS, percent_floor
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, ROADS_FILE
from uwcopilot.studio.data.snapshot_layers import daytime_values, place_counts_by_tract
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.metros.rings import scaled_rings
from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.siteiq.context import MetroContext
from uwcopilot.studio.siteiq.factors import SUM_LABELS, measure_point

PROFILES = load_all_profiles()
METRO = load_metro("houston_tx")
ACS = {"resident_population": 3000, "households": 1200, "renter_households": 500, "median_household_income": 60000}


def tract_values(north: float, east: float, workers: int | None) -> dict:
    lat, lon = offset(north, east)
    return {"lat": lat, "lon": lon, "land_area_sq_miles": 1.0, **ACS, "daytime_workers": workers}


TRACTS = {
    "48201000100": tract_values(0, 0, 900),
    "48201000200": tract_values(0, 1.5, 400),
    "48201000300": tract_values(1.5, 0, None),
    "48201000400": tract_values(3, 3, 50),
}
PLACES = [
    HandPlace(0.1, 0.1, ("amenity=car_wash",), "car_wash"),
    HandPlace(0.0, 1.4, ("amenity=car_wash",), "car_wash"),
    HandPlace(0.2, 0.0, ("shop=supermarket",), "grocery_store"),
    HandPlace(1.4, 0.1, ("amenity=fuel",), "gas_station"),
]
ROADS = [straight_road("MAIN", 15000, [(0.2, -1), (0.2, 2)]), straight_road("SIDE", 3000, [(-1, 1.6), (1, 1.6)])]


@pytest.fixture
def fabric(tmp_path) -> SnapshotFabric:
    write_hand_snapshot(tmp_path, TRACTS, PLACES, ROADS)
    return SnapshotFabric(metro=METRO, snapshot=latest_snapshot("houston_tx", tmp_path))


def rows_by_factor(fabric: SnapshotFabric, profile_id: str) -> dict[str, FactorCoverage]:
    return {row.factor: row for row in fabric.coverage(PROFILES[profile_id])}


def test_layers_load_and_answer_without_the_network(fabric) -> None:
    lat, lon = offset(0, 0)
    search = fabric.sites_near(lat, lon, 0.5, ["amenity=car_wash", "highway=motorway_junction"])
    assert search.status == "DATA" and len(search.pois) == 1 and "highway=motorway_junction" in search.reason
    assert fabric.sites_near(lat, lon, 1, ["highway=motorway_junction"]).status == "UNKNOWN"
    road = fabric.nearest_road(lat, lon)
    assert road.status == "DATA" and road.segment.route_id == "MAIN" and road.distance_miles == pytest.approx(0.2)
    assert fabric.nearest_road(*offset(3, 3)).status == "UNKNOWN"
    assert fabric.point_layers_local


def test_centroid_answers_equal_a_fresh_search(fabric) -> None:
    layer, _ = fabric.roads()
    for item in TRACTS.values():
        stored = fabric.nearest_road(item["lat"], item["lon"])
        moved = layer.nearest(item["lat"] + 1e-12, item["lon"], 0.5)
        if moved is None:
            assert stored.segment is None
        else:
            assert stored.segment == layer.segment(moved[0])
            assert stored.distance_miles == pytest.approx(moved[1], abs=1e-6)


def test_daytime_workers_are_data_or_unknown_never_zero(fabric) -> None:
    known = fabric.tract_features("48201000100")["daytime_workers"]
    assert known.status == "DATA" and known.value == 900 and "jobs located in tract" in known.reason
    missing = fabric.tract_features("48201000300")["daytime_workers"]
    assert missing.status == "UNKNOWN" and missing.value is None and "zero" in missing.reason


def test_coverage_counts_tract_shares_and_tag_definitions(fabric) -> None:
    car_wash = rows_by_factor(fabric, "car_wash")
    # MAIN (0.2 miles north of the origin) is within 0.5 miles of tracts 1 and 2; tracts 3 and 4 have no road.
    assert car_wash["traffic_aadt"].status == "DATA" and car_wash["traffic_aadt"].share_of_tracts == 0.5
    assert car_wash["competition"].definition_share == 1.0 and car_wash["competition"].status == "DATA"
    assert car_wash["anchors"].definition_share == 0.8 and "highway=motorway_junction" in car_wash["anchors"].reason
    restaurant = rows_by_factor(fabric, "restaurant_cafe")
    # Tract 3 has no LODES value: 3 of 4 tracts have one, but tract 1 is within tract 3's market ring too.
    workers = restaurant["daytime_workers"]
    assert workers.status == "DATA" and workers.share_of_tracts == 0.5
    assert "75.0% of tracts have a value" in workers.reason
    stays = rows_by_factor(fabric, "short_term_rental")
    assert stays["str_comparables"].status == "UNKNOWN" and stays["tourism"].definition_share < 1


def test_summed_factors_count_the_tracts_whose_ring_sum_siteiq_can_compute(fabric) -> None:
    ctx = MetroContext(metro=METRO, fabric=fabric)
    for profile_id in ("restaurant_cafe", "retail_storefront", "laundromat"):
        profile = PROFILES[profile_id]
        rings = scaled_rings(profile, METRO)
        rows = rows_by_factor(fabric, profile_id)
        for factor in set(RING_SUM_FACTORS) & set(rows):
            measured = [
                measure_point(ctx, profile, item["lat"], item["lon"], rings, point_layers=False)[factor].value
                for item in TRACTS.values()
            ]
            expected = sum(value is not None for value in measured) / len(measured)
            assert rows[factor].share_of_tracts == pytest.approx(expected), (profile_id, factor)
    assert set(RING_SUM_FACTORS) == set(SUM_LABELS)


def test_percentages_in_reasons_are_rounded_down() -> None:
    assert percent_floor(0.9952) == "99.5%" and percent_floor(0.99999) == "99.9%"
    assert percent_floor(0.995) == "99.5%" and percent_floor(0.0058) == "0.5%"
    assert percent_floor(1.0) == "100.0%" and percent_floor(0.0) == "0.0%"


def test_a_search_reaching_outside_the_extract_box_is_unknown_not_zero(fabric) -> None:
    layer, _ = fabric.places()
    xmin, xmax, ymin, ymax = layer.bbox
    inside = fabric.sites_near((ymin + ymax) / 2, (xmin + xmax) / 2, 1.0, ["amenity=car_wash"])
    assert inside.status == "DATA"
    edge = fabric.sites_near((ymin + ymax) / 2, xmax - 0.001, 1.0, ["amenity=car_wash"])
    far = fabric.sites_near(ymax + 0.5, xmin - 2.0, 1.5, ["amenity=car_wash"])
    for search in (edge, far):
        assert search.status == "UNKNOWN" and search.pois == [] and "outside the Overture extract" in search.reason


def test_water_only_tracts_without_a_block_get_zero_with_the_reason(tmp_path) -> None:
    tracts = {
        "48201000100": tract_values(0, 0, 900),
        "48201990000": {**tract_values(0, 1.5, None), "land_area_sq_miles": 0.0, "resident_population": 0},
        "48201000300": tract_values(1.5, 0, None),
    }
    ids = sorted(tracts)
    values, water_only = daytime_values(ids, {"48201000100": 900}, tracts)
    assert water_only == ["48201990000"] and values["48201000300"] == {"daytime_workers": None}
    assert values["48201990000"] == {"daytime_workers": 0, WORKERS_BASIS_FIELD: WATER_ONLY_BASIS}
    for geoid, item in values.items():
        tracts[geoid].update(item)
    write_hand_snapshot(tmp_path, tracts, PLACES, ROADS)
    fabric = SnapshotFabric(metro=METRO, snapshot=latest_snapshot("houston_tx", tmp_path))
    water = fabric.tract_features("48201990000")["daytime_workers"]
    assert water.status == "DATA" and water.value == 0 and "water only" in water.reason
    assert fabric.tract_features("48201000300")["daytime_workers"].status == "UNKNOWN"


def test_hand_built_snapshot_is_labelled_synthetic(fabric) -> None:
    assert fabric.snapshot.manifest["data_label"] == "SYNTHETIC"
    layer, _ = fabric.places()
    assert layer is not None


def test_coverage_share_multiplies_tract_share_by_definition_share() -> None:
    row = FactorCoverage(
        factor="anchors",
        scope="poi_layer",
        weight=1.0,
        status="DATA",
        share_of_tracts=0.5,
        definition_share=0.8,
        source="overture_places",
        freshness=None,
        reason="r",
    )
    assert coverage_share([row]) == pytest.approx(0.4)


def test_a_changed_layer_file_is_unknown_with_the_reason(tmp_path) -> None:
    folder = write_hand_snapshot(tmp_path, TRACTS, PLACES, ROADS)
    (folder / PLACES_FILE).write_text("{}", encoding="utf-8")
    (folder / ROADS_FILE).unlink()
    fabric = SnapshotFabric(metro=METRO, snapshot=latest_snapshot("houston_tx", tmp_path))
    search = fabric.sites_near(*offset(0, 0), 1, ["amenity=car_wash"])
    assert search.status == "UNKNOWN" and "does not match the SHA-256" in search.reason
    road = fabric.nearest_road(*offset(0, 0))
    assert road.status == "UNKNOWN" and "missing" in road.reason
    rows = rows_by_factor(fabric, "car_wash")
    assert rows["competition"].status == "UNKNOWN" and rows["traffic_aadt"].status == "UNKNOWN"


def test_without_stored_layers_a_live_client_keeps_the_layers_live_per_site(tmp_path) -> None:
    write_hand_snapshot(tmp_path, TRACTS, None, None)
    snapshot = latest_snapshot("houston_tx", tmp_path)
    offline = SnapshotFabric(metro=METRO, snapshot=snapshot)
    assert offline.point_layers_local and offline.sites_near(*offset(0, 0), 1, ["amenity=car_wash"]).status == "UNKNOWN"
    live = SnapshotFabric(metro=METRO, snapshot=snapshot, live=PoliteClient(cache=None, offline=True))
    assert not live.point_layers_local
    assert rows_by_factor(offline, "car_wash")["competition"].share_of_tracts == 0.0


def test_siteiq_measures_places_exactly_like_the_snapshot_counts(fabric) -> None:
    ctx = MetroContext(metro=METRO, fabric=fabric)
    profile = PROFILES["car_wash"]
    ids = sorted(TRACTS)
    lats = np.array([TRACTS[geoid]["lat"] for geoid in ids])
    lons = np.array([TRACTS[geoid]["lon"] for geoid in ids])
    stored = place_counts_by_tract(places_layer(PLACES), ids, lats, lons, METRO, {"car_wash": profile})
    factors = stored["profiles"]["car_wash"]["factors"]
    rings = scaled_rings(profile, METRO)
    for position, geoid in enumerate(ids):
        measured = measure_point(ctx, profile, TRACTS[geoid]["lat"], TRACTS[geoid]["lon"], rings)
        assert measured["competition"].value == factors["competition"]["counts"][position]
        assert measured["anchors"].value == factors["anchors"]["counts"][position]
        assert measured["competition"].details["count_market"] == factors["competition_market"]["counts"][position]
