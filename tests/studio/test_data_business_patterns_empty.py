"""County Business Patterns: a NAICS code with no rows (HTTP 204, empty body) gives no counts instead of failing.

The 204 answer is hand-built here; its shape (status 204, empty body, JSON content type) is what api.census.gov
returned on 2026-10-13 for NAICS2017 codes "44" and "445131" in Texas (CBP 2023).
"""

import httpx
import pytest
from studio_helpers import FakeNetwork, make_client

from uwcopilot.studio.data import business_patterns
from uwcopilot.studio.data.errors import UpstreamError


@pytest.fixture(autouse=True)
def census_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uwcopilot.studio.data.keys.ENV_FILE", None)
    monkeypatch.setenv("CENSUS_API_KEY", "test-key-not-real")


def empty_answer(request: httpx.Request) -> httpx.Response:
    return httpx.Response(204, content=b"", headers={"content-type": "application/json;charset=utf-8"})


def test_no_rows_is_an_empty_count_not_a_failure(tmp_path) -> None:
    network = FakeNetwork()
    network.add("https://api.census.gov/data/2023/cbp", empty_answer)
    client, _ = make_client(tmp_path, network)
    result = business_patterns.fetch_county_counts(client, "48", "445131")
    assert result.no_rows and result.counts == {}
    assert "key" not in result.source.params and result.source.params["NAICS2017"] == "445131"


def test_an_empty_200_answer_is_still_refused(tmp_path) -> None:
    network = FakeNetwork()
    network.add("https://api.census.gov/data/2023/cbp", lambda request: httpx.Response(200, content=b""))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError):
        business_patterns.fetch_county_counts(client, "48", "811192")
