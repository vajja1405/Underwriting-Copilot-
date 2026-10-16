"""BLS wages, FRED rates, FHWA HPMS, OpenStreetMap, HUD, city open data, Inside Airbnb and state traffic adapters."""

from datetime import date

import httpx
import pytest
from studio_helpers import FakeNetwork, fixture_body, load_fixture, make_client

from uwcopilot.studio.data import city_open_data, hud_fmr, osm, rates, str_comps, traffic_hpms, traffic_state, wages
from uwcopilot.studio.data.errors import MissingApiKey, NotAvailable, UpstreamError


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key is visible unless a test sets one; the machine's .env file is never read."""
    monkeypatch.setattr("uwcopilot.studio.data.keys.ENV_FILE", None)
    for name in ("CENSUS_API_KEY", "BLS_API_KEY", "FRED_API_KEY", "HUD_USER_TOKEN", "ORS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def qcew_text() -> str:
    return fixture_body("qcew/area_C2642_2025q2.json")


def test_qcew_area_code_and_title_check(tmp_path) -> None:
    assert wages.area_code_for_cbsa("26420") == "C2642"
    network = FakeNetwork()
    network.add_fixture(wages.AREA_TITLES_URL, "qcew/area_titles.json")
    client, _ = make_client(tmp_path, network)
    assert wages.verify_area_code(client, "C2642", "Houston-Pasadena-The Woodlands").endswith("MSA")
    with pytest.raises(UpstreamError, match="expected"):
        wages.verify_area_code(client, "C2642", "Chicago")


def test_qcew_wage_uses_disclosed_rows_and_records_the_level(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(wages.area_url(2025, 2, "C2642"), "qcew/area_C2642_2025q2.json")
    client, _ = make_client(tmp_path, network)
    fetched = client.get(wages.area_url(2025, 2, "C2642"))
    rows = wages.parse_area_rows(qcew_text())
    expected = rows["722511"]
    wage = wages.qcew_wage(qcew_text(), fetched, "C2642", "722511", 2025, 2)
    assert wage.used_naics == "722511" and wage.avg_weekly_wage == float(expected["avg_wkly_wage"])
    low, mode, high = wage.hourly_range()
    assert low < mode < high and mode == pytest.approx(wage.avg_weekly_wage / wages.HOURS_PER_WEEK["mode"])
    assert wage.source.vintage == "2025 Q2"


def test_qcew_does_not_fall_back_to_broad_industry_groups(tmp_path) -> None:
    rows = wages.parse_area_rows(qcew_text())
    assert rows["811192"]["disclosure_code"] == "N"  # recorded: car-wash wages are not disclosed for this metro
    assert wages.naics_fallbacks("811192") == ["811192", "81119"]
    assert wages.naics_fallbacks("311811", min_digits=2)[-1] == "31-33"
    network = FakeNetwork()
    network.add_fixture(wages.area_url(2025, 2, "C2642"), "qcew/area_C2642_2025q2.json")
    client, _ = make_client(tmp_path, network)
    fetched = client.get(wages.area_url(2025, 2, "C2642"))
    with pytest.raises(NotAvailable, match="811192"):
        wages.qcew_wage(qcew_text(), fetched, "C2642", "811192", 2025, 2)


def test_published_quarters_skip_missing_files(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(wages.area_url(2025, 2, "C2642"), "qcew/area_C2642_2025q2.json")
    network.add(f"{wages.QCEW_BASE}/", lambda request: httpx.Response(404, content=b"not found"))
    client, _ = make_client(tmp_path, network)
    quarters = wages.published_quarters(client, "C2642", date(2025, 9, 1))
    assert [(item.year, item.quarter) for item in quarters] == [(2025, 2)]


def test_bls_v2_needs_a_key_and_parses_series(tmp_path, monkeypatch) -> None:
    client, _ = make_client(tmp_path, FakeNetwork())
    with pytest.raises(MissingApiKey, match="BLS_API_KEY"):
        wages.fetch_bls_series(client, ["X"], 2024, 2025)
    monkeypatch.setenv("BLS_API_KEY", "BLSKEY")
    network = FakeNetwork()
    network.add_fixture(wages.BLS_V2_URL, "wages/bls_v2_handwritten.json")
    client, _ = make_client(tmp_path / "2", network)
    series, record = wages.fetch_bls_series(client, ["SYNTHETIC0001"], 2024, 2025)
    assert series["SYNTHETIC0001"][0] == ("2025", "A01", 17.25)
    assert series["SYNTHETIC0001"][1][2] is None
    assert "BLSKEY" not in record.model_dump_json()


def test_fred_keyless_csv_recorded(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(rates.CSV_URL, "fred/fredgraph_dprime.json")
    client, _ = make_client(tmp_path, network)
    observation = rates.latest_rate(client, date(2026, 9, 1))
    points = rates.parse_csv(fixture_body("fred/fredgraph_dprime.json"), "DPRIME")
    assert (observation.observation_date, observation.value_pct) == max(points)
    assert observation.source.params == {"id": "DPRIME", "cosd": "2026-09-01"}


def test_fred_api_used_when_key_is_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "FREDKEY")
    network = FakeNetwork()
    network.add_fixture(rates.API_URL, "fred/api_handwritten.json")
    client, _ = make_client(tmp_path, network)
    observation = rates.latest_rate(client, date(2026, 9, 1))
    assert observation.value_pct == 7.5 and observation.observation_date == date(2026, 10, 1)
    assert "api_key" not in observation.source.params


def test_hpms_nearest_segment_recorded(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(traffic_hpms.layer_url("TX"), "hpms/segments_near.json")
    client, _ = make_client(tmp_path, network)
    params = load_fixture("hpms/segments_near.json")["params"]
    lon, lat = (float(value) for value in params["geometry"].split(","))
    road = traffic_hpms.nearest_road(client, "TX", lat, lon, radius_miles=0.1)
    segments = traffic_hpms.parse_segments(httpx.Response(200, content=fixture_body("hpms/segments_near.json")).json())
    assert road.segment.aadt in {segment.aadt for segment in segments}
    assert road.distance_miles < 0.2 and road.source.vintage == "HPMS 2024"


def test_hpms_county_count_recorded(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(traffic_hpms.layer_url("TX"), "hpms/county_count.json")
    client, _ = make_client(tmp_path, network)
    count, _ = traffic_hpms.county_segment_count(client, "TX", "201")
    assert count > 0
    with pytest.raises(ValueError):
        traffic_hpms.layer_url("Texas")


def test_hpms_empty_answer_is_not_available() -> None:
    with pytest.raises(NotAvailable):
        traffic_hpms.nearest_segment([], 0, 0)
    with pytest.raises(UpstreamError):
        traffic_hpms.parse_segments({"error": {"code": 499}})


def test_overpass_query_is_built_from_safe_tags() -> None:
    query = osm.build_query(10.0, -140.0, 1.0, ["amenity=car_wash", "office"])
    assert 'nwr["amenity"="car_wash"](around:1609,10.000000,-140.000000);' in query
    assert 'nwr["office"](around:1609' in query and query.endswith("out center tags;")
    with pytest.raises(ValueError):
        osm.build_query(10.0, -140.0, 1.0, ['amenity=x"];out;'])


def test_overpass_parse_hand_written(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(osm.OVERPASS_URL, "osm/overpass_handwritten.json")
    client, _ = make_client(tmp_path, network)
    pois, record = osm.pois_near(client, 10.0, -140.0, 3.0, ["amenity=car_wash"])
    assert [poi.poi_id for poi in pois] == ["node/1", "way/2", "node/3"]  # nearest first; no-position relation dropped
    assert pois[0].name == "SYNTHETIC Wash A" and pois[0].distance_miles < pois[1].distance_miles
    assert record.source_id == "osm_overpass"
    assert osm.tag_matches({"office": "yes"}, "office") and not osm.tag_matches({"shop": "x"}, "shop=y")


def test_hud_needs_token_and_never_stores_it(tmp_path, monkeypatch) -> None:
    client, _ = make_client(tmp_path, FakeNetwork())
    with pytest.raises(MissingApiKey, match="HUD_USER_TOKEN"):
        hud_fmr.fetch_fmr(client, "4820199999", 2026)
    monkeypatch.setenv("HUD_USER_TOKEN", "HUDTOKEN")
    network = FakeNetwork()
    network.add_fixture(hud_fmr.BASE_URL, "hud/fmr_handwritten.json")
    client, _ = make_client(tmp_path / "2", network)
    rents = hud_fmr.fetch_fmr(client, hud_fmr.county_entity_id("48", "201"), 2026)
    assert network.requests[0].headers["authorization"] == "Bearer HUDTOKEN"
    assert rents.rents_by_bedrooms["Two-Bedroom"] == 1500 and "HUDTOKEN" not in rents.source.model_dump_json()


def test_city_licenses_request_and_parse() -> None:
    dataset = city_open_data.dataset_for("chicago")
    params = city_open_data.licenses_request(dataset, "car_wash", 10.0, -140.0, 1.0, date(2026, 10, 12))
    assert "Operation of a Hand Car Wash" in params["$where"] and "expiration_date >= '2026-10-12'" in params["$where"]
    assert "legal_name" not in params["$select"] and "doing_business_as_name" not in params["$select"]
    rows = httpx.Response(200, content=fixture_body("city_open_data/chicago_licenses_handwritten.json")).json()
    pois = city_open_data.parse_licenses(rows, dataset, 10.0, -140.0, 1.0)
    assert len(pois) == 1 and pois[0].tags["business_activity"] == "Operation of a Hand Car Wash"
    with pytest.raises(NotAvailable):
        city_open_data.licenses_request(dataset, "restaurant_cafe", 10.0, -140.0, 1.0, date(2026, 10, 12))
    with pytest.raises(NotAvailable):
        city_open_data.dataset_for("houston")


def test_city_category_values_exist_in_the_recorded_portals() -> None:
    """Every mapped category value appears in the recorded category counts of its portal."""
    recorded = {
        "chicago": fixture_body("city_open_data/chicago_categories.json"),
        "nyc": fixture_body("city_open_data/nyc_categories.json"),
    }
    for adapter, dataset in city_open_data.DATASETS.items():
        for values in dataset.categories.values():
            for _, value in values:
                assert value in recorded[adapter], (adapter, value)
        metadata = fixture_body(f"city_open_data/{adapter}_metadata.json")
        for field in (*dataset.category_fields, "latitude", "longitude", dataset.expiration_field):
            assert f'"{field}"' in metadata, (adapter, field)


def test_str_comparables_unknown_until_coverage_is_verified() -> None:
    with pytest.raises(NotAvailable, match="not verified"):
        str_comps.fetch_comparables("houston")
    listings = str_comps.parse_listings_csv(fixture_body("str/listings_handwritten.json"), 10.0, -140.0, 2.0)
    assert [item.listing_id for item in listings] == ["1001", "1002"]
    assert listings[0].nightly_price == 1250.0 and listings[1].availability_365 is None
    with pytest.raises(UpstreamError, match="missing columns"):
        str_comps.parse_listings_csv("id,latitude\n1,2\n", 0, 0, 1)


def test_state_traffic_adapters_are_not_available_yet(tmp_path) -> None:
    client, _ = make_client(tmp_path, FakeNetwork())
    for state in ("NY", "NJ", "CA", "IL", "IN", "TX"):
        with pytest.raises(NotAvailable, match="HPMS"):
            traffic_state.adapter_for(state).nearest_count(client, 0.0, 0.0)
    with pytest.raises(NotAvailable):
        traffic_state.adapter_for("ZZ")
