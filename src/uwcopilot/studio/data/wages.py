"""Local wages: BLS QCEW average weekly wages by metro and industry (keyless), and BLS API v2 series (keyed).

1. QCEW open data (no key): one CSV per area and quarter,
   https://data.bls.gov/cew/data/api/<year>/<quarter>/area/<area code>.csv. A metro's area code is "C" plus the
   first four digits of its CBSA code; the code and title are checked against the QCEW area-titles file
   (area_titles.csv) before use. Rows are filtered to private ownership (own_code 5, "Private" in the QCEW
   ownership-titles file). Small cells are not disclosed (disclosure_code "N"); then the next broader NAICS level is
   tried, but never broader than MIN_NAICS_DIGITS digits (a 3-digit group such as "repair and maintenance" mixes in
   other industries, so it would misstate a car wash's wages). The level used is recorded. A quarter whose rows are
   not yet published (disclosure_code "-") is skipped.
2. BLS Public Data API v2 (needs BLS_API_KEY): a POST with series ids, for example OEWS hourly wages by occupation.
   The series ids must be taken from the BLS series-id documentation; this module does not build them.

QCEW reports WEEKLY wages for all employees (full and part time); it has no hours. Converting to an hourly wage needs
an hours-per-week assumption, labeled ASSUMPTION (HOURS_PER_WEEK) and shown next to the result.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.http import FetchResult, PoliteClient, expect_json
from uwcopilot.studio.data.keys import require_key
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

BLS_HOST = "api.bls.gov"
QCEW_HOST = "data.bls.gov"
QCEW_BASE = "https://data.bls.gov/cew/data/api"
AREA_TITLES_URL = "https://data.bls.gov/cew/doc/titles/area/area_titles.csv"
BLS_V2_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
PRIVATE_OWNERSHIP = "5"
# Design choice: accept a parent NAICS level down to 5 digits (e.g. 72251 for 722511), no broader.
MIN_NAICS_DIGITS = 5
ALL_INDUSTRIES = "10"
# QCEW codes for NAICS sectors that span several two-digit codes (from the QCEW industry-titles file).
SECTOR_RANGES: dict[str, str] = {
    "31": "31-33",
    "32": "31-33",
    "33": "31-33",
    "44": "44-45",
    "45": "44-45",
    "48": "48-49",
    "49": "48-49",
}
# ASSUMPTION (project author): paid hours per week behind a QCEW weekly wage. QCEW has no hours; replace with BLS
# OEWS hourly wages for the occupation once BLS_API_KEY is set.
HOURS_PER_WEEK: dict[str, Any] = {
    "low": 25.0,
    "mode": 35.0,
    "high": 40.0,
    "unit": "paid hours per week",
    "status": "ASSUMPTION",
    "note": "QCEW reports weekly wages without hours; replace with BLS OEWS hourly mean wages for the occupation.",
}
QCEW_SOURCE = SourceInfo(
    source_id="bls_qcew",
    publisher="U.S. Bureau of Labor Statistics",
    dataset="Quarterly Census of Employment and Wages (QCEW) open data, area slice",
    license_note="Terms to verify at https://www.bls.gov/cew/ (not yet recorded)",
)
BLS_V2_SOURCE = SourceInfo(
    source_id="bls_api_v2",
    publisher="U.S. Bureau of Labor Statistics",
    dataset="BLS Public Data API v2 time series",
    license_note="Terms to verify at https://www.bls.gov/developers/ (not yet recorded)",
)


@dataclass(frozen=True)
class QcewWage:
    """One disclosed QCEW row: private-sector average weekly wage for an industry in a metro and quarter."""

    area_code: str
    requested_naics: str
    used_naics: str
    year: int
    quarter: int
    avg_weekly_wage: float
    employment: int
    establishments: int
    source: SourceRecord

    def hourly_range(self) -> tuple[float, float, float]:
        """(low, mode, high) USD/hour = weekly wage / the HOURS_PER_WEEK assumption (more hours -> lower rate)."""
        return (
            self.avg_weekly_wage / HOURS_PER_WEEK["high"],
            self.avg_weekly_wage / HOURS_PER_WEEK["mode"],
            self.avg_weekly_wage / HOURS_PER_WEEK["low"],
        )


def area_code_for_cbsa(cbsa_code: str) -> str:
    """QCEW metro area code: 'C' + the first four digits of the 5-digit CBSA code (checked by verify_area_code)."""
    if len(cbsa_code) != 5 or not cbsa_code.isdigit():
        raise ValueError(f"CBSA codes have 5 digits (got {cbsa_code!r})")
    return "C" + cbsa_code[:4]


def check_qcew_csv(fetched: FetchResult) -> None:
    """Accept a QCEW file only when it is a CSV whose header has area_fips (an http.Accept check)."""
    if "html" in fetched.content_type.lower() or "area_fips" not in fetched.body[:500].decode("utf-8", "replace"):
        raise UpstreamError(QCEW_HOST, "answer is not a QCEW CSV file (no area_fips column)")


def verify_area_code(client: PoliteClient, area_code: str, expected_title_start: str) -> str:
    """The QCEW title of an area code; raises UpstreamError when it is missing or names another place."""
    fetched = client.get(AREA_TITLES_URL, accept=check_qcew_csv)
    for row in csv.DictReader(io.StringIO(fetched.text())):
        if row.get("area_fips") == area_code:
            title = row.get("area_title", "")
            if not title.startswith(expected_title_start):
                raise UpstreamError("data.bls.gov", f"area {area_code} is {title!r}, expected {expected_title_start!r}")
            return title
    raise UpstreamError("data.bls.gov", f"area code {area_code} is not in the QCEW area titles file")


def area_url(year: int, quarter: int, area_code: str) -> str:
    """URL of one QCEW area slice."""
    return f"{QCEW_BASE}/{year}/{quarter}/area/{area_code}.csv"


def naics_fallbacks(naics: str, min_digits: int = MIN_NAICS_DIGITS) -> list[str]:
    """The code and its broader levels down to min_digits: 722511 -> 722511, 72251 (with the default of 5).

    With min_digits=2 the sector is last, in QCEW's range form for multi-code sectors (31 -> 31-33).
    """
    levels = [naics[:length] for length in range(len(naics), max(min_digits, 3) - 1, -1)]
    if min_digits <= 2:
        sector = naics[:2]
        levels.append(SECTOR_RANGES.get(sector, sector))
    return levels


def parse_area_rows(text: str) -> dict[str, dict[str, str]]:
    """{industry_code: row} for private-ownership rows of one area slice."""
    rows: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("own_code") == PRIVATE_OWNERSHIP:
            rows[row["industry_code"]] = row
    return rows


def is_disclosed(row: dict[str, str]) -> bool:
    """True when the row's wage is published (disclosure_code empty and a positive weekly wage)."""
    return row.get("disclosure_code", "") == "" and float(row.get("avg_wkly_wage") or 0) > 0


def pick_wage_row(rows: dict[str, dict[str, str]], naics: str) -> dict[str, str] | None:
    """The most specific disclosed row for a NAICS code, or None when no level is disclosed."""
    for code in naics_fallbacks(naics):
        row = rows.get(code)
        if row is not None and is_disclosed(row):
            return row
    return None


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    """The quarter before (year, quarter)."""
    return (year, quarter - 1) if quarter > 1 else (year - 1, 4)


@dataclass(frozen=True)
class QcewQuarter:
    """One published quarter of an area slice."""

    year: int
    quarter: int
    text: str
    fetched: FetchResult


def published_quarters(
    client: PoliteClient, area_code: str, today: date, wanted: int = 4, max_back: int = 10
) -> list[QcewQuarter]:
    """Up to `wanted` newest quarters whose private all-industries row is published, newest first."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    found: list[QcewQuarter] = []
    for _ in range(max_back):
        try:
            fetched = client.get(area_url(year, quarter, area_code), accept=check_qcew_csv)
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
        else:
            text = fetched.text()
            total = parse_area_rows(text).get(ALL_INDUSTRIES)
            if total is not None and is_disclosed(total):
                found.append(QcewQuarter(year, quarter, text, fetched))
                if len(found) == wanted:
                    break
        year, quarter = previous_quarter(year, quarter)
    if not found:
        raise NotAvailable(f"no published QCEW quarter found for {area_code} in the last {max_back} quarters")
    return found


def qcew_wage(text: str, fetched: FetchResult, area_code: str, naics: str, year: int, quarter: int) -> QcewWage:
    """The QCEW wage for one NAICS code from an already-fetched area slice; NotAvailable when nothing is disclosed."""
    row = pick_wage_row(parse_area_rows(text), naics)
    if row is None:
        raise NotAvailable(f"QCEW does not disclose private wages for NAICS {naics} or its parents in {area_code}")
    record = QCEW_SOURCE.record(fetched, vintage=f"{year} Q{quarter}", geography=area_code)
    return QcewWage(
        area_code=area_code,
        requested_naics=naics,
        used_naics=row["industry_code"],
        year=year,
        quarter=quarter,
        avg_weekly_wage=float(row["avg_wkly_wage"]),
        employment=int(row["month3_emplvl"]),
        establishments=int(row["qtrly_estabs"]),
        source=record,
    )


def bls_series_payload(series_ids: list[str], start_year: int, end_year: int) -> dict[str, Any]:
    """The public part of a BLS API v2 request (the registration key is added separately and never stored)."""
    return {"seriesid": list(series_ids), "startyear": str(start_year), "endyear": str(end_year)}


def parse_bls_series(data: dict[str, Any]) -> dict[str, list[tuple[str, str, float | None]]]:
    """{series id: [(year, period, value)]}; a value of '-' (not available) becomes None."""
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise UpstreamError(BLS_HOST, f"status {data.get('status')!r}: {data.get('message')}")
    series: dict[str, list[tuple[str, str, float | None]]] = {}
    for item in data.get("Results", {}).get("series", []):
        points = []
        for point in item.get("data", []):
            raw = str(point.get("value", "")).replace(",", "")
            value = float(raw) if raw.replace(".", "", 1).isdigit() else None
            points.append((str(point.get("year")), str(point.get("period")), value))
        series[str(item.get("seriesID"))] = points
    return series


def check_bls_answer(fetched: FetchResult) -> None:
    """Accept a BLS API v2 answer only when it is JSON with status REQUEST_SUCCEEDED (an http.Accept check).

    BLS answers a refused key or an exhausted quota with HTTP 200 and another status, so the status is checked
    before the answer is cached.
    """
    data = expect_json(fetched, BLS_HOST)
    if not isinstance(data, dict):
        raise UpstreamError(BLS_HOST, "expected a JSON object")
    parse_bls_series(data)


def fetch_bls_series(
    client: PoliteClient, series_ids: list[str], start_year: int, end_year: int
) -> tuple[dict[str, list[tuple[str, str, float | None]]], SourceRecord]:
    """BLS API v2 time series. Needs BLS_API_KEY (raises MissingApiKey without it)."""
    key = require_key("BLS_API_KEY")
    payload = {**bls_series_payload(series_ids, start_year, end_year), "registrationkey": key}
    fetched = client.post_json(BLS_V2_URL, payload, secret_fields=["registrationkey"], accept=check_bls_answer)
    return parse_bls_series(fetched.json()), BLS_V2_SOURCE.record(fetched, vintage=f"{start_year}-{end_year}")
