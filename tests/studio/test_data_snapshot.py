"""`make studio-data`: every step runs, failures name the host or key, and the snapshot keeps what succeeded.

The Houston network below answers from recorded fixtures, except three hand-built answers named where they are made:
the HPMS county count (set to the number of recorded segments served, so the download is complete), and the Texas
LODES checksum list and WAC file (a three-row CSV for the Gazetteer excerpt's tracts, made in this test).
"""

import hashlib
import io
import json
import zipfile
from datetime import date

import httpx
import pytest
from layer_helpers import SampleReader, lodes_gzip
from studio_helpers import FakeNetwork, fixture_body, load_fixture, make_client

from uwcopilot.studio.data import census_geography, lodes, rates, traffic_hpms, wages
from uwcopilot.studio.data.fabric import SnapshotFabric, latest_snapshot
from uwcopilot.studio.data.osm import OVERPASS_URL
from uwcopilot.studio.data.overture_config import load_settings
from uwcopilot.studio.data.snapshot import build_snapshot, main, write_snapshot
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.profiles import load_all_profiles


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uwcopilot.studio.data.keys.ENV_FILE", None)
    for name in ("CENSUS_API_KEY", "BLS_API_KEY", "FRED_API_KEY", "HUD_USER_TOKEN", "ORS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def houston_network() -> FakeNetwork:
    """Fixtures for every keyless host; Overpass resets the connection like it did from the build environment."""
    network = FakeNetwork()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2024_Gaz_tracts_national.txt", fixture_body("census_geography/gazetteer_excerpt.json"))
    body = buffer.getvalue()
    network.add(census_geography.gazetteer_url(), lambda request: httpx.Response(200, content=body))
    network.add_fixture(wages.AREA_TITLES_URL, "qcew/area_titles.json")
    network.add_fixture(wages.area_url(2025, 2, "C2642"), "qcew/area_C2642_2025q2.json")
    network.add(f"{wages.QCEW_BASE}/", lambda request: httpx.Response(404))
    network.add_fixture(rates.CSV_URL, "fred/fredgraph_dprime.json")
    recorded = [json.loads(load_fixture(f"hpms/county_201_page{page}.json")["body"]) for page in (0, 1)]
    one_page = {"type": "FeatureCollection", "features": recorded[0]["features"] + recorded[1]["features"]}

    def hpms(request: httpx.Request) -> httpx.Response:
        """Every county answers with the 10 recorded segments as one complete page, then an empty page."""
        if request.url.params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": 10})  # hand-built: the number of recorded segments served
        if request.url.params["resultOffset"] == "0":
            return httpx.Response(200, json=one_page)
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    network.add(traffic_hpms.layer_url("TX"), hpms)
    network.add_fixture(f"{load_settings().release.s3_https_base}/", "overture/listing.json")
    network.add(
        lodes.version_url("tx"), lambda request: httpx.Response(200, text=fixture_body("lodes/il_version.json"))
    )
    csv_text = (  # hand-built: three blocks of the Gazetteer excerpt's tracts
        "w_geocode,C000,createdate\n482011000011000,25,20251202\n482011000012001,5,20251202\n"
        "482012104001000,40,20251202\n"
    )
    checksums = f"{hashlib.sha256(csv_text.encode()).hexdigest()}  tx_wac_S000_JT00_2023.csv\n"
    network.add(lodes.checksum_url("tx"), lambda request: httpx.Response(200, text=checksums))
    network.add(lodes.wac_url("tx"), lambda request: httpx.Response(200, content=lodes_gzip(csv_text)))

    def reset(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection reset by peer", request=request)

    network.add(OVERPASS_URL.replace("/interpreter", "/status"), reset)
    return network


def test_houston_snapshot_with_keyless_sources_only(tmp_path) -> None:
    network = houston_network()
    client, _ = make_client(tmp_path, network)
    build = build_snapshot(
        load_metro("houston_tx"), client, load_all_profiles(), date(2025, 9, 1), places_reader=SampleReader()
    )
    status = {step.name: step.status for step in build.steps}
    assert status["tract_geography"] == "ok" and len(build.tracts) == 6
    assert status["acs_tracts"] == "failed" and status["business_patterns"] == "failed"
    assert status["qcew_wages"] == "ok" and "restaurant_cafe" in build.metro_inputs["local_wages"]
    assert status["prime_rate"] == "ok" and build.metro_inputs["prime_rate"]["status"] == "DATA"
    assert status["hpms_roads"] == "ok" and build.layers["roads"]["counties_with_aadt"] == 10
    assert status["roads_by_tract"] == "ok" and build.layers["road_layer"]["segments"] == 100  # 10 per county
    assert status["overture_places"] == "ok" and build.layers["places"]["release"] == "2026-09-23.1"
    assert status["lehd_daytime_workers"] == "ok"
    assert build.tracts["48201100001"]["daytime_workers"] == 30 and build.tracts["48201210400"]["daytime_workers"] == 40
    assert "osm_pois" not in status  # Overpass is optional (--with-overpass)
    assert status["enhancement:traffic_state:TX"] == "not_available"
    failures = "\n".join(build.failures())
    assert "CENSUS_API_KEY" in failures and "overpass-api.de" not in failures
    assert set(build.files) == {PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE}
    wage = build.metro_inputs["local_wages"]["restaurant_cafe"]
    assert wage["assumptions"] and "hours per week" in wage["assumptions"][0]
    # The weekly wage is data; the hourly value and its range come from the hours assumption (rule D).
    assert wage["status"] == "DATA_X_ASSUMPTION"
    assert "DATA: BLS QCEW" in wage["reason"] and "x ASSUMPTION" in wage["reason"]

    write_snapshot(build, tmp_path / "snapshots")
    fabric = SnapshotFabric(
        metro=load_metro("houston_tx"), snapshot=latest_snapshot("houston_tx", tmp_path / "snapshots")
    )
    assert len(fabric.tract_ids()) == 6
    assert fabric.metro_inputs(load_all_profiles()["restaurant_cafe"])["local_wages"].status == "DATA_X_ASSUMPTION"
    assert fabric.mode()[0] == "limited"
    manifest = fabric.snapshot.manifest
    assert set(manifest["files"]) == {PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE}
    assert fabric.places()[0] is not None and fabric.roads()[0] is not None and fabric.point_layers_local
    workers = fabric.tract_features("48201100001")["daytime_workers"]
    assert workers.status == "DATA" and workers.source.source_id == "lehd_lodes8_wac"
    assert fabric.tract_features("48201210500")["daytime_workers"].status == "UNKNOWN"


def test_overpass_check_runs_only_when_asked(tmp_path) -> None:
    client, _ = make_client(tmp_path, houston_network())
    build = build_snapshot(
        load_metro("houston_tx"),
        client,
        load_all_profiles(),
        date(2025, 9, 1),
        places_reader=SampleReader(),
        include_overpass=True,
    )
    status = {step.name: step.status for step in build.steps}
    assert status["osm_pois"] == "failed" and "overpass-api.de" in "\n".join(build.failures())


def test_a_rerun_without_layers_removes_the_old_layer_files(tmp_path) -> None:
    client, _ = make_client(tmp_path, houston_network())
    profiles = load_all_profiles()
    build = build_snapshot(load_metro("houston_tx"), client, profiles, date(2025, 9, 1), places_reader=SampleReader())
    folder = write_snapshot(build, tmp_path / "snapshots")
    assert (folder / ROADS_FILE).is_file()
    build.files = {}
    write_snapshot(build, tmp_path / "snapshots")
    assert not (folder / ROADS_FILE).exists() and not (folder / PLACES_FILE).exists()


def test_synthetic_metro_has_nothing_to_download(capsys) -> None:
    assert main(["--metro", "synthetic_metro"]) == 0
    assert "nothing to download" in capsys.readouterr().out


def test_unverified_metro_is_refused(tmp_path) -> None:
    metro = load_metro("houston_tx").model_copy(update={"verified": False, "counties": [], "delineation": None})
    client, _ = make_client(tmp_path, FakeNetwork())
    with pytest.raises(ValueError, match="studio_fetch_delineation"):
        build_snapshot(metro, client, load_all_profiles(), date(2026, 10, 12))
