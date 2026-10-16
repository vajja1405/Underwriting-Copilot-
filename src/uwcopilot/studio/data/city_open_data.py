"""City open-data business licenses (Socrata SODA API): licensed businesses of a given kind near a site.

Two documented datasets are wired (dataset ids and category values were checked against the live portals on
2026-10-12; the recorded metadata and category counts are in tests/studio/fixtures/city_open_data/):
- City of Chicago "Business Licenses" (data.cityofchicago.org, dataset r5kz-chrr; terms: "See Terms of Use");
- NYC Department of Consumer and Worker Protection "Issued Licenses" (data.cityofnewyork.us, dataset w7w3-xahh).
Other cities raise NotAvailable until a documented dataset is recorded.

Only the category fields, the coordinates and the expiration date are requested ($select): no business or owner
names, so
no personal data is pulled. A profile with no matching license category in a city raises NotAvailable.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.geo import haversine_miles, miles_to_degrees
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.osm import Poi
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord

MAX_ROWS = 2000


@dataclass(frozen=True)
class CityLicenseDataset:
    """One city's license dataset and how Studio's business types map onto its categories."""

    adapter: str
    resource_url: str
    category_fields: tuple[str, ...]
    expiration_field: str
    source: SourceInfo
    # {profile id: ((field, value), ...)}; a row matches when one of its fields contains one of the values.
    categories: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


DATASETS: dict[str, CityLicenseDataset] = {
    "chicago": CityLicenseDataset(
        adapter="chicago",
        resource_url="https://data.cityofchicago.org/resource/r5kz-chrr.json",
        category_fields=("license_description", "business_activity"),
        expiration_field="expiration_date",
        source=SourceInfo(
            source_id="chicago_business_licenses",
            publisher="City of Chicago",
            dataset="Business Licenses (data.cityofchicago.org, r5kz-chrr)",
            license_note="Portal metadata says 'See Terms of Use'; terms to record from the portal (not yet recorded)",
        ),
        categories={
            "car_wash": (
                ("business_activity", "Operation of a Hand Car Wash"),
                ("business_activity", "Operation of an Automatic Car Wash"),
            ),
            "laundromat": (("business_activity", "Operation of a Laundromat - Self Service"),),
            "retail_storefront": (
                ("business_activity", "Retail Sales of General Merchandise and Non-Perishable Food"),
            ),
            "motel": (("license_description", "Hotel"),),
            "short_term_rental": (
                ("license_description", "Shared Housing Unit Operator"),
                ("license_description", "Vacation Rental"),
            ),
        },
    ),
    "nyc": CityLicenseDataset(
        adapter="nyc",
        resource_url="https://data.cityofnewyork.us/resource/w7w3-xahh.json",
        category_fields=("business_category",),
        expiration_field="lic_expir_dd",
        source=SourceInfo(
            source_id="nyc_dcwp_licenses",
            publisher="NYC Department of Consumer and Worker Protection (DCWP)",
            dataset="Issued Licenses (data.cityofnewyork.us, w7w3-xahh)",
            license_note="Terms to record from https://opendata.cityofnewyork.us/ (not yet recorded)",
        ),
        categories={
            "car_wash": (("business_category", "Car Wash"),),
            "motel": (("business_category", "Hotel"),),
        },
    ),
}


def dataset_for(adapter: str) -> CityLicenseDataset:
    """The dataset for an adapter id; NotAvailable when the city has no documented dataset yet."""
    if adapter not in DATASETS:
        raise NotAvailable(f"no documented city open-data license dataset recorded for {adapter!r} yet")
    return DATASETS[adapter]


def soql_quote(value: str) -> str:
    """A SoQL string literal (single quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


def licenses_request(
    dataset: CityLicenseDataset, profile_id: str, lat: float, lon: float, radius_miles: float, today: date
) -> dict[str, str]:
    """SoQL parameters: unexpired licenses of the profile's categories inside the box around the site."""
    values = dataset.categories.get(profile_id)
    if not values:
        raise NotAvailable(f"{dataset.adapter} licenses have no category recorded for business type {profile_id!r}")
    dlat, dlon = miles_to_degrees(lat, radius_miles, radius_miles)
    category = " OR ".join(f"{name} like {soql_quote('%' + value + '%')}" for name, value in values)
    where = (
        f"({category}) AND latitude between {lat - dlat:.6f} and {lat + dlat:.6f} "
        f"AND longitude between {lon - dlon:.6f} and {lon + dlon:.6f} "
        f"AND {dataset.expiration_field} >= {soql_quote(today.isoformat())}"
    )
    return {
        "$select": ",".join([*dataset.category_fields, "latitude", "longitude", dataset.expiration_field]),
        "$where": where,
        "$limit": str(MAX_ROWS),
    }


def parse_licenses(
    rows: list[dict[str, Any]], dataset: CityLicenseDataset, lat: float, lon: float, radius_miles: float
) -> list[Poi]:
    """POIs within radius_miles of the site (the box query is trimmed to a circle), nearest first."""
    if not isinstance(rows, list):
        raise UpstreamError(dataset.adapter, "expected a JSON list of rows")
    pois: list[Poi] = []
    for index, row in enumerate(rows):
        try:
            poi_lat, poi_lon = float(row["latitude"]), float(row["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = haversine_miles(lat, lon, poi_lat, poi_lon)
        if distance <= radius_miles:
            tags = {name: str(row.get(name, "")) for name in dataset.category_fields}
            pois.append(
                Poi(poi_id=f"{dataset.adapter}/{index}", lat=poi_lat, lon=poi_lon, tags=tags, distance_miles=distance)
            )
    pois.sort(key=lambda poi: poi.distance_miles or 0.0)
    return pois


def licensed_businesses_near(
    client: PoliteClient, adapter: str, profile_id: str, lat: float, lon: float, radius_miles: float, today: date
) -> tuple[list[Poi], SourceRecord]:
    """Unexpired licenses of the profile's kind within radius_miles of the site, with provenance."""
    dataset = dataset_for(adapter)
    params = licenses_request(dataset, profile_id, lat, lon, radius_miles, today)
    fetched = client.get(dataset.resource_url, params)
    record = dataset.source.record(
        fetched, vintage=f"live portal data at {fetched.retrieved_at.date()}", geography=f"{lat:.5f},{lon:.5f}"
    )
    return parse_licenses(fetched.json(), dataset, lat, lon, radius_miles), record
