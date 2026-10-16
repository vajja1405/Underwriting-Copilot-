"""Coverage rows of a real metro's snapshot: per required factor, data or not, from where, and for what share.

The rules (Studio rule G: coverage is shown, not hidden):
- tract factors that SiteIQ SUMS over the MARKET ring (residents, daytime workers, households, renter households):
  the share of tracts whose market-ring sum is known, because one tract without a value makes the whole sum
  UNKNOWN (siteiq/factors.py measure_tract_factor). The ring is the one SiteIQ uses for that business type, measured
  from each tract centroid (the site's own tract is the tract itself). The per-tract share is given in the reason;
- median household income (averaged, tracts without a published median skipped): the share of tracts with a value;
- traffic: the share of tract centroids with an HPMS segment with AADT within ROAD_SEARCH_MILES, from the snapshot's
  road layer. Without a stored layer, traffic can only be looked up live for one site, so it counts 0 and is flagged
  live_per_site;
- competition, anchors and tourism: with the snapshot's Overture places layer, the share of tracts whose ring (the
  ring SiteIQ counts that factor in) lies inside the extract's bounding box, times definition_share, the share of the
  factor's tag filters that an Overture category is mapped to. A filter without a category is never counted as
  "none found": it lowers the coverage instead. Without a places layer the factor counts 0;
- short-term rental comparables: UNKNOWN (Inside Airbnb coverage not verified);
- metro-level inputs (rent, wages, prime rate): DATA counts 1, anything else 0.
Percentages in reasons are rounded DOWN to one decimal (percent_floor), so 99.52% reads "99.5%", never "100%".
"""

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from uwcopilot.studio.data.fabric_base import (
    ROAD_SEARCH_MILES,
    FactorCoverage,
    FactorStatus,
    FactorValue,
    required_factors,
    share_basis_for,
)
from uwcopilot.studio.data.geo import haversine_miles_array
from uwcopilot.studio.data.lodes import YEAR
from uwcopilot.studio.data.places_layer import circle_inside_box
from uwcopilot.studio.metros.rings import near_and_market, scaled_rings
from uwcopilot.studio.profiles.factors import LOCATION_FACTORS, METRO_LEVEL_INPUTS, TOURISM_TAGS
from uwcopilot.studio.profiles.models import Profile

if TYPE_CHECKING:
    from uwcopilot.studio.data.fabric_snapshot import SnapshotFabric

# (status, share, source id, reason, live per site, definition share)
Status = tuple[FactorStatus, float, str | None, str, bool, float]
# The tract factors SiteIQ sums over the market ring (the keys of siteiq.factors.SUM_LABELS; a test checks they agree).
RING_SUM_FACTORS: tuple[str, ...] = ("resident_population", "daytime_workers", "households", "renter_households")


def percent_floor(share: float) -> str:
    """A share as a percentage rounded DOWN to one decimal ('99.5%' for 0.9952; '100.0%' only for exactly 1)."""
    return f"{math.floor(round(share * 1000, 6)) / 10:.1f}%"


def coverage_rows(fabric: "SnapshotFabric", profile: Profile) -> list[FactorCoverage]:
    """One FactorCoverage per required factor of the profile."""
    rows: list[FactorCoverage] = []
    table = fabric.tract_table()
    snapshot_date = fabric.snapshot.snapshot_date.isoformat() if fabric.snapshot else None
    needed = required_factors(profile)
    inputs = fabric.metro_inputs(profile) if any(f in METRO_LEVEL_INPUTS for f in needed) else {}
    for factor, weight in needed.items():
        scope = LOCATION_FACTORS[factor].scope
        status, share, source, reason, live, definition = factor_status(fabric, profile, factor, table, inputs)
        rows.append(
            FactorCoverage(
                factor=factor,
                scope=scope,
                weight=weight,
                status=status,
                share_of_tracts=round(share, 4),
                share_basis=share_basis_for(scope, real_metro=True),
                live_per_site=live,
                definition_share=round(definition, 4),
                source=source,
                freshness=snapshot_date,
                reason=reason,
            )
        )
    return rows


def factor_status(
    fabric: "SnapshotFabric",
    profile: Profile,
    factor: str,
    table: dict[str, dict[str, float | None]],
    inputs: dict[str, FactorValue],
) -> Status:
    """The coverage facts of one factor in this metro (see the module docstring)."""
    if fabric.snapshot is None:
        return "UNKNOWN", 0.0, None, fabric.no_snapshot_reason(), False, 1.0
    scope = LOCATION_FACTORS[factor].scope
    if scope == "tract":
        return tract_status(fabric, profile, factor, table)
    if scope == "road_layer":
        return road_status(fabric, table)
    if scope == "metro":
        value = inputs.get(factor)
        if value is None or value.status == "UNKNOWN":
            return "UNKNOWN", 0.0, None, value.reason if value else "not in the snapshot", False, 1.0
        source_id = value.source.source_id if value.source else None
        return value.status, 1.0 if value.status == "DATA" else 0.0, source_id, value.reason, False, 1.0
    if factor == "str_comparables":
        return "UNKNOWN", 0.0, None, "Inside Airbnb coverage not verified for this metro", False, 1.0
    return place_status(fabric, profile, factor)


def ring_sum_share(fabric: "SnapshotFabric", profile: Profile, factor: str) -> tuple[float, float]:
    """(share of tracts whose market-ring sum of the factor is known, the market ring in miles).

    A tract's sum is known when the tract itself and every tract whose centroid lies within the market ring of its
    centroid (great-circle distance <= ring, as SiteIQ's ring_area) have a value.
    """
    _, market = near_and_market(scaled_rings(profile, fabric.metro))
    tracts = fabric.snapshot.tracts if fabric.snapshot else {}
    ids = sorted(tracts)
    if not ids:
        return 0.0, market
    lats = np.array([float(tracts[geoid]["lat"]) for geoid in ids])
    lons = np.array([float(tracts[geoid]["lon"]) for geoid in ids])
    missing = [index for index, geoid in enumerate(ids) if tracts[geoid].get(factor) is None]
    unknown = np.zeros(len(ids), dtype=bool)
    for index in missing:
        unknown |= haversine_miles_array(float(lats[index]), float(lons[index]), lats, lons) <= market
        unknown[index] = True
    return float((~unknown).sum()) / len(ids), market


def tract_status(
    fabric: "SnapshotFabric", profile: Profile, factor: str, table: dict[str, dict[str, float | None]]
) -> Status:
    """Ring-sum share for summed factors (LODES for daytime workers, ACS for the others); per-tract share for income."""
    per_tract = sum(values.get(factor) is not None for values in table.values()) / max(len(table), 1)
    if factor == "daytime_workers" and per_tract == 0:
        status, detail = fabric.snapshot.step_status("lehd_daytime_workers")
        reason = f"no LODES jobs per tract (lehd_daytime_workers step: {status}; {detail})"
        return "UNKNOWN", 0.0, None, reason, False, 1.0
    if factor == "daytime_workers":
        source, what = "lehd_lodes8_wac", f"LODES WAC {YEAR} jobs (jobs located in the tract, not distinct people)"
    else:
        source, what = "census_acs5", "an ACS estimate"
    if factor not in RING_SUM_FACTORS:
        reason = f"{percent_floor(per_tract)} of tracts have {what}"
        return ("DATA" if per_tract > 0 else "UNKNOWN"), per_tract, source, reason, False, 1.0
    share, market = ring_sum_share(fabric, profile, factor)
    without = sum(values.get(factor) is None for values in table.values())
    reason = (
        f"{percent_floor(share)} of tracts have a known {market:g}-mile market-ring sum of {what}; "
        f"{percent_floor(per_tract)} of tracts have a value ({without} without one, and one tract without a value "
        "makes every ring sum that includes it UNKNOWN)"
    )
    return ("DATA" if share > 0 else "UNKNOWN"), share, source, reason, False, 1.0


def road_status(fabric: "SnapshotFabric", table: dict[str, Any]) -> Status:
    """Traffic: per-tract values from the stored road layer, else live per site only (counts 0)."""
    layer, problem = fabric.roads()
    if layer is not None:
        tracts = fabric.snapshot.tracts
        with_road = sum(tracts[geoid].get("traffic_aadt") is not None for geoid in table)
        share = with_road / max(len(table), 1)
        reason = (
            f"{percent_floor(share)} of tract centroids have an HPMS segment with AADT within {ROAD_SEARCH_MILES} "
            f"miles (snapshot road layer, {len(layer)} segments)"
        )
        return ("DATA" if share > 0 else "UNKNOWN"), share, "fhwa_hpms", reason, False, 1.0
    roads = (fabric.snapshot.manifest.get("layers") or {}).get("roads") or {}
    with_aadt, total = roads.get("counties_with_aadt") or 0, roads.get("counties_total") or 0
    if with_aadt == 0:
        status, detail = fabric.snapshot.step_status("hpms_roads")
        reason = f"{problem}; no HPMS road layer in the snapshot (hpms_roads step: {status}; {detail})"
        return "UNKNOWN", 0.0, None, reason, False, 1.0
    reason = (
        f"{problem}, so tracts cannot be ranked on traffic (UNKNOWN); available for one site through a live HPMS "
        f"lookup (segments with AADT in {with_aadt} of {total} counties)"
    )
    return "UNKNOWN", 0.0, "fhwa_hpms", reason, True, 1.0


def place_tags_and_ring(profile: Profile, factor: str, rings: tuple[float, ...]) -> tuple[list[str], float]:
    """The tag filters SiteIQ counts for a place factor and the ring it counts them in."""
    near, market = near_and_market(rings)
    if factor == "competition":
        return list(profile.competitor_tags), near
    if factor == "anchors":
        return list(profile.anchor_tags), near
    return list(TOURISM_TAGS), market


def place_status(fabric: "SnapshotFabric", profile: Profile, factor: str) -> Status:
    """Competition, anchors or tourism from the stored places layer (see the module docstring)."""
    layer, problem = fabric.places()
    if layer is None:
        osm_status, osm_detail = fabric.snapshot.step_status("osm_pois")
        if osm_status == "ok":
            reason = f"{problem}; available for one site through a live OpenStreetMap lookup"
            return "UNKNOWN", 0.0, "osm_overpass", reason, True, 1.0
        reason = f"{problem}; OpenStreetMap layer unavailable ({osm_status}: {osm_detail})"
        return "UNKNOWN", 0.0, None, reason, False, 1.0
    tags, ring = place_tags_and_ring(profile, factor, scaled_rings(profile, fabric.metro))
    definition = layer.definition_share(tags)
    unmapped = layer.unmapped(tags)
    if definition == 0:
        return "UNKNOWN", 0.0, "overture_places", f"no Overture category is mapped to {', '.join(tags)}", False, 0.0
    bbox = (fabric.snapshot.manifest.get("layers") or {}).get("places", {}).get("bbox")
    share = share_inside_box(fabric, bbox, ring)
    reason = (
        f"Overture places release {layer.release}: the {ring:g}-mile ring of {percent_floor(share)} of tracts lies "
        f"inside the extract; {len(tags) - len(unmapped)} of {len(tags)} tag filters mapped"
    )
    if unmapped:
        reason += f" (not counted: {', '.join(unmapped)})"
    return "DATA", share, "overture_places", reason, False, definition


def share_inside_box(fabric: "SnapshotFabric", bbox: list[float] | None, ring_miles: float) -> float:
    """Share of tract centroids whose ring of ring_miles lies inside the extract's box [xmin, xmax, ymin, ymax]."""
    tracts = fabric.snapshot.tracts if fabric.snapshot else {}
    if not bbox or not tracts:
        return 0.0
    inside = sum(
        circle_inside_box(float(item["lat"]), float(item["lon"]), ring_miles, bbox) for item in tracts.values()
    )
    return inside / len(tracts)
