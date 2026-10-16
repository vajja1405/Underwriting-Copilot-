"""Rejected answers (a refused key, an error page, an error status in a 200 answer) are named and never cached.

Before this check, an "Invalid Key" page from api.census.gov was cached as a success under a cache key that leaves
the API key out, so a corrected key kept getting the old page. These tests pin the fix. Every answer is a mock.
"""

import logging
from datetime import UTC, datetime

import httpx
import pytest
from studio_helpers import FakeNetwork, fixture_body, make_client

from uwcopilot.studio.data import census_acs, traffic_hpms, wages
from uwcopilot.studio.data.cache import CachedResponse, ResponseCache, request_key, sha256_bytes
from uwcopilot.studio.data.errors import InvalidApiKey, UpstreamError
from uwcopilot.studio.data.snapshot import main

ACS_URL = census_acs.dataset_url(census_acs.DEFAULT_YEAR)
INVALID_PAGE = "https://api.census.gov/data/invalid_key.html"
MISSING_PAGE = "https://api.census.gov/data/missing_key.html"
INVALID_HTML = b'<html style="font-size: 14px;"><head><title>Invalid Key</title></head><body>x</body></html>'


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests decide which keys exist; the .env file of the machine is never read."""
    monkeypatch.setattr("uwcopilot.studio.data.keys.ENV_FILE", None)
    for name in ("CENSUS_API_KEY", "BLS_API_KEY", "FRED_API_KEY", "HUD_USER_TOKEN", "ORS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def census_network(good_keys: set[str]) -> FakeNetwork:
    """api.census.gov as it behaves live: a refused key redirects (302) to invalid_key.html, a good key gets data."""
    network = FakeNetwork()
    table = fixture_body("census_acs/tracts_handwritten.json").encode("utf-8")

    def data(request: httpx.Request) -> httpx.Response:
        key = request.url.params.get("key")
        if key is None:
            return httpx.Response(302, headers={"location": MISSING_PAGE})
        if key not in good_keys:
            return httpx.Response(302, headers={"location": INVALID_PAGE})
        return httpx.Response(200, content=table, headers={"content-type": "application/json;charset=utf-8"})

    network.add(ACS_URL, data)
    network.add(INVALID_PAGE, lambda request: httpx.Response(200, content=INVALID_HTML, headers=html()))
    network.add(
        MISSING_PAGE, lambda request: httpx.Response(200, content=b"<title>Missing Key</title>", headers=html())
    )
    return network


def html() -> dict[str, str]:
    return {"content-type": "text/html"}


def cache_entries(tmp_path) -> list:
    return list((tmp_path / "cache").rglob("*.json"))


def test_invalid_census_key_is_named_and_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "BADKEY123")
    client, _ = make_client(tmp_path, census_network(good_keys={"GOODKEY456"}))
    with pytest.raises(InvalidApiKey) as caught:
        census_acs.fetch_tracts(client, "48", "201")
    message = str(caught.value)
    assert caught.value.reason == "invalid_key" and "CENSUS_API_KEY" in message and "activat" in message
    assert "BADKEY123" not in message
    assert cache_entries(tmp_path) == []  # the Invalid Key page is never stored


def test_missing_key_page_is_named(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "IGNORED")
    network = FakeNetwork()
    network.add(ACS_URL, lambda request: httpx.Response(302, headers={"location": MISSING_PAGE}))
    network.add(
        MISSING_PAGE, lambda request: httpx.Response(200, content=b"<title>Missing Key</title>", headers=html())
    )
    client, _ = make_client(tmp_path, network)
    with pytest.raises(InvalidApiKey) as caught:
        census_acs.fetch_tracts(client, "48", "201")
    assert caught.value.reason == "missing_key"


def test_a_corrected_key_works_on_the_next_run(tmp_path, monkeypatch) -> None:
    network = census_network(good_keys={"GOODKEY456"})
    client, _ = make_client(tmp_path, network)
    monkeypatch.setenv("CENSUS_API_KEY", "BADKEY123")
    with pytest.raises(InvalidApiKey):
        census_acs.fetch_tracts(client, "48", "201")
    monkeypatch.setenv("CENSUS_API_KEY", "GOODKEY456")
    table = census_acs.fetch_tracts(client, "48", "201")
    assert table.values["48201900100"]["resident_population"] == 4100
    assert table.source.origin == "live"  # fetched again, not replayed from the cache
    data_requests = [request for request in network.requests if str(request.url).startswith(ACS_URL)]
    assert len(data_requests) == 2


def poisoned_entry(cache: ResponseCache) -> str:
    """An Invalid Key page stored the way older runs stored it (status 200, text/html, no final URL)."""
    url, params = census_acs.tract_request(census_acs.DEFAULT_YEAR, "48", "201")
    key = request_key("GET", url, params)
    cache.put(
        key,
        CachedResponse(
            body=INVALID_HTML,
            sha256=sha256_bytes(INVALID_HTML),
            retrieved_at=datetime(2026, 10, 13, 2, 45, tzinfo=UTC),
            url=url,
            params=params,
            status_code=200,
            content_type="text/html",
        ),
    )
    return key


def test_a_cached_invalid_key_page_is_deleted_and_fetched_again(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "GOODKEY456")
    network = census_network(good_keys={"GOODKEY456"})
    client, _ = make_client(tmp_path, network)
    key = poisoned_entry(client.cache)
    table = census_acs.fetch_tracts(client, "48", "201")
    assert table.source.origin == "live" and len(table.values) == 3
    cached = client.cache.get(key)
    assert cached is not None and cached.content_type.startswith("application/json")  # replaced by the data


def test_offline_run_reports_the_cached_key_page_and_deletes_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "GOODKEY456")
    client, _ = make_client(tmp_path, FakeNetwork(), offline=True)
    key = poisoned_entry(client.cache)
    with pytest.raises(InvalidApiKey):
        census_acs.fetch_tracts(client, "48", "201")
    assert client.cache.get(key) is None


def test_refresh_ignores_cached_answers(tmp_path) -> None:
    network = FakeNetwork()
    network.add("https://api.example.test/", lambda request: httpx.Response(200, json={"ok": 1}))
    client, _ = make_client(tmp_path, network)
    client.get("https://api.example.test/data")
    refreshing, _ = make_client(tmp_path, network, refresh=True)
    again = refreshing.get("https://api.example.test/data")
    assert not again.from_cache and len(network.requests) == 2


def test_bls_error_status_in_a_200_answer_is_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BLS_API_KEY", "BLSKEY")
    answers = iter(
        [
            {"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold reached"]},
            {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": "S1", "data": []}]}},
        ]
    )
    network = FakeNetwork()
    network.add(wages.BLS_V2_URL, lambda request: httpx.Response(200, json=next(answers)))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError, match="REQUEST_NOT_PROCESSED"):
        wages.fetch_bls_series(client, ["S1"], 2024, 2025)
    series, _ = wages.fetch_bls_series(client, ["S1"], 2024, 2025)
    assert series == {"S1": []} and len(network.requests) == 2


def test_arcgis_error_in_a_200_answer_is_not_cached(tmp_path) -> None:
    answers = iter([{"error": {"code": 400, "message": "Invalid query"}}, {"count": 12}])
    network = FakeNetwork()
    network.add(traffic_hpms.layer_url("TX"), lambda request: httpx.Response(200, json=next(answers)))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError, match="Invalid query"):
        traffic_hpms.county_segment_count(client, "TX", "201")
    count, _ = traffic_hpms.county_segment_count(client, "TX", "201")
    assert count == 12 and len(network.requests) == 2


def test_httpx_request_log_never_shows_a_key(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)  # what logging.basicConfig(level=INFO) does in an app
    network = FakeNetwork()
    network.add("https://api.example.test/", lambda request: httpx.Response(200, json={"ok": 1}))
    client, _ = make_client(tmp_path, network)
    client.get("https://api.example.test/data", {"get": "NAME", "key": "SECRET123"}, secret_params=["key"])
    httpx_lines = [record.getMessage() for record in caplog.records if record.name == "httpx"]
    assert httpx_lines and all("key=***" in line for line in httpx_lines)
    assert "SECRET123" not in caplog.text


def test_snapshot_refresh_cannot_be_combined_with_offline() -> None:
    with pytest.raises(SystemExit):
        main(["--metro", "houston_tx", "--offline", "--refresh"])
