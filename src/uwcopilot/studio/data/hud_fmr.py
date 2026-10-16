"""HUD Fair Market Rents (FMR) by county: a RESIDENTIAL rent reference, used only for short-term rentals.

Needs HUD_USER_TOKEN (the HUD USER API answers 401 without it). FMRs are residential: they are never used as
commercial rent. The request shape (GET /fmr/data/<entity id>?year=<year> with a Bearer token) and the response
fields below were written from the author's knowledge of the API documentation at
https://www.huduser.gov/portal/dataset/fmr-api.html, which could not be checked in this session: the tests use a
HAND-WRITTEN fixture, and the entity-id format must be confirmed against the documentation before a live run.
"""

from dataclasses import dataclass
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.http import PoliteClient, json_object_check
from uwcopilot.studio.data.keys import require_key
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

BASE_URL = "https://www.huduser.gov/hudapi/public/fmr/data"
BEDROOM_FIELDS: tuple[str, ...] = ("Efficiency", "One-Bedroom", "Two-Bedroom", "Three-Bedroom", "Four-Bedroom")
SOURCE = SourceInfo(
    source_id="hud_fmr",
    publisher="U.S. Department of Housing and Urban Development (HUD USER)",
    dataset="Fair Market Rents API (residential reference only)",
    license_note="Terms to verify at https://www.huduser.gov/portal/dataset/fmr-api.html (not yet recorded)",
)


@dataclass(frozen=True)
class FairMarketRents:
    """Monthly residential Fair Market Rents by bedroom count for one area and year."""

    entity_id: str
    area_name: str
    year: str
    rents_by_bedrooms: dict[str, float]
    source: SourceRecord


def county_entity_id(state_fips: str, county_fips: str) -> str:
    """HUD's county entity id: state + county FIPS + '99999' (UNVERIFIED format; confirm in the API documentation)."""
    return f"{state_fips}{county_fips}99999"


def parse_fmr(data: dict[str, Any]) -> tuple[str, str, dict[str, float]]:
    """(area name, year, {bedroom field: rent}) from the API answer."""
    body = data.get("data")
    if not isinstance(body, dict):
        raise UpstreamError("www.huduser.gov", "answer has no 'data' object")
    basic = body.get("basicdata")
    if isinstance(basic, list):  # areas with small-area FMRs return one entry per ZIP code; use the area-wide entry
        basic = next(
            (item for item in basic if item.get("zip_code") in (None, "MSA level")), basic[0] if basic else None
        )
    if not isinstance(basic, dict):
        raise NotAvailable("HUD returned no basic Fair Market Rent data for this area")
    rents = {name: float(basic[name]) for name in BEDROOM_FIELDS if basic.get(name) not in (None, "")}
    return str(body.get("county_name") or body.get("metro_name") or ""), str(basic.get("year", "")), rents


def fetch_fmr(client: PoliteClient, entity_id: str, year: int) -> FairMarketRents:
    """Fair Market Rents for one HUD entity id and year. Needs HUD_USER_TOKEN."""
    token = require_key("HUD_USER_TOKEN")
    fetched = client.get(
        f"{BASE_URL}/{entity_id}",
        {"year": str(year)},
        headers={"Authorization": f"Bearer {token}"},
        secret_headers=["Authorization"],
        accept=json_object_check("www.huduser.gov", "data"),
    )
    area, data_year, rents = parse_fmr(fetched.json())
    record = SOURCE.record(fetched, vintage=f"FY{data_year or year}", geography=entity_id)
    return FairMarketRents(
        entity_id=entity_id, area_name=area, year=data_year or str(year), rents_by_bedrooms=rents, source=record
    )
