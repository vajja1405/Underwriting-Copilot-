"""Short-term rental comparables from Inside Airbnb listing files, where a city is covered.

Inside Airbnb publishes per-city listing files; which cities are covered, the file URLs and the terms must be checked
at https://insideairbnb.com/get-the-data/. data.insideairbnb.com answered HTTP 403 from this build environment on
2026-10-12, so coverage of every v1 metro is UNVERIFIED and fetch_comparables() raises NotAvailable: short-term
rental comparables are UNKNOWN until coverage is confirmed (the Studio plan's rule).

parse_listings_csv() reads a listings CSV that an owner downloads by hand. Its column names (id, latitude, longitude,
room_type, price, availability_365) were written from the author's knowledge of the file layout and are checked
when the file is read: a missing column raises UpstreamError, so a changed layout is never read wrongly.
"""

import csv
import io
from dataclasses import dataclass

from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.geo import haversine_miles

REQUIRED_COLUMNS: tuple[str, ...] = ("id", "latitude", "longitude", "room_type", "price", "availability_365")
# Covered cities confirmed against the Inside Airbnb site; empty until someone opens it and records the coverage.
VERIFIED_COVERAGE: dict[str, str] = {}


@dataclass(frozen=True)
class StrListing:
    """One short-term rental listing used as a comparable."""

    listing_id: str
    lat: float
    lon: float
    room_type: str
    nightly_price: float | None
    availability_365: int | None
    distance_miles: float | None = None


def fetch_comparables(city_adapter: str) -> list[StrListing]:
    """Comparables for a city; NotAvailable until the city's coverage is verified and recorded."""
    if city_adapter not in VERIFIED_COVERAGE:
        raise NotAvailable(
            f"Inside Airbnb coverage for {city_adapter!r} is not verified (data.insideairbnb.com answered 403 from the "
            "build environment on 2026-10-12); short-term rental comparables are UNKNOWN"
        )
    raise NotAvailable("automatic download is not built; read a downloaded file with parse_listings_csv()")


def parse_price(raw: str) -> float | None:
    """'$1,250.00' -> 1250.0; empty -> None."""
    cleaned = raw.replace("$", "").replace(",", "").strip()
    return float(cleaned) if cleaned else None


def parse_listings_csv(text: str, lat: float, lon: float, radius_miles: float) -> list[StrListing]:
    """Listings within radius_miles of (lat, lon), nearest first."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise UpstreamError("insideairbnb", f"listings file is missing columns {missing}")
    listings: list[StrListing] = []
    for row in reader:
        row_lat, row_lon = float(row["latitude"]), float(row["longitude"])
        distance = haversine_miles(lat, lon, row_lat, row_lon)
        if distance > radius_miles:
            continue
        availability = row["availability_365"].strip()
        listings.append(
            StrListing(
                listing_id=row["id"],
                lat=row_lat,
                lon=row_lon,
                room_type=row["room_type"],
                nightly_price=parse_price(row["price"]),
                availability_365=int(availability) if availability.isdigit() else None,
                distance_miles=distance,
            )
        )
    listings.sort(key=lambda item: item.distance_miles or 0.0)
    return listings
