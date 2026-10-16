"""LEHD LODES 8 WAC: jobs per tract from a recorded Illinois excerpt, checked against a published checksum.

The checksum lists served to the connector below are built in the tests from the excerpt (the recorded list holds
the checksum of the FULL Illinois file, which the excerpt cannot match); everything else is recorded.
"""

import csv
import hashlib
import io

import httpx
import pytest
from layer_helpers import fixture_record, lodes_gzip
from studio_helpers import FakeNetwork, make_client

from uwcopilot.studio.data import lodes
from uwcopilot.studio.data.errors import UpstreamError

EXCERPT = fixture_record("lodes/il_wac_excerpt.json")
CSV_TEXT = EXCERPT["body"]


def manual_tract_sums(text: str) -> dict[str, int]:
    """C000 summed by the first 11 digits of w_geocode, computed here without the connector."""
    sums: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        tract = row["w_geocode"][:11]
        sums[tract] = sums.get(tract, 0) + int(row["C000"])
    return sums


def network(checksum_text: str, wac_body: bytes, version: str | None = None) -> FakeNetwork:
    net = FakeNetwork()
    version_record = fixture_record("lodes/il_version.json")
    body = version if version is not None else version_record["body"]
    net.add(lodes.version_url("il"), lambda request: httpx.Response(200, text=body))
    net.add(lodes.checksum_url("il"), lambda request: httpx.Response(200, text=checksum_text))
    net.add(lodes.wac_url("il"), lambda request: httpx.Response(200, content=wac_body))
    return net


def test_recorded_files_have_the_documented_shapes() -> None:
    vintage, version = lodes.parse_version(fixture_record("lodes/il_version.json")["body"])
    assert vintage and version.startswith("8")
    checksums = lodes.parse_checksums(fixture_record("lodes/il_sha256sum.json")["body"])
    assert "il_wac_S000_JT00_2023.csv" in checksums and len(checksums["il_wac_S000_JT00_2023.csv"]) == 64
    assert EXCERPT["url"] == lodes.wac_url("il") and EXCERPT["fixture_origin"].startswith("excerpt of a response")
    assert CSV_TEXT.splitlines()[0].startswith("w_geocode,C000,")


def test_urls_and_names() -> None:
    assert lodes.wac_file_name("IL") == "il_wac_S000_JT00_2023.csv.gz"
    assert lodes.wac_url("tx").endswith("/LODES8/tx/wac/tx_wac_S000_JT00_2023.csv.gz")
    assert lodes.checksum_url("ny").endswith("/LODES8/ny/lodes_ny.sha256sum")
    with pytest.raises(ValueError):
        lodes.wac_file_name("Illinois")


def test_tract_sums_match_a_manual_sum_and_filter_by_county() -> None:
    body = lodes_gzip(CSV_TEXT)
    result = lodes.tract_jobs(body, {"17031"})
    assert result.tract_jobs == manual_tract_sums(CSV_TEXT)
    assert result.rows_read == result.rows_kept == len(CSV_TEXT.splitlines()) - 1
    assert result.min_jobs_in_file >= 1 and result.createdates
    assert result.uncompressed_sha256 == hashlib.sha256(CSV_TEXT.encode()).hexdigest()
    assert lodes.tract_jobs(body, {"17043"}).tract_jobs == {}


def test_bad_block_codes_and_missing_columns_are_refused() -> None:
    with pytest.raises(UpstreamError, match="block code"):
        lodes.tract_jobs(lodes_gzip("w_geocode,C000\n1703,5\n"), {"17031"})
    with pytest.raises(UpstreamError, match="missing columns"):
        lodes.tract_jobs(lodes_gzip("block,jobs\n170310101001000,5\n"), {"17031"})


def test_state_jobs_are_checked_against_the_published_checksum(tmp_path) -> None:
    sha = hashlib.sha256(CSV_TEXT.encode()).hexdigest()
    client, _ = make_client(tmp_path, network(f"{sha}  il_wac_S000_JT00_2023.csv\n", lodes_gzip(CSV_TEXT)))
    result = lodes.fetch_state_jobs(client, "il", {"17031"})
    assert result.jobs.tract_jobs == manual_tract_sums(CSV_TEXT) and result.published_sha256 == sha
    assert result.record.source_id == "lehd_lodes8_wac" and result.record.geography == "state IL"
    assert result.vintage in result.record.vintage and "2023" in result.record.vintage
    assert "LEHD Origin-Destination Employment Statistics" in result.citation and result.vintage in result.citation
    assert "terms not recorded" in result.record.license_note and len(result.support_records) == 2


def test_a_checksum_mismatch_fails(tmp_path) -> None:
    client, _ = make_client(tmp_path, network(f"{'0' * 64}  il_wac_S000_JT00_2023.csv\n", lodes_gzip(CSV_TEXT)))
    with pytest.raises(UpstreamError, match="does not match the published checksum"):
        lodes.fetch_state_jobs(client, "il", {"17031"})


def test_a_file_missing_from_the_checksum_list_fails(tmp_path) -> None:
    client, _ = make_client(tmp_path, network(f"{'0' * 64}  il_wac_S000_JT00_2022.csv\n", lodes_gzip(CSV_TEXT)))
    with pytest.raises(UpstreamError, match="is not in"):
        lodes.fetch_state_jobs(client, "il", {"17031"})


def test_html_pages_are_refused_and_never_cached(tmp_path) -> None:
    sha = hashlib.sha256(CSV_TEXT.encode()).hexdigest()
    client, _ = make_client(tmp_path, network(f"{sha}  il_wac_S000_JT00_2023.csv\n", b"<html>maintenance</html>"))
    with pytest.raises(UpstreamError, match="not a gzip file"):
        lodes.fetch_state_jobs(client, "il", {"17031"})
    client, _ = make_client(tmp_path / "v", network("", b"", version="<html>down</html>"))
    with pytest.raises(UpstreamError, match="HTML page"):
        lodes.fetch_state_jobs(client, "il", {"17031"})
