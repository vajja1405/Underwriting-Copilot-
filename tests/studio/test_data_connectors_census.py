"""Census connectors (Geocoder, ACS, Business Patterns, Gazetteer) against recorded or labeled hand-written fixtures."""

import io
import json
import zipfile

import httpx
import pytest
from studio_helpers import FakeNetwork, fixture_body, load_fixture, make_client

from uwcopilot.studio.data import business_patterns, census_acs, census_geography, geocode
from uwcopilot.studio.data.errors import MissingApiKey, NotAvailable, UpstreamError


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests decide which keys exist; the .env file of the machine is never read."""
    monkeypatch.setattr("uwcopilot.studio.data.keys.ENV_FILE", None)
    for name in ("CENSUS_API_KEY", "BLS_API_KEY", "FRED_API_KEY", "HUD_USER_TOKEN", "ORS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def with_key(monkeypatch: pytest.MonkeyPatch, name: str = "CENSUS_API_KEY", value: str = "TESTKEY") -> None:
    """Pretend a key is set (read_key's default env_file is bound at import, so patch the reader's default too)."""
    monkeypatch.setenv(name, value)


def test_geocode_recorded_address(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(f"{geocode.BASE_URL}/onelineaddress", "geocoder/onelineaddress.json")
    client, _ = make_client(tmp_path, network)
    match = geocode.geocode_address(client, "901 Bagby St, Houston, TX 77002")
    recorded = json.loads(fixture_body("geocoder/onelineaddress.json"))["result"]["addressMatches"][0]
    assert match.tract_geoid == recorded["geographies"]["Census Tracts"][0]["GEOID"]
    assert match.tract_geoid.startswith(match.state_fips + match.county_fips)
    assert match.lat == pytest.approx(recorded["coordinates"]["y"])
    assert match.source.source_id == "census_geocoder" and "Public_AR_Current" in match.source.vintage


def test_geocode_recorded_coordinates(tmp_path) -> None:
    network = FakeNetwork()
    network.add_fixture(f"{geocode.BASE_URL}/coordinates", "geocoder/coordinates.json")
    client, _ = make_client(tmp_path, network)
    params = load_fixture("geocoder/coordinates.json")["params"]
    match = geocode.tract_for_point(client, float(params["y"]), float(params["x"]))
    assert len(match.tract_geoid) == 11


def test_geocode_no_match_is_not_available() -> None:
    with pytest.raises(NotAvailable, match="no match"):
        geocode.parse_address_response({"result": {"addressMatches": []}})
    with pytest.raises(UpstreamError):
        geocode.parse_address_response({"error": "x"})


def test_acs_needs_a_key(tmp_path) -> None:
    client, _ = make_client(tmp_path, FakeNetwork())
    with pytest.raises(MissingApiKey, match="CENSUS_API_KEY"):
        census_acs.fetch_tracts(client, "48", "201")


def test_acs_tracts_from_hand_written_fixture(tmp_path, monkeypatch) -> None:
    with_key(monkeypatch)
    network = FakeNetwork()
    network.add_fixture(census_acs.dataset_url(2024), "census_acs/tracts_handwritten.json")
    client, _ = make_client(tmp_path, network)
    table = census_acs.fetch_tracts(client, "48", "201")
    assert network.requests[0].url.params["key"] == "TESTKEY"
    assert "key" not in table.source.params
    assert table.values["48201900100"]["resident_population"] == 4100
    assert table.values["48201900200"]["median_household_income"] is None  # negative "not available" code
    assert table.values["48201900300"]["median_household_income"] is None  # null


def test_acs_html_answer_is_an_error(tmp_path, monkeypatch) -> None:
    with_key(monkeypatch)
    network = FakeNetwork()
    network.add(
        census_acs.dataset_url(2024),
        lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
    )
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError, match="HTML"):
        census_acs.fetch_tracts(client, "48", "201")


def test_acs_variables_match_the_recorded_metadata(tmp_path) -> None:
    network = FakeNetwork()
    for variable, _ in census_acs.ACS_VARIABLES.values():
        network.add_fixture(census_acs.variable_request(2024, variable), f"census_acs/variable_{variable}.json")
    client, _ = make_client(tmp_path, network)
    labels = census_acs.verify_variables(client)
    assert set(labels) == {variable for variable, _ in census_acs.ACS_VARIABLES.values()}
    with pytest.raises(UpstreamError, match="now reads"):
        census_acs.check_variable_label({"name": "B01003_001E", "label": "Other"}, "B01003_001E", "Estimate!!Total")


def test_business_patterns_from_hand_written_fixture(tmp_path, monkeypatch) -> None:
    with_key(monkeypatch)
    network = FakeNetwork()
    network.add_fixture("https://api.census.gov/data/2023/cbp", "business_patterns/cbp_handwritten.json")
    client, _ = make_client(tmp_path, network)
    result = business_patterns.fetch_county_counts(client, "48", "811192")
    assert result.counts == {"48201": 37, "48039": 5}  # the null county is left out, not counted as zero
    assert result.source.vintage == "CBP 2023"


def gazetteer_zip() -> bytes:
    """Zip the recorded Gazetteer excerpt the way the Census Bureau ships the file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2024_Gaz_tracts_national.txt", fixture_body("census_geography/gazetteer_excerpt.json"))
    return buffer.getvalue()


def test_gazetteer_tracts_for_metro_counties(tmp_path) -> None:
    network = FakeNetwork()
    body = gazetteer_zip()
    network.add(census_geography.gazetteer_url(), lambda request: httpx.Response(200, content=body))
    client, _ = make_client(tmp_path, network)
    tracts, record = census_geography.fetch_metro_tracts(client, {"48201"})
    assert len(tracts) == 6
    first = next(iter(tracts.values()))
    assert first.usps == "TX" and first.geoid.startswith("48201") and first.land_area_sq_miles > 0
    assert census_geography.parse_tracts(fixture_body("census_geography/gazetteer_excerpt.json"), {"06037"}) == {}
    assert record.source_id == "census_gazetteer_tracts"
