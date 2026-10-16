"""Record the Studio test fixtures that can be recorded from live, KEYLESS hosts (run by hand; never in CI).

    uv run python tests/studio/fixtures/record_fixtures.py
    uv run python tests/studio/fixtures/record_fixtures.py --only overture,lodes,hpms_pages,industry_titles

Each fixture is a JSON file with the response body as text plus:
- fixture_origin: "recorded from the live API on <UTC date> by tests/studio/fixtures/record_fixtures.py", or, for a
  trimmed body, "excerpt of a response recorded ..." (the full body's SHA-256 is kept in full_body_sha256);
- url and params (never a key: only keyless requests are recorded here), recorded_at_utc, content_type;
- body_sha256: SHA-256 of `body` as stored, which the tests check.

Hand-written fixtures (for hosts that need a key or were unreachable) are NOT written by this script; they carry
fixture_origin "hand-written from documentation, NOT recorded from the live API - re-record when the host is
reachable". No fixture holds personal data: addresses are public buildings, and city license queries are aggregated
counts by category only.
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent
SCRIPT = "tests/studio/fixtures/record_fixtures.py"
TIMEOUT_S = 60.0
# A fixed street address used only to place a map point; no name or person is attached to it in this repo.
# (What stands at this address is not stated here: the Geocoder's answer gives only the matched address.)
PUBLIC_ADDRESS = "901 Bagby St, Houston, TX 77002"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies"
HPMS_TX = "https://geo.dot.gov/server/rest/services/Hosted/HPMS_FULL_TX_2024/FeatureServer/0/query"
QCEW_AREA = "https://data.bls.gov/cew/data/api/2025/2/area/C2642.csv"
QCEW_TITLES = "https://data.bls.gov/cew/doc/titles/area/area_titles.csv"
QCEW_KEEP_INDUSTRIES = {
    "10",
    "722511",
    "72251",
    "722513",
    "722515",
    "811192",
    "81119",
    "8111",
    "811",
    "812310",
    "81231",
    "721110",
    "72111",
    "445131",
    "44513",
    "4451",
    "445",
}
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_tracts_national.zip"
DELINEATION = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/"
    "list1_2023.xlsx"
)
PEP = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/cbsa-est2025-alldata.csv"
FIVE_CBSAS = {"35620", "31080", "16980", "19100", "26420"}
OVERTURE_BASE = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com"
OVERTURE_PREFIX = "release/2026-09-23.1/theme=places/type=place/"
# xmin, xmax, ymin, ymax: a small box in central Houston around the Gazetteer excerpt's tracts. Only places whose
# first source is licensed CC0-1.0 (AllThePlaces) are kept, and only the fields Studio reads (no names, ids,
# addresses, phones or websites). Their later source entries name Overture's own CDLA-Permissive-2.0 contributions,
# so the CDLA Permissive 2.0 text is saved next to the sample (its section 2.1 asks for that when data is shared).
OVERTURE_SAMPLE_BOX = (-95.40, -95.33, 29.73, 29.82)
OVERTURE_SAMPLE_ROWS = 60
CDLA_TEXT = "https://raw.githubusercontent.com/Community-Data-License-Agreements/Releases/main/CDLA-Permissive-2.0.txt"
LODES_BASE = "https://lehd.ces.census.gov/data/lodes/LODES8/il"
LODES_WAC = f"{LODES_BASE}/wac/il_wac_S000_JT00_2023.csv.gz"
LODES_COUNTY = "17031"
LODES_SAMPLE_ROWS = 40
INDUSTRY_TITLES = "https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv"
INDUSTRY_CODES_KEPT = {
    "10",
    "44-45",
    "333244",
    "445131",
    "531110",
    "721110",
    "721199",
    "722511",
    "722513",
    "722515",
    "311811",
    "811192",
    "812310",
}


RECORDED_PREFIX = "recorded from the live API on "


def sha256_text(text: str) -> str:
    """SHA-256 of a text's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def today() -> str:
    """Today's UTC date."""
    return datetime.now(UTC).date().isoformat()


def fetch(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """GET and fail loudly on an error status."""
    response = client.get(url, params=params)
    response.raise_for_status()
    return response


def save(
    name: str, url: str, params: dict[str, str], response: httpx.Response, body: str, excerpt_of: str = ""
) -> None:
    """Write one fixture file. excerpt_of describes how the body was trimmed (empty for a full body)."""
    origin = f"recorded from the live API on {today()} by {SCRIPT}"
    record = {
        "fixture_origin": f"excerpt of a response {origin}; {excerpt_of}" if excerpt_of else origin,
        "url": url,
        "params": params,
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "content_type": response.headers.get("content-type", ""),
        "body_sha256": sha256_text(body),
        "full_body_sha256": hashlib.sha256(response.content).hexdigest(),
        "body": body,
    }
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(FIXTURES)}")


def csv_excerpt(text: str, keep: Callable[[dict[str, str]], bool]) -> str:
    """The header plus the rows for which keep(row) is true, re-serialized with the same columns."""
    reader = csv.DictReader(io.StringIO(text))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames or [], lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in reader:
        if keep(row):
            writer.writerow(row)
    return out.getvalue()


def record_geocoder(client: httpx.Client) -> None:
    """Address lookup and coordinates lookup."""
    params = {
        "address": PUBLIC_ADDRESS,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Census Tracts",
        "format": "json",
    }
    response = fetch(client, f"{GEOCODER}/onelineaddress", params)
    save("geocoder/onelineaddress.json", f"{GEOCODER}/onelineaddress", params, response, response.text)
    match = response.json()["result"]["addressMatches"][0]["coordinates"]
    params = {
        "x": f"{match['x']:.6f}",
        "y": f"{match['y']:.6f}",
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Census Tracts",
        "format": "json",
    }
    response = fetch(client, f"{GEOCODER}/coordinates", params)
    save("geocoder/coordinates.json", f"{GEOCODER}/coordinates", params, response, response.text)


def record_fred(client: httpx.Client) -> None:
    """A short window of the keyless DPRIME CSV."""
    params = {"id": "DPRIME", "cosd": "2026-09-01"}
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    response = fetch(client, url, params)
    save("fred/fredgraph_dprime.json", url, params, response, response.text)


def record_hpms(client: httpx.Client) -> None:
    """Segments near the public building, and a county count (Harris County, TX)."""
    params = {
        "geometry": "-95.369483,29.760724",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": "161",
        "units": "esriSRUnit_Meter",
        "where": "aadt > 0",
        "outFields": "route_id,routename,aadt,f_system,data_year,begin_point,end_point",
        "returnGeometry": "true",
        "outSR": "4326",
        "geometryPrecision": "6",
        "resultRecordCount": "50",
        "f": "geojson",
    }
    response = fetch(client, HPMS_TX, params)
    save("hpms/segments_near.json", HPMS_TX, params, response, response.text)
    params = {"where": "county_id = 201 AND aadt > 0", "returnCountOnly": "true", "f": "json"}
    response = fetch(client, HPMS_TX, params)
    save("hpms/county_count.json", HPMS_TX, params, response, response.text)


def record_acs_variables(client: httpx.Client) -> None:
    """The keyless metadata of every ACS variable the connector uses."""
    for variable in ("B01003_001E", "B11001_001E", "B25003_003E", "B19013_001E"):
        url = f"https://api.census.gov/data/2024/acs/acs5/variables/{variable}.json"
        response = fetch(client, url)
        save(f"census_acs/variable_{variable}.json", url, {}, response, response.text)


def record_qcew(client: httpx.Client) -> None:
    """Excerpts of the Houston-area QCEW slice and of the area titles file."""
    response = fetch(client, QCEW_AREA)
    body = csv_excerpt(response.text, lambda row: row["industry_code"] in QCEW_KEEP_INDUSTRIES)
    save(
        "qcew/area_C2642_2025q2.json",
        QCEW_AREA,
        {},
        response,
        body,
        f"rows kept: industry_code in {sorted(QCEW_KEEP_INDUSTRIES)} (all ownerships)",
    )
    response = fetch(client, QCEW_TITLES)
    wanted = {"C" + code[:4] for code in FIVE_CBSAS}
    body = csv_excerpt(response.text, lambda row: row["area_fips"] in wanted)
    save("qcew/area_titles.json", QCEW_TITLES, {}, response, body, f"rows kept: area_fips in {sorted(wanted)}")


def record_gazetteer(client: httpx.Client) -> None:
    """The header and the first rows of Harris County, TX from the national tract Gazetteer (unzipped text)."""
    response = fetch(client, GAZETTEER)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    lines = text.splitlines()
    kept = [lines[0]] + [line for line in lines[1:] if line.split("\t")[1].startswith("48201")][:6]
    save(
        "census_geography/gazetteer_excerpt.json",
        GAZETTEER,
        {},
        response,
        "\n".join(kept) + "\n",
        "the unzipped text file's header and the first 6 rows with GEOID starting 48201",
    )


def record_delineation(client: httpx.Client) -> None:
    """The delineation rows of the five v1 metros (read with the project's own xlsx reader)."""
    sys.path.insert(0, str(FIXTURES.parents[2] / "src"))
    from uwcopilot.studio.metros.delineation import read_xlsx_rows

    response = fetch(client, DELINEATION)
    with tempfile.TemporaryDirectory() as folder:
        workbook = Path(folder) / "list1_2023.xlsx"
        workbook.write_bytes(response.content)
        rows = read_xlsx_rows(workbook)
    header_index = next(i for i, row in enumerate(rows) if "CBSA Code" in row)
    kept = rows[: header_index + 1] + [row for row in rows[header_index + 1 :] if row and row[0] in FIVE_CBSAS]
    kept += [row for row in rows if row and row[0].startswith(("Note:", "Source:"))]
    save(
        "delineation/list1_2023_rows.json",
        DELINEATION,
        {},
        response,
        json.dumps(kept, indent=1),
        "the sheet as rows of strings: title rows, header, the rows of the five v1 CBSAs and the note rows",
    )


def record_pep(client: httpx.Client) -> None:
    """The ranking file's header and its CBSA-total rows for the ten largest metros plus a few others."""
    response = fetch(client, PEP)
    text = response.content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    totals = [row for row in reader if row["LSAD"] == "Metropolitan Statistical Area" and not row["MDIV"]]
    totals.sort(key=lambda row: int(row["POPESTIMATE2025"]), reverse=True)
    keep = {row["CBSA"] for row in totals[:10]}
    body = csv_excerpt(text, lambda row: row["CBSA"] in keep and (not row["STCOU"] or row["CBSA"] == "26420"))
    save(
        "ranking/cbsa_est2025_excerpt.json",
        PEP,
        {},
        response,
        body,
        "rows kept: every row of the ten largest metropolitan CBSAs except county rows (Houston county rows kept)",
    )


def record_city_categories(client: httpx.Client) -> None:
    """Aggregated license counts by category (no names, no addresses) and dataset metadata excerpts."""
    chicago = "https://data.cityofchicago.org/resource/r5kz-chrr.json"
    params = {
        "$select": "license_description, business_activity, count(*) AS n",
        "$where": "upper(business_activity) like '%LAUNDROMAT%' OR upper(business_activity) like '%CAR WASH%' "
        "OR license_description in ('Hotel', 'Shared Housing Unit Operator', 'Vacation Rental')",
        "$group": "license_description, business_activity",
        "$order": "n DESC",
        "$limit": "25",
    }
    response = fetch(client, chicago, params)
    save("city_open_data/chicago_categories.json", chicago, params, response, response.text)
    nyc = "https://data.cityofnewyork.us/resource/w7w3-xahh.json"
    params = {
        "$select": "business_category, count(*) AS n",
        "$group": "business_category",
        "$order": "n DESC",
        "$limit": "40",
    }
    response = fetch(client, nyc, params)
    save("city_open_data/nyc_categories.json", nyc, params, response, response.text)
    for name, url in (
        ("chicago", "https://data.cityofchicago.org/api/views/r5kz-chrr.json"),
        ("nyc", "https://data.cityofnewyork.us/api/views/w7w3-xahh.json"),
    ):
        response = fetch(client, url)
        data = response.json()
        excerpt = {
            "id": data.get("id"),
            "name": data.get("name"),
            "attribution": data.get("attribution"),
            "license": data.get("license"),
            "columns": [column["fieldName"] for column in data["columns"]],
        }
        save(
            f"city_open_data/{name}_metadata.json",
            url,
            {},
            response,
            json.dumps(excerpt, indent=1),
            "fields kept: id, name, attribution, license, column field names",
        )


def record_overture(client: httpx.Client) -> None:
    """The release's places listing, and a tiny CC0 sample of places (Studio-read fields only) as Parquet."""
    sys.path.insert(0, str(FIXTURES.parents[2] / "src"))
    from uwcopilot.studio.data import duckdb_http

    params = {"list-type": "2", "prefix": OVERTURE_PREFIX}
    response = fetch(client, f"{OVERTURE_BASE}/", params)
    save("overture/listing.json", f"{OVERTURE_BASE}/", params, response, response.text)
    keys = [key for key in re.findall(r"<Key>([^<]+)</Key>", response.text) if key.endswith(".parquet")]
    urls = [f"{OVERTURE_BASE}/{key}" for key in keys]
    target = FIXTURES / "overture" / "places_sample_cc0.parquet"
    query = (
        "SELECT bbox, {'primary': taxonomy.primary, 'hierarchy': taxonomy.hierarchy} AS taxonomy, confidence, "
        "operating_status, list_transform(sources, s -> {'dataset': s.dataset, 'license': s.license}) AS sources "
        "FROM read_parquet(?) WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? "
        "AND sources[1].license = 'CC0-1.0' ORDER BY bbox.ymin, bbox.xmin, taxonomy.primary LIMIT ?"
    )
    connection = duckdb_http.connect(https=True)
    try:
        connection.execute(f"CREATE TABLE sample AS {query}", [urls, *OVERTURE_SAMPLE_BOX, OVERTURE_SAMPLE_ROWS])
        rows = connection.execute("SELECT * FROM sample").fetchall()
        columns = [item[0] for item in connection.description]
        connection.execute(f"COPY sample TO '{target.as_posix()}' (FORMAT parquet)")
    finally:
        connection.close()
    body = json.dumps({"columns": columns, "rows": rows}, indent=1, default=str)
    record = {
        "fixture_origin": (
            f"{RECORDED_PREFIX}{today()} by {SCRIPT}: a DuckDB query (below) of the Overture release files over "
            "HTTPS; the same rows are in places_sample_cc0.parquet (body = the rows as JSON, for reading)"
        ),
        "url": f"{OVERTURE_BASE}/{OVERTURE_PREFIX}",
        "params": {
            "query": query,
            "bbox_xmin_xmax_ymin_ymax": ",".join(str(value) for value in OVERTURE_SAMPLE_BOX),
            "limit": str(OVERTURE_SAMPLE_ROWS),
            "files_read": str(len(urls)),
        },
        "release": "2026-09-23.1",
        "license_note": (
            "every kept row's first source is licensed CC0-1.0 (AllThePlaces), per the row itself; the rows' later "
            "source entries are Overture contributions licensed CDLA-Permissive-2.0, so the CDLA Permissive 2.0 "
            "text (downloaded from cdla_text_url) is kept next to this file, as its section 2.1 asks"
        ),
        "cdla_text_url": CDLA_TEXT,
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "content_type": "application/json (rows) and application/vnd.apache.parquet (file)",
        "body_sha256": sha256_text(body),
        "file": target.name,
        "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "body": body,
    }
    (FIXTURES / "overture" / "places_sample_cc0.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    response = fetch(client, CDLA_TEXT)
    (FIXTURES / "overture" / "CDLA-Permissive-2.0.txt").write_bytes(response.content)
    print(f"wrote overture/{target.name} ({len(rows)} rows) and overture/CDLA-Permissive-2.0.txt")


def record_lodes(client: httpx.Client) -> None:
    """Illinois LODES files: version.txt, the WAC lines of the checksum list, and the first Cook County WAC rows."""
    response = fetch(client, f"{LODES_BASE}/version.txt")
    save("lodes/il_version.json", f"{LODES_BASE}/version.txt", {}, response, response.text)
    url = f"{LODES_BASE}/lodes_il.sha256sum"
    response = fetch(client, url)
    lines = [line for line in response.text.splitlines() if "_wac_S000_JT00_" in line]
    save("lodes/il_sha256sum.json", url, {}, response, "\n".join(lines) + "\n", "lines kept: *_wac_S000_JT00_*")
    response = fetch(client, LODES_WAC)
    text = gzip.decompress(response.content).decode("utf-8")
    rows = text.splitlines()
    kept = [rows[0]] + [line for line in rows[1:] if line.startswith(LODES_COUNTY)][:LODES_SAMPLE_ROWS]
    save(
        "lodes/il_wac_excerpt.json",
        LODES_WAC,
        {},
        response,
        "\n".join(kept) + "\n",
        f"the decompressed CSV's header and its first {LODES_SAMPLE_ROWS} rows with w_geocode starting {LODES_COUNTY}",
    )


def record_hpms_pages(client: httpx.Client) -> None:
    """The first two 5-segment pages of Harris County's segments, with the connector's own county query."""
    sys.path.insert(0, str(FIXTURES.parents[2] / "src"))
    from uwcopilot.studio.data.traffic_hpms import county_segments_request

    for page in (0, 1):
        params = county_segments_request("201", page * 5, 5)
        response = fetch(client, HPMS_TX, params)
        save(f"hpms/county_201_page{page}.json", HPMS_TX, params, response, response.text)


def record_industry_titles(client: httpx.Client) -> None:
    """The BLS QCEW industry titles rows of the profile codes, plus a few rows that show each title form."""
    response = fetch(client, INDUSTRY_TITLES)
    body = csv_excerpt(response.text, lambda row: row["industry_code"] in INDUSTRY_CODES_KEPT)
    save(
        "bls/industry_titles_excerpt.json",
        INDUSTRY_TITLES,
        {},
        response,
        body,
        f"rows kept: industry_code in {sorted(INDUSTRY_CODES_KEPT)}",
    )


STEPS: dict[str, Callable[[httpx.Client], None]] = {
    "geocoder": record_geocoder,
    "fred": record_fred,
    "hpms": record_hpms,
    "acs_variables": record_acs_variables,
    "qcew": record_qcew,
    "gazetteer": record_gazetteer,
    "delineation": record_delineation,
    "pep": record_pep,
    "city_categories": record_city_categories,
    "overture": record_overture,
    "lodes": record_lodes,
    "hpms_pages": record_hpms_pages,
    "industry_titles": record_industry_titles,
}


def main(argv: list[str] | None = None) -> int:
    """Record every keyless fixture, or only the steps named with --only (comma-separated)."""
    parser = argparse.ArgumentParser(description="Record the Studio test fixtures from keyless live hosts.")
    parser.add_argument("--only", default="", help=f"comma-separated steps out of: {', '.join(STEPS)}")
    args = parser.parse_args(argv)
    chosen = [name.strip() for name in args.only.split(",") if name.strip()] or list(STEPS)
    unknown = sorted(set(chosen) - set(STEPS))
    if unknown:
        parser.error(f"unknown steps {unknown}")
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, trust_env=True) as client:
        for name in chosen:
            STEPS[name](client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
