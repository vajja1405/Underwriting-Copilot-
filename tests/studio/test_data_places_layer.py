"""PlacesLayer and the per-tract place counts, on hand-built geometry (distances checkable by hand)."""

import numpy as np
from layer_helpers import HandPlace, offset, places_layer

from uwcopilot.studio.data.geo import haversine_miles_array
from uwcopilot.studio.data.snapshot_layers import place_counts_by_tract, places_margin_miles
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.metros.rings import OWN_TRACT_MAX_MILES, near_and_market, scaled_rings
from uwcopilot.studio.profiles import load_all_profiles

WASH = ("amenity=car_wash",)
PLACES = [
    HandPlace(0.0, 0.3, WASH, "car_wash"),
    HandPlace(0.0, 0.9, WASH, "car_wash"),
    HandPlace(1.2, 0.0, WASH, "car_wash"),
    HandPlace(0.0, -0.5, ("shop=supermarket",), "grocery_store"),
    HandPlace(0.2, 0.2, ("office",), "corporate_or_business_office"),
    HandPlace(-2.5, 0.0, ("railway=station", "public_transport=station"), "train_station"),
]


def test_search_finds_places_within_the_radius_nearest_first() -> None:
    layer = places_layer(PLACES)
    lat, lon = offset(0, 0)
    found = layer.search(lat, lon, 1.0, ["amenity=car_wash"])
    assert [round(poi.distance_miles, 2) for poi in found] == [0.3, 0.9]
    assert found[0].tags["amenity"] == "car_wash" and found[0].tags["overture:category"] == "car_wash"
    assert found[0].name is None and found[0].poi_id.startswith("overture/")
    assert len(layer.search(lat, lon, 1.25, ["amenity=car_wash"])) == 3


def test_filters_follow_the_openstreetmap_convention() -> None:
    layer = places_layer(PLACES)
    lat, lon = offset(0, 0)
    assert len(layer.search(lat, lon, 1.0, ["office"])) == 1
    assert len(layer.search(lat, lon, 1.0, ["office=*"])) == 1
    assert len(layer.search(lat, lon, 3.0, ["public_transport"])) == 1
    assert layer.search(lat, lon, 3.0, ["amenity=cafe"]) == []


def test_unmapped_filters_are_reported_not_counted_as_none_found() -> None:
    layer = places_layer(PLACES)
    tags = ["shop=supermarket", "highway=motorway_junction"]
    assert layer.unmapped(tags) == ["highway=motorway_junction"]
    assert layer.definition_share(tags) == 0.5 and layer.definition_share([]) == 0.0
    assert layer.definition_share(["amenity=car_wash"]) == 1.0


def test_counts_around_equal_a_brute_force_count() -> None:
    layer = places_layer(PLACES)
    points = [offset(north, east) for north, east in [(0, 0), (0, 0.6), (1.0, 0.0), (-2.0, 0.1), (5, 5)]]
    lats, lons = np.array([p[0] for p in points]), np.array([p[1] for p in points])
    counts = layer.counts_around(lats, lons, 0.75, ["amenity=car_wash"])
    expected = []
    wash = [i for i, tags in enumerate(layer.place_tags) if "amenity=car_wash" in tags]
    for lat, lon in points:
        distances = haversine_miles_array(lat, lon, layer.lats[wash], layer.lons[wash])
        expected.append(int((distances <= 0.75).sum()))
    assert counts.tolist() == expected == [1, 2, 1, 0, 0]


def test_place_counts_by_tract_use_the_rings_siteiq_uses() -> None:
    metro = load_metro("houston_tx")
    profiles = load_all_profiles()
    layer = places_layer(PLACES)
    ids = ["T1", "T2"]
    points = [offset(0, 0), offset(0, 2)]
    lats, lons = np.array([p[0] for p in points]), np.array([p[1] for p in points])
    counts = place_counts_by_tract(layer, ids, lats, lons, metro, {"car_wash": profiles["car_wash"]})
    block = counts["profiles"]["car_wash"]
    near, market = near_and_market(scaled_rings(profiles["car_wash"], metro))
    assert block["rings_miles"] == list(scaled_rings(profiles["car_wash"], metro))
    competition = block["factors"]["competition"]
    assert competition["ring_miles"] == near and block["factors"]["tourism"]["ring_miles"] == market
    assert competition["counts"] == layer.counts_around(lats, lons, near, ["amenity=car_wash"]).tolist()
    anchors = block["factors"]["anchors"]
    assert "highway=motorway_junction" in anchors["unmapped_tags"] and anchors["definition_share"] < 1
    assert counts["geoids"] == ids


def test_extract_margin_covers_the_largest_market_ring_plus_the_site_distance() -> None:
    metro = load_metro("houston_tx")
    profiles = load_all_profiles()
    largest = max(near_and_market(scaled_rings(profile, metro))[1] for profile in profiles.values())
    assert places_margin_miles(metro, profiles) == round(largest + OWN_TRACT_MAX_MILES, 3)
