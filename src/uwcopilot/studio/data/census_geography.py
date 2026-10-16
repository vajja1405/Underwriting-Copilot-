"""Census Gazetteer tract file: every census tract's GEOID, land area and internal point (a centroid). No key.

The Gazetteer files are published by the Census Bureau at
https://www2.census.gov/geo/docs/maps-data/data/gazetteer/<year>_Gazetteer/<year>_Gaz_tracts_national.zip as one
tab-separated text file (columns USPS, GEOID, ALAND, AWATER, ALAND_SQMI, AWATER_SQMI, INTPTLAT, INTPTLONG). The 2024
file describes the 2020 census tracts, the same tracts as the ACS 5-year tables of 2020-2024. Terms to verify at
https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html.

Studio uses it as the tract layer of a real metro: the tract list of the metro's counties and a point per tract.
Its USPS column also gives each state's postal code for its state FIPS code (the first two GEOID digits), which the
HPMS connector needs to pick a state's layer.
"""

import csv
import io
import zipfile
from dataclasses import dataclass

from uwcopilot.studio.data.errors import UpstreamError
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

DEFAULT_YEAR = 2024
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
REQUIRED_COLUMNS: tuple[str, ...] = ("USPS", "GEOID", "ALAND_SQMI", "INTPTLAT", "INTPTLONG")
SOURCE = SourceInfo(
    source_id="census_gazetteer_tracts",
    publisher="U.S. Census Bureau",
    dataset="Census Gazetteer file, census tracts (national)",
    license_note="Terms to verify at https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"
    " (not yet recorded)",
)


@dataclass(frozen=True)
class TractPoint:
    """One tract's id, state postal code, land area and internal point."""

    geoid: str
    usps: str
    lat: float
    lon: float
    land_area_sq_miles: float


def gazetteer_url(year: int = DEFAULT_YEAR) -> str:
    """URL of the national tract Gazetteer zip for a year."""
    return f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{year}_Gazetteer/{year}_Gaz_tracts_national.zip"


def read_gazetteer_text(body: bytes) -> str:
    """The text file inside the zip (exactly one .txt member, size-capped)."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = [info for info in archive.infolist() if info.filename.endswith(".txt")]
        if len(members) != 1:
            raise UpstreamError("www2.census.gov", f"expected one .txt file in the Gazetteer zip, found {len(members)}")
        if members[0].file_size > MAX_UNZIPPED_BYTES:
            raise UpstreamError("www2.census.gov", "the Gazetteer text file is larger than expected")
        return archive.read(members[0]).decode("utf-8-sig", errors="replace")


def parse_tracts(text: str, county_geoids: set[str]) -> dict[str, TractPoint]:
    """{GEOID: TractPoint} for tracts whose first five digits are one of county_geoids."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    header = [name.strip() for name in reader.fieldnames or []]
    reader.fieldnames = header
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise UpstreamError("www2.census.gov", f"Gazetteer file is missing columns {missing}")
    tracts: dict[str, TractPoint] = {}
    for row in reader:
        geoid = row["GEOID"].strip()
        if geoid[:5] in county_geoids:
            tracts[geoid] = TractPoint(
                geoid=geoid,
                usps=row["USPS"].strip(),
                lat=float(row["INTPTLAT"]),
                lon=float(row["INTPTLONG"].strip()),
                land_area_sq_miles=float(row["ALAND_SQMI"]),
            )
    return tracts


def fetch_metro_tracts(
    client: PoliteClient, county_geoids: set[str], year: int = DEFAULT_YEAR
) -> tuple[dict[str, TractPoint], SourceRecord]:
    """Every tract of the given counties, with the provenance of the national file."""
    fetched = client.get(gazetteer_url(year))
    tracts = parse_tracts(read_gazetteer_text(fetched.body), county_geoids)
    record = SOURCE.record(fetched, vintage=f"{year} Gazetteer (2020 census tracts)", geography="United States")
    return tracts, record
