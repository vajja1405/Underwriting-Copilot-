"""Measure a site's location factors (raw values, before any scoring), each with its status, reason and sources.

What is measured, and where (rings come from trade_area.py):
- residents, daytime workers, households, renter households: the sum over tracts whose centroid lies in the MARKET
  ring, always including the site's own tract. If any of those tracts has no value, the sum is UNKNOWN: a missing
  tract is never counted as zero.
- median household income: the household-weighted mean of the published tract medians in the MARKET ring (tracts
  without a published median are skipped and counted in the reason). A median of medians is an approximation; it
  is used for ranking, not reported as the area's true median.
- competition: same-type places (the profile's competitor tags) in the NEAR ring, with the nearest distance, and
  the saturation (competitors per 10,000 residents in the MARKET ring) for context.
- anchors: places with the profile's anchor tags in the NEAR ring.
- tourism: tourism anchors (profiles.factors.TOURISM_TAGS) in the MARKET ring.
- short-term rental comparables: listings in the NEAR ring, with their median nightly price for context.
- traffic: AADT of the nearest road segment with traffic counts (fabric.nearest_road).
- commercial rent: the founder's quote for the site, else the metro value (a range that is the same for every site).

Nothing here is a fact about the world: the measurements are counts and sums of what the data fabric returns.
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from uwcopilot.studio.data.fabric import TRACT_FACTORS, FactorValue, PoiSearch, required_factors
from uwcopilot.studio.data.osm import Poi
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.profiles.factors import LOCATION_FACTORS, TOURISM_TAGS
from uwcopilot.studio.profiles.models import Profile
from uwcopilot.studio.siteiq.context import MetroContext
from uwcopilot.studio.siteiq.models import SiteInputs, SiteStatus
from uwcopilot.studio.siteiq.trade_area import Area, near_and_market, ring_area

PER_RESIDENTS = 10_000
SUM_LABELS: dict[str, str] = {
    "resident_population": "residents",
    "daytime_workers": "daytime workers",
    "households": "households",
    "renter_households": "renter households",
}
POINT_FACTORS: tuple[str, ...] = ("competition", "anchors", "tourism", "str_comparables", "traffic_aadt")


@dataclass(frozen=True)
class RawFactor:
    """One measured factor. value None means UNKNOWN; reason says what was measured or why it is missing."""

    factor: str
    value: float | None
    status: SiteStatus
    area: str
    reason: str
    sources: tuple[SourceRecord, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def data_status(ctx: MetroContext) -> SiteStatus:
    """SYNTHETIC for the generated city, DATA for a real metro's snapshot."""
    return "SYNTHETIC" if ctx.fabric.data_label == "SYNTHETIC" else "DATA"


def unique_sources(sources: list[SourceRecord | None]) -> tuple[SourceRecord, ...]:
    """The distinct SourceRecords (same source, checksum and geography counted once), in first-seen order."""
    seen: dict[tuple[str, str, str | None], SourceRecord] = {}
    for record in sources:
        if record is not None:
            seen.setdefault((record.source_id, record.sha256, record.geography), record)
    return tuple(seen.values())


def area_mask(ctx: MetroContext, area: Area, own: int | None) -> np.ndarray:
    """Tracts whose centroid is in the area, plus the site's own tract."""
    mask = area.contains(ctx.tracts.lats, ctx.tracts.lons)
    if own is not None:
        mask[own] = True
    return mask


def tract_features_in(ctx: MetroContext, mask: np.ndarray) -> list[dict[str, FactorValue]]:
    """The fabric's tract features (with reasons and sources) for the tracts in the mask."""
    return [ctx.fabric.tract_features(ctx.tracts.ids[index]) for index in np.flatnonzero(mask)]


def measure_tract_factor(
    ctx: MetroContext, factor: str, area: Area, mask: np.ndarray, features: list[dict[str, FactorValue]] | None
) -> RawFactor:
    """A tract-level factor summed (or income averaged) over the tracts in mask (area_mask of the area).

    features (the fabric's tract features for the same tracts) are given for a scored site: they add reasons and
    sources. They are left out for reference locations, where only the value is needed.
    """
    count = int(mask.sum())
    sources = unique_sources([item[factor].source for item in features]) if features else ()
    if count == 0:
        reason = f"no tract centroid {area.label}" if len(ctx.tracts) else "the metro has no tract layer (no snapshot)"
        return RawFactor(factor, None, "UNKNOWN", area.label, reason)
    values = ctx.tracts.values[factor][mask]
    if factor == "median_household_income":
        return income_factor(ctx, values, ctx.tracts.values["households"][mask], area, count, sources, features)
    missing = int(np.isnan(values).sum())
    if missing:
        detail = unknown_detail(factor, features)
        reason = f"{missing} of {count} tracts {area.label} have no value, so the sum is UNKNOWN{detail}"
        return RawFactor(factor, None, "UNKNOWN", area.label, reason, details={"tracts": count, "missing": missing})
    total = float(values.sum())
    reason = f"{total:,.0f} {SUM_LABELS[factor]} {area.label} ({count} tracts)"
    return RawFactor(factor, total, data_status(ctx), area.label, reason, sources, {"tracts": count})


def income_factor(
    ctx: MetroContext,
    incomes: np.ndarray,
    households: np.ndarray,
    area: Area,
    count: int,
    sources: tuple[SourceRecord, ...],
    features: list[dict[str, FactorValue]] | None,
) -> RawFactor:
    """Household-weighted mean of the published tract medians in the area (tracts without one are skipped)."""
    usable = ~np.isnan(incomes) & ~np.isnan(households) & (households > 0)
    published = int(usable.sum())
    details = {"tracts": count, "tracts_with_published_median": published}
    if published == 0:
        detail = unknown_detail("median_household_income", features)
        reason = f"no tract {area.label} has a published median income{detail}"
        return RawFactor("median_household_income", None, "UNKNOWN", area.label, reason, details=details)
    value = float(np.average(incomes[usable], weights=households[usable]))
    reason = (
        f"household-weighted mean of tract median incomes {area.label}: ${value:,.0f} "
        f"({published} of {count} tracts publish a median)"
    )
    return RawFactor("median_household_income", value, data_status(ctx), area.label, reason, sources, details)


def unknown_detail(factor: str, features: list[dict[str, FactorValue]] | None) -> str:
    """The fabric's own reason for the first UNKNOWN value (for example 'no ACS estimate in the snapshot')."""
    for item in features or []:
        if item[factor].status == "UNKNOWN":
            return f" ({item[factor].reason})"
    return ""


def inside(pois: list[Poi], area: Area) -> list[Poi]:
    """The places that lie in the area."""
    if not pois:
        return []
    mask = area.contains(np.array([poi.lat for poi in pois]), np.array([poi.lon for poi in pois]))
    return [poi for poi, keep in zip(pois, mask, strict=True) if keep]


def unknown_from_search(factor: str, search: PoiSearch, area: Area) -> RawFactor:
    """An UNKNOWN factor carrying the search's reason (the layer is missing, which differs from 'none found')."""
    return RawFactor(factor, None, "UNKNOWN", area.label, search.reason)


def measure_competition(
    ctx: MetroContext, profile: Profile, lat: float, lon: float, near: Area, market: Area, residents: float | None
) -> RawFactor:
    """Same-type places in the near ring (scored), nearest distance and saturation per 10,000 residents (context)."""
    search = ctx.fabric.sites_near(lat, lon, max(near.search_miles, market.search_miles), profile.competitor_tags)
    if search.status == "UNKNOWN":
        return unknown_from_search("competition", search, near)
    in_near = inside(search.pois, near)
    in_market = inside(search.pois, market)
    nearest = min((poi.distance_miles for poi in search.pois if poi.distance_miles is not None), default=None)
    saturation = len(in_market) / residents * PER_RESIDENTS if residents else None
    details = {
        "count_near": len(in_near),
        "count_market": len(in_market),
        "nearest_miles": round(nearest, 2) if nearest is not None else None,
        "per_10k_residents_market": round(saturation, 3) if saturation is not None else None,
        "tags": list(profile.competitor_tags),
    }
    where = f"nearest {nearest:.1f} miles" if nearest is not None else f"none within {search_label(near, market)}"
    reason = f"{len(in_near)} same-type places {near.label}; {where}"
    sources = (search.source,) if search.source else ()
    return RawFactor("competition", float(len(in_near)), search.status, near.label, reason, sources, details)


def search_label(near: Area, market: Area) -> str:
    """The widest searched distance, for 'none within ...' reasons."""
    return f"{max(near.search_miles, market.search_miles):g} miles"


def measure_count(ctx: MetroContext, factor: str, lat: float, lon: float, area: Area, tags: list[str]) -> RawFactor:
    """How many places with any of the tags lie in the area (anchors, tourism)."""
    search = ctx.fabric.sites_near(lat, lon, area.search_miles, tags)
    if search.status == "UNKNOWN":
        return unknown_from_search(factor, search, area)
    found = inside(search.pois, area)
    word = "anchor places" if factor == "anchors" else "tourism anchors"
    reason = f"{len(found)} {word} {area.label}"
    if found:
        reason += f"; nearest {found[0].distance_miles:.1f} miles" if found[0].distance_miles is not None else ""
    sources = (search.source,) if search.source else ()
    return RawFactor(factor, float(len(found)), search.status, area.label, reason, sources, {"tags": tags})


def measure_comparables(ctx: MetroContext, lat: float, lon: float, area: Area) -> RawFactor:
    """Short-term rental listings in the area, with their median nightly price as context."""
    search = ctx.fabric.str_comparables(lat, lon, area.search_miles)
    if search.status == "UNKNOWN":
        return RawFactor("str_comparables", None, "UNKNOWN", area.label, search.reason)
    listings = search.listings
    if area.kind != "ring" and listings:
        mask = area.contains(np.array([item.lat for item in listings]), np.array([item.lon for item in listings]))
        listings = [item for item, keep in zip(listings, mask, strict=True) if keep]
    prices = [item.nightly_price for item in listings if item.nightly_price is not None]
    median_price = float(np.median(prices)) if prices else None
    reason = f"{len(listings)} short-term rental listings {area.label}"
    if median_price is not None:
        reason += f"; median nightly price ${median_price:,.0f}"
    details = {"median_nightly_price": median_price}
    sources = (search.source,) if search.source else ()
    return RawFactor("str_comparables", float(len(listings)), search.status, area.label, reason, sources, details)


def measure_traffic(ctx: MetroContext, lat: float, lon: float) -> RawFactor:
    """AADT on the nearest road segment with traffic counts."""
    road = ctx.nearest_road(lat, lon)
    where = "nearest road with traffic counts"
    if road.segment is None:
        return RawFactor("traffic_aadt", None, "UNKNOWN", where, road.reason)
    segment = road.segment
    route = segment.route_name or segment.route_id
    reason = f"{segment.aadt:,} vehicles/day on {route}, {road.distance_miles:.2f} miles away"
    details = {
        "route": route,
        "distance_miles": round(road.distance_miles or 0.0, 3),
        "functional_class": segment.functional_class,
        "data_year": segment.data_year,
    }
    sources = (road.source,) if road.source else ()
    return RawFactor("traffic_aadt", float(segment.aadt), road.status, where, reason, sources, details)


def measure_rent(ctx: MetroContext, profile: Profile, inputs: SiteInputs | None) -> RawFactor:
    """The founder's rent quote for the site, else the metro value (the same for every site in the metro)."""
    metro_rent = ctx.fabric.metro_inputs(profile)["commercial_rent"]
    details = {"metro_low": metro_rent.low, "metro_high": metro_rent.high, "metro_status": metro_rent.status}
    if inputs is not None and inputs.rent_quote_usd_per_month is not None:
        quote = inputs.rent_quote_usd_per_month
        reason = f"founder's rent quote ${quote:,.0f}/month for this site (not checked by Studio)"
        return RawFactor("commercial_rent", quote, "FOUNDER_INPUT", "this site", reason, (), details)
    reason = f"metro value, the same for every site: {metro_rent.reason}"
    sources = (metro_rent.source,) if metro_rent.source else ()
    return RawFactor("commercial_rent", metro_rent.value, metro_rent.status, "whole metro", reason, sources, details)


def not_measured(factor: str, area: Area) -> RawFactor:
    """A point-layer factor that was deliberately not looked up (a live layer outside the reference sample)."""
    reason = "live layer not looked up for this reference location"
    return RawFactor(factor, None, "UNKNOWN", area.label, reason)


def measure_point(
    ctx: MetroContext,
    profile: Profile,
    lat: float,
    lon: float,
    rings: tuple[float, ...],
    *,
    inputs: SiteInputs | None = None,
    explain: bool = False,
    point_layers: bool = True,
) -> dict[str, RawFactor]:
    """Every factor the profile needs (weighted factors and demand inputs), measured at one location.

    explain=True also collects the fabric's per-tract reasons and sources (slower; used for a scored site, not for
    the thousands of reference locations). point_layers=False skips POI and road lookups (UNKNOWN instead).
    """
    near_miles, market_miles = near_and_market(rings)
    near, market = ring_area(lat, lon, near_miles), ring_area(lat, lon, market_miles)
    needed = set(required_factors(profile))
    own = ctx.own_tract(lat, lon)
    mask = area_mask(ctx, market, own)
    features = tract_features_in(ctx, mask) if explain else None
    measured = {
        factor: measure_tract_factor(ctx, factor, market, mask, features)
        for factor in TRACT_FACTORS
        if factor in needed or (factor == "resident_population" and "competition" in needed)
    }
    for factor in POINT_FACTORS:
        if factor not in needed:
            continue
        if not point_layers:
            measured[factor] = not_measured(factor, near)
        elif factor == "competition":
            residents = measured["resident_population"].value
            measured[factor] = measure_competition(ctx, profile, lat, lon, near, market, residents)
        elif factor == "anchors":
            measured[factor] = measure_count(ctx, "anchors", lat, lon, near, list(profile.anchor_tags))
        elif factor == "tourism":
            measured[factor] = measure_count(ctx, "tourism", lat, lon, market, list(TOURISM_TAGS))
        elif factor == "str_comparables":
            measured[factor] = measure_comparables(ctx, lat, lon, near)
        else:
            measured[factor] = measure_traffic(ctx, lat, lon)
    if "commercial_rent" in needed:
        measured["commercial_rent"] = measure_rent(ctx, profile, inputs)
    unknown_scopes = needed - set(measured)
    for factor in sorted(unknown_scopes):
        reason = f"no measurement is defined for factor scope {LOCATION_FACTORS[factor].scope!r}"
        measured[factor] = RawFactor(factor, None, "UNKNOWN", "n/a", reason)
    return measured
