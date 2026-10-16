"""Optional per-site extras that are shown next to the score but not used in it.

- Drive-time demand area: with ORS_API_KEY set and a live client, the demand estimate uses a 10-minute drive-time
  area from OpenRouteService instead of the market ring. Without them (or when the request fails) the market ring
  is used and the reason is recorded in TradeAreaInfo.
- City business licenses (Chicago, NYC; studio.data.city_open_data): the number of unexpired licenses of the
  business type near the site. There is no metro-wide reference distribution for it, so it is context only.
"""

from datetime import date

from uwcopilot.studio.data.city_open_data import licensed_businesses_near
from uwcopilot.studio.data.errors import NotAvailable, StudioDataError
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.keys import read_key
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.models import Profile
from uwcopilot.studio.siteiq.isochrones import fetch_isochrones
from uwcopilot.studio.siteiq.models import ContextItem, TradeAreaInfo, TradeAreaMethod
from uwcopilot.studio.siteiq.trade_area import (
    DRIVE_MINUTES,
    MARKET_DRIVE_MINUTES,
    Area,
    drive_time_area,
    near_and_market,
    ring_area,
)


def trade_area_info(
    metro: MetroConfig,
    rings: tuple[float, ...],
    method: TradeAreaMethod,
    area: Area,
    reason: str,
    minutes: list[int] | None = None,
    source: SourceRecord | None = None,
) -> TradeAreaInfo:
    """The TradeAreaInfo stored with a site score."""
    near, market = near_and_market(rings)
    return TradeAreaInfo(
        demand_method=method,
        rings_miles=list(rings),
        near_miles=near,
        market_miles=market,
        ring_multiplier=metro.trade_area.ring_multiplier,
        drive_minutes=minutes or [],
        demand_area_label=area.label,
        reason=reason,
        source=source,
    )


def demand_area(
    metro: MetroConfig, rings: tuple[float, ...], lat: float, lon: float, live: PoliteClient | None
) -> tuple[Area, TradeAreaInfo]:
    """The area the demand estimate uses (drive time when possible, else the market ring) and its description."""
    ring = ring_area(lat, lon, near_and_market(rings)[1])
    if live is None:
        reason = "distance rings: drive-time areas need a live client and ORS_API_KEY"
        return ring, trade_area_info(metro, rings, "rings", ring, reason)
    api_key = read_key("ORS_API_KEY")
    if api_key is None:
        reason = "distance rings: ORS_API_KEY is not set (recorded fallback)"
        return ring, trade_area_info(metro, rings, "rings", ring, reason)
    try:
        polygons, record = fetch_isochrones(live, api_key, lat, lon, DRIVE_MINUTES)
    except StudioDataError as exc:
        reason = f"distance rings: OpenRouteService request failed ({exc})"
        return ring, trade_area_info(metro, rings, "rings", ring, reason)
    polygon = polygons.get(MARKET_DRIVE_MINUTES)
    if polygon is None or len(polygon) < 3:
        reason = f"distance rings: no {MARKET_DRIVE_MINUTES}-minute polygon in the OpenRouteService answer"
        return ring, trade_area_info(metro, rings, "rings", ring, reason)
    area = drive_time_area(lat, lon, MARKET_DRIVE_MINUTES, polygon)
    reason = f"OpenRouteService {MARKET_DRIVE_MINUTES}-minute drive-time area for the demand estimate; scores use rings"
    return area, trade_area_info(metro, rings, "drive_time", area, reason, sorted(polygons), record)


def license_context(
    metro: MetroConfig, profile: Profile, lat: float, lon: float, near_miles: float, live: PoliteClient | None
) -> list[ContextItem]:
    """Unexpired city licenses of the business type near the site, for metros with a city license adapter."""
    items: list[ContextItem] = []
    for enhancement in metro.enabled_enhancements("city_open_data"):
        name = f"{enhancement.adapter}_business_licenses"
        if live is None:
            reason = "city license lookups are live per site and live lookups are off"
            items.append(ContextItem(name=name, value=None, status="UNKNOWN", reason=reason))
            continue
        try:
            found, record = licensed_businesses_near(
                live, enhancement.adapter, profile.id, lat, lon, near_miles, date.today()
            )
        except NotAvailable as exc:
            items.append(ContextItem(name=name, value=None, status="UNKNOWN", reason=exc.reason))
            continue
        except StudioDataError as exc:
            items.append(ContextItem(name=name, value=None, status="UNKNOWN", reason=f"lookup failed: {exc}"))
            continue
        reason = f"{len(found)} unexpired licenses of this business type within {near_miles:g} miles (context only)"
        items.append(ContextItem(name=name, value=float(len(found)), status="DATA", reason=reason, source=record))
    return items
