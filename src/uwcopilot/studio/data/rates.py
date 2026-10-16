"""Interest rates from FRED (Federal Reserve Bank of St. Louis): the bank prime loan rate for the S3 default rate.

Two ways in:
- with FRED_API_KEY: the FRED API, series/observations as JSON (https://fred.stlouisfed.org/docs/api/fred/);
- without a key: the keyless CSV download fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>, whose header is
  `observation_date,<SERIES>`. Missing observations are '.' in both.

The series id is DPRIME (daily bank prime loan rate). S3's default loan rate is this rate plus a stated, editable
spread; Studio never states a rate without its date and source. Terms per series to verify on its FRED page.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.http import Accept, FetchResult, PoliteClient, expect_json
from uwcopilot.studio.data.keys import read_key
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

PRIME_SERIES = "DPRIME"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"
SOURCE = SourceInfo(
    source_id="fred",
    publisher="Federal Reserve Bank of St. Louis (FRED)",
    dataset="FRED series observations",
    license_note="Terms to verify at https://fred.stlouisfed.org/legal/ and the series page (not yet recorded)",
)


@dataclass(frozen=True)
class RateObservation:
    """The latest observation of a rate series, in percent."""

    series_id: str
    observation_date: date
    value_pct: float
    source: SourceRecord


def csv_request(series_id: str, start: date) -> tuple[str, dict[str, str]]:
    """(url, params) for the keyless CSV download from a start date."""
    return CSV_URL, {"id": series_id, "cosd": start.isoformat()}


def api_request(series_id: str, start: date) -> tuple[str, dict[str, str]]:
    """(url, public params) for the keyed API; the api_key is added separately and never stored."""
    return API_URL, {"series_id": series_id, "file_type": "json", "observation_start": start.isoformat()}


def parse_csv(text: str, series_id: str) -> list[tuple[date, float]]:
    """[(date, value)] from the keyless CSV, skipping '.' (missing) values."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or series_id not in reader.fieldnames:
        raise UpstreamError("fred.stlouisfed.org", f"CSV has no column {series_id!r}")
    date_column = "observation_date" if "observation_date" in reader.fieldnames else reader.fieldnames[0]
    points = []
    for row in reader:
        raw = (row.get(series_id) or "").strip()
        if raw and raw != ".":
            points.append((date.fromisoformat(row[date_column]), float(raw)))
    return points


def parse_api(data: dict[str, Any]) -> list[tuple[date, float]]:
    """[(date, value)] from the API's JSON, skipping '.' (missing) values."""
    if "observations" not in data:
        raise UpstreamError("api.stlouisfed.org", str(data.get("error_message", "no observations in the answer")))
    return [
        (date.fromisoformat(item["date"]), float(item["value"]))
        for item in data["observations"]
        if item.get("value") not in (None, "", ".")
    ]


def check_api_answer(fetched: FetchResult) -> None:
    """Accept a FRED API answer only when it is JSON with observations (an http.Accept check)."""
    data = expect_json(fetched, "api.stlouisfed.org")
    if not isinstance(data, dict):
        raise UpstreamError("api.stlouisfed.org", "expected a JSON object")
    parse_api(data)


def csv_check(series_id: str) -> Accept:
    """An http.Accept check for the keyless CSV: it must have a column for the series."""

    def check(fetched: FetchResult) -> None:
        parse_csv(fetched.text(), series_id)

    return check


def latest_rate(client: PoliteClient, start: date, series_id: str = PRIME_SERIES) -> RateObservation:
    """The newest observation since start: the API when FRED_API_KEY is set, otherwise the keyless CSV."""
    key = read_key("FRED_API_KEY")
    if key is not None:
        url, params = api_request(series_id, start)
        fetched = client.get(url, {**params, "api_key": key}, secret_params=["api_key"], accept=check_api_answer)
        points = parse_api(fetched.json())
    else:
        url, params = csv_request(series_id, start)
        fetched = client.get(url, params, accept=csv_check(series_id))
        points = parse_csv(fetched.text(), series_id)
    if not points:
        raise NotAvailable(f"FRED returned no {series_id} observations since {start.isoformat()}")
    observed, value = max(points)
    record = SOURCE.record(
        fetched, vintage=f"{series_id} observation of {observed.isoformat()}", geography="United States"
    )
    return RateObservation(series_id=series_id, observation_date=observed, value_pct=value, source=record)
