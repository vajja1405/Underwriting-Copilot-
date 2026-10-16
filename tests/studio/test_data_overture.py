"""Overture places: the pinned release, the reviewed category map, the accounting and the extract (no network)."""

import hashlib
import json
from datetime import date

import httpx
import pytest
from layer_helpers import HandPlace, SampleReader, fixture_record, places_payload
from studio_helpers import FIXTURES, FakeNetwork, make_client

from uwcopilot.studio.data import overture_places
from uwcopilot.studio.data.errors import HostUnreachable, NotAvailable, UpstreamError
from uwcopilot.studio.data.geo import miles_to_degrees
from uwcopilot.studio.data.overture_config import (
    CATEGORY_MAP_FILE,
    CategoryMap,
    file_sha256,
    load_category_map,
    load_settings,
)
from uwcopilot.studio.data.overture_places import (
    LICENSE_RULE,
    BBox,
    DuckDbPlacesReader,
    PathCount,
    PlaceRow,
    account,
    build_extract_body,
    check_listing,
    extract_key,
    extract_metro_places,
    list_release_files,
    merge_facilities,
    parse_listing,
    summary,
    tag_places,
)
from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.profiles.factors import TOURISM_TAGS

SAMPLE = FIXTURES / "overture" / "places_sample_cc0.parquet"
SAMPLE_INFO = FIXTURES / "overture" / "places_sample_cc0.json"
SETTINGS = load_settings()
CMAP = load_category_map()
S3 = SETTINGS.release.s3_https_base


def listing_network() -> FakeNetwork:
    network = FakeNetwork()
    network.add_fixture(f"{S3}/", "overture/listing.json")
    return network


SAMPLE_BOX = BBox(-95.40, -95.33, 29.73, 29.82)


def test_release_is_pinned_once_with_its_documentation() -> None:
    release = SETTINGS.release
    assert release.id == "2026-09-23.1" and release.places_prefix.startswith(f"release/{release.id}/theme=places/")
    assert len(SETTINGS.sources) >= 5 and all(len(item.sha256) == 64 for item in SETTINGS.sources)
    urls = {item.url for item in SETTINGS.sources}
    assert "https://docs.overturemaps.org/attribution/" in urls and "https://cdla.dev/permissive-2-0/" in urls


def test_license_note_records_the_mixed_licenses_honestly() -> None:
    note = SETTINGS.license_note()
    for name in ("CDLA Permissive 2.0", "Apache 2.0", "CC0 1.0", "Mixed licenses", "no OpenStreetMap data"):
        assert name in note
    assert set(SETTINGS.license.licenses_by_source) == {"CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0"}


def test_every_profile_tag_is_mapped_or_listed_as_unmapped_with_a_reason() -> None:
    mapped = CMAP.mapped_tags()
    unmapped = {item.tag: item.reason for item in CMAP.unmapped_profile_tags}
    wanted = set(TOURISM_TAGS)
    for profile in load_all_profiles().values():
        wanted |= set(profile.competitor_tags) | set(profile.anchor_tags)
    for tag in sorted(wanted):
        assert tag in mapped or unmapped.get(tag), tag
    assert all(row.note.strip() for row in CMAP.rows)


def test_most_specific_category_decides_and_reviewed_rows_stop_the_walk() -> None:
    assert CMAP.tags_for(["lodging", "hotel", "motel"]) == ("tourism=motel",)
    assert CMAP.tags_for(["lodging", "hotel"]) == ("tourism=hotel",)
    assert CMAP.tags_for(["travel_and_transportation", "airport", "airport_terminal"]) == ()
    assert CMAP.tags_for(["services_and_business", "financial_service", "atm"]) is None
    assert CMAP.tags_for(None) is None and CMAP.tags_for([]) is None
    assert set(CMAP.search_categories()) == {row.category for row in CMAP.rows if row.tags}


def test_category_map_refuses_duplicates_bad_tags_and_contradictions() -> None:
    row = {"category": "car_wash", "tags": ["amenity=car_wash"], "note": "n"}
    base = {"reviewed_on": "2026-10-13", "release": "2026-09-23.1"}
    with pytest.raises(ValueError, match="more than one row"):
        CategoryMap.model_validate({**base, "rows": [row, row]})
    with pytest.raises(ValueError, match="invalid tag"):
        CategoryMap.model_validate({**base, "rows": [{**row, "tags": ['amenity="x"']}]})
    with pytest.raises(ValueError, match="both mapped and listed"):
        CategoryMap.model_validate(
            {**base, "rows": [row], "unmapped_profile_tags": [{"tag": "amenity=car_wash", "reason": "r"}]}
        )


def test_accounting_puts_every_place_in_exactly_one_bucket() -> None:
    counts = [
        PathCount(None, False, None, 5),
        PathCount(("services_and_business", "financial_service", "atm"), True, None, 7),
        PathCount(("travel_and_transportation", "airport", "airport_terminal"), True, None, 2),
        PathCount(("travel_and_transportation", "vehicle_service", "automotive_service", "car_wash"), True, "open", 3),
        PathCount(("travel_and_transportation", "vehicle_service", "automotive_service", "car_wash"), False, None, 4),
        PathCount(("food_and_drink", "restaurant"), True, "permanently_closed", 1),
    ]
    result = account(counts, CMAP, ["permanently_closed"])
    assert result.places_in_bbox == 22 and result.without_taxonomy == 5 and result.unmapped == 7
    assert result.unmapped_categories == {"atm": 7} and result.not_counted_by_row == 2
    assert result.mapped == 8 and result.mapped_below_confidence == 4 and result.mapped_excluded_status == 1
    assert result.kept == 3
    total = result.without_taxonomy + result.unmapped + result.not_counted_by_row + result.mapped
    assert total == result.places_in_bbox


def test_recorded_listing_parses_to_the_release_files() -> None:
    record = fixture_record("overture/listing.json")
    assert record["fixture_origin"].startswith("recorded from the live API")
    files, token = parse_listing(record["body"].encode("utf-8"))
    assert token is None and len(files) == 16
    assert all(item.key.startswith(SETTINGS.release.places_prefix) and item.etag and item.size > 0 for item in files)


def test_listing_check_refuses_doctype_and_non_listings(tmp_path) -> None:
    network = FakeNetwork()
    network.add(f"{S3}/", lambda request: httpx.Response(200, text="<!DOCTYPE x><ListBucketResult/>"))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError, match="DOCTYPE"):
        list_release_files(client, SETTINGS)
    other = FakeNetwork()
    other.add(f"{S3}/", lambda request: httpx.Response(200, text="<Error>AccessDenied</Error>"))
    client, _ = make_client(tmp_path / "b", other)
    with pytest.raises(UpstreamError, match="not an S3 bucket listing"):
        check_listing(client.get(f"{S3}/"))


def test_recorded_sample_matches_its_sidecar_and_ships_the_cdla_text() -> None:
    info = json.loads(SAMPLE_INFO.read_text(encoding="utf-8"))
    assert info["fixture_origin"].startswith("recorded from the live API on") and "DuckDB" in info["fixture_origin"]
    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == info["file_sha256"]
    text = (FIXTURES / "overture" / "CDLA-Permissive-2.0.txt").read_text(encoding="utf-8")
    assert text.startswith("Community Data License Agreement - Permissive - Version 2.0")


def test_duckdb_reader_on_the_recorded_sample_reads_no_names() -> None:
    reader = DuckDbPlacesReader()
    counts = reader.path_counts([str(SAMPLE)], SAMPLE_BOX, 0.5)
    assert sum(item.count for item in counts) == 60
    rows = reader.places([str(SAMPLE)], SAMPLE_BOX, CMAP.search_categories(), 0.5, ["permanently_closed"])
    # The sample keeps rows whose first source is AllThePlaces (CC0); their later sources are Overture's own
    # CDLA-licensed contributions, so the stored license string names both (overture_places.LICENSE_RULE).
    assert rows and all(isinstance(row, PlaceRow) and row.license == "CC0-1.0+CDLA-Permissive-2.0" for row in rows)
    assert {row.dataset for row in rows} == {"AllThePlaces"}
    assert all(not hasattr(row, "name") for row in rows)


def test_extract_body_keeps_only_mapped_places_and_agrees_with_the_accounting() -> None:
    files, _ = parse_listing(fixture_record("overture/listing.json")["body"].encode("utf-8"))
    body = build_extract_body(files, SETTINGS, CMAP, SAMPLE_BOX, "a" * 64, SampleReader())
    payload = json.loads(body)
    assert payload["accounting"]["places_in_bbox"] == 60
    assert len(payload["rows"]) == payload["accounting"]["kept"] > 0
    assert set(payload["categories"]) <= {row.category for row in CMAP.rows if row.tags}
    assert payload["kept_by_license"] == {"CC0-1.0+CDLA-Permissive-2.0": len(payload["rows"])}
    assert payload["license_rule"] == LICENSE_RULE and payload["data_label"] == "PUBLIC"
    assert payload["columns"] == ["lon", "lat", "confidence", "tags", "category", "dataset", "license"]
    assert all(len(row) == len(payload["columns"]) for row in payload["rows"])
    assert "kept" in summary(payload) and "unmapped" in summary(payload)


def test_inconsistent_reader_answers_are_refused() -> None:
    class Disagreeing(SampleReader):
        def places(self, files, bbox, categories, min_confidence, excluded_status):
            return super().places(files, bbox, categories, min_confidence, excluded_status)[:-1]

    files, _ = parse_listing(fixture_record("overture/listing.json")["body"].encode("utf-8"))
    with pytest.raises(UpstreamError, match="inconsistent answers"):
        build_extract_body(files, SETTINGS, CMAP, SAMPLE_BOX, "a" * 64, Disagreeing())


def test_extract_is_cached_with_checksum_request_date_and_license(tmp_path) -> None:
    client, _ = make_client(tmp_path, listing_network())
    SampleReader.calls = 0
    first = extract_metro_places(client, "houston_tx", SAMPLE_BOX, reader=SampleReader())
    assert first.record.origin == "live" and first.record.sha256 == hashlib.sha256(first.body).hexdigest()
    assert first.record.params["release"] == "2026-09-23.1" and first.record.params["bbox"] == SAMPLE_BOX.text()
    assert first.record.params["category_map_sha256"] == file_sha256(CATEGORY_MAP_FILE)
    assert "CDLA" in first.record.license_note and first.record.retrieved_at.tzinfo is not None
    second = extract_metro_places(client, "houston_tx", SAMPLE_BOX, reader=SampleReader())
    assert second.record.origin == "cache" and second.body == first.body and SampleReader.calls == 1


def test_offline_without_a_cached_extract_names_the_host(tmp_path) -> None:
    client, _ = make_client(tmp_path, listing_network())
    client.get(f"{S3}/", {"list-type": "2", "prefix": SETTINGS.release.places_prefix})
    client.offline = True
    with pytest.raises(HostUnreachable, match="overturemaps"):
        extract_metro_places(client, "houston_tx", SAMPLE_BOX, reader=SampleReader())


def test_bbox_around_widens_by_the_margin() -> None:
    box = BBox.around([29.7, 29.8], [-95.4, -95.3], 1.0)
    assert box.ymin < 29.7 and box.ymax > 29.8 and box.xmin < -95.4 and box.xmax > -95.3
    assert round((box.ymax - 29.8) * 69.09, 1) == 1.0
    assert len(box.text().split(",")) == 4 and box.as_list() == [box.xmin, box.xmax, box.ymin, box.ymax]


AIRPORT = ("travel_and_transportation", "air_transport_facility_or_service", "airport")
HOSPITAL = ("health_care", "hospital")
CHILDRENS = ("health_care", "hospital", "childrens_hospital")
WASH = ("travel_and_transportation", "vehicle_service", "automotive_service", "car_wash")


def hand_row(hierarchy: tuple[str, ...], north_miles: float, east_miles: float, confidence: float = 0.9) -> PlaceRow:
    """A hand-built place (not recorded data) at a mile offset from a fixed origin."""
    dlat, dlon = miles_to_degrees(29.76, north_miles, east_miles)
    return PlaceRow(hierarchy, -95.37 + dlon, 29.76 + dlat, confidence, "hand-built", "hand-built")


def test_facility_rows_merge_the_places_of_one_facility() -> None:
    distances = CMAP.merge_distances()
    assert set(distances) == {"airport", "hospital", "college_university"}
    airport_miles, hospital_miles = distances["airport"], distances["hospital"]
    rows = [
        hand_row(AIRPORT, 0, 0, 0.95),
        hand_row(AIRPORT, 0, airport_miles * 0.5),  # same airport: within the merge distance of the kept place
        hand_row(AIRPORT, 0, airport_miles * 0.9),  # also within it of the kept place (never chained)
        hand_row(AIRPORT, 0, airport_miles * 3),  # another airport
        hand_row(HOSPITAL, 5, 0, 0.6),
        hand_row(CHILDRENS, 5, hospital_miles * 0.5, 0.8),  # decided by the hospital row: merged with it
        hand_row(WASH, 8, 0),
        hand_row(WASH, 8, 0.01),  # car washes are not a facility row: two places stay two
    ]
    places, merged = merge_facilities(tag_places(rows, CMAP), CMAP)
    assert merged == {"airport": 2, "hospital": 1}
    kept = sorted((place.row, place.category, place.confidence) for place in places)
    assert kept == [
        ("airport", "airport", 0.9),
        ("airport", "airport", 0.95),
        ("car_wash", "car_wash", 0.9),
        ("car_wash", "car_wash", 0.9),
        ("hospital", "childrens_hospital", 0.8),  # the highest confidence place of the facility is kept
    ]


def test_merge_never_chains_kept_places() -> None:
    miles = CMAP.merge_distances()["hospital"]
    rows = [hand_row(HOSPITAL, 0, step * miles * 0.6, 0.9 - step * 0.01) for step in range(5)]
    places, merged = merge_facilities(tag_places(rows, CMAP), CMAP)
    # Kept: offsets 0, 1.2 and 2.4 (x miles); 0.6 and 1.8 are within the distance of a kept place and merged.
    assert len(places) == 3 and merged == {"hospital": 2}


def test_extract_bytes_do_not_depend_on_the_order_rows_are_read() -> None:
    class Reversed(SampleReader):
        def places(self, files, bbox, categories, min_confidence, excluded_status):
            return list(reversed(super().places(files, bbox, categories, min_confidence, excluded_status)))

    files, _ = parse_listing(fixture_record("overture/listing.json")["body"].encode("utf-8"))
    first = build_extract_body(files, SETTINGS, CMAP, SAMPLE_BOX, "a" * 64, SampleReader())
    second = build_extract_body(files, SETTINGS, CMAP, SAMPLE_BOX, "a" * 64, Reversed())
    assert first == second
    one = hand_row(WASH, 0, 0)
    tied = [one, PlaceRow(WASH, one.lon, one.lat, one.confidence, "other", "CC0-1.0")]
    assert tag_places(tied, CMAP) == tag_places(list(reversed(tied)), CMAP)


def test_extract_cache_key_names_the_code_version(monkeypatch) -> None:
    files, _ = parse_listing(fixture_record("overture/listing.json")["body"].encode("utf-8"))
    key = extract_key(SETTINGS, files, SAMPLE_BOX, "a" * 64)
    assert key["format"] == overture_places.FORMAT and key["rules_version"] == overture_places.RULES_VERSION
    assert len(key["queries_sha256"]) == 64
    monkeypatch.setattr(overture_places, "RULES_VERSION", "changed")
    assert extract_key(SETTINGS, files, SAMPLE_BOX, "a" * 64) != key
    monkeypatch.setattr(overture_places, "PLACES_SQL", overture_places.PLACES_SQL + " ")
    assert extract_key(SETTINGS, files, SAMPLE_BOX, "a" * 64)["queries_sha256"] != key["queries_sha256"]


def test_a_release_past_overtures_public_window_is_refused(tmp_path) -> None:
    release = SETTINGS.release
    assert release.release_date == "2026-09-25" and release.public_days_max == 60
    assert release.retire_on() == date(2026, 11, 24)
    client, _ = make_client(tmp_path, listing_network())
    extract = extract_metro_places(client, "houston_tx", SAMPLE_BOX, reader=SampleReader(), today=date(2026, 11, 24))
    assert extract.record.origin == "live" and "release 2026-09-23.1 of 2026-09-25" in extract.record.vintage
    with pytest.raises(NotAvailable, match="public window"):
        extract_metro_places(client, "houston_tx", SAMPLE_BOX, reader=SampleReader(), today=date(2026, 11, 25))
    assert "GDPR" in SETTINGS.license.retention and "Retention:" in SETTINGS.license_note()


def test_hand_built_payloads_can_be_labelled_synthetic() -> None:
    payload = places_payload([HandPlace(0, 0, ("amenity=car_wash",), "car_wash")])
    assert payload["data_label"] == "SYNTHETIC"


def test_merge_distance_needs_a_counted_row() -> None:
    base = {"reviewed_on": "2026-10-13", "release": "2026-09-23.1"}
    row = {"category": "airport_terminal", "tags": [], "note": "n", "merge_within_miles": 1.0}
    with pytest.raises(ValueError, match="merge_within_miles needs tags"):
        CategoryMap.model_validate({**base, "rows": [row]})


def test_second_review_rows_count_lodging_food_and_clubs_and_skip_kindergartens() -> None:
    eatery = ("food_and_drink", "casual_eatery")
    beverage = ("food_and_drink", "non_alcoholic_beverage_venue")
    assert CMAP.tags_for(["lodging"]) == ("tourism=hotel",)
    assert CMAP.tags_for(["lodging", "resort", "beach_resort"]) == ("tourism=hotel",)
    assert CMAP.tags_for(["lodging", "lodge"]) == ("tourism=hotel",)
    assert CMAP.tags_for(["lodging", "hotel", "motel"]) == ("tourism=motel",)
    assert CMAP.tags_for(["lodging", "cabin"]) == ("tourism=apartment",)
    for skipped in ("hostel", "campground", "rv_park", "holiday_park", "retreat"):
        assert CMAP.tags_for(["lodging", skipped]) == (), skipped
    assert CMAP.tags_for([*eatery, "food_truck_stand"]) == ("amenity=fast_food",)
    assert CMAP.tags_for([*eatery, "dessert_shop", "donut_shop"]) == ("amenity=cafe",)
    assert CMAP.tags_for([*eatery, "dessert_shop", "ice_cream_shop"]) == ("amenity=ice_cream",)
    assert CMAP.tags_for([*beverage, "bubble_tea_shop"]) == ("amenity=cafe",)
    assert CMAP.tags_for([*beverage, "coffee_shop", "coffee_roastery"]) == ("amenity=cafe",)
    assert CMAP.tags_for([*eatery, "candy_store", "chocolatier"]) == ()
    assert CMAP.tags_for(["shopping", "warehouse_club_store"]) == ("shop=supermarket",)
    assert CMAP.tags_for(["education", "place_of_learning", "school", "kindergarten"]) == ()
    assert CMAP.tags_for(["education", "place_of_learning", "school", "elementary_school"]) == ("amenity=school",)
