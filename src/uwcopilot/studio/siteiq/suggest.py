"""suggest_areas: the best census tracts in a metro for a business type, with reasons and a GeoJSON map layer.

    from uwcopilot.studio.siteiq import suggest_areas
    result = suggest_areas("laundromat", "synthetic_metro")
    [area.geoid for area in result.areas], result.geojson

How it works:
1. Every tract centroid of the metro is scored with the same method as score_site (the reference distributions of
   reference.py already hold every tract's factor values, so each tract's percentiles come from there).
2. A tract is EXCLUDED, not guessed, when any weighted site factor (tract, POI or road data) is UNKNOWN there, or
   when too little of the profile's weight has data to give an overall score. Exclusions are counted by reason.
3. The rest are ranked by overall score (ties broken by tract id) and the top ones are scored again with
   score_site, which gives their plain-language reasons.
4. The budget is checked against the metro rent range and the profile's build-out range. It cannot filter tracts:
   no tract-level rent or build-out cost data exists, and the result says so.

The GeoJSON layer has one Point per suggested tract centroid and one approximate circle for its market ring.
Census tract boundaries are not loaded, so no tract polygon is drawn.
"""

from collections import Counter

import numpy as np

from uwcopilot.studio import SYNTHETIC_LABEL
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.models import Profile
from uwcopilot.studio.siteiq.context import MetroContext
from uwcopilot.studio.siteiq.models import Budget, BudgetCheck, Exclusion, SiteScore, SuggestedArea, Suggestions
from uwcopilot.studio.siteiq.reference import MetroReference, reference_for, site_factors
from uwcopilot.studio.siteiq.scoring import MIN_SCORED_WEIGHT, WEIGHT_TOLERANCE, resolve, score_site
from uwcopilot.studio.siteiq.trade_area import circle_polygon, near_and_market, scaled_rings

TOP_N = 5
TOP_REASONS = 3
# A used factor scoring below this is shown as a "watch out" (a presentation choice).
WATCH_OUT_BELOW = 50.0


def factor_scores(profile: Profile, reference: MetroReference) -> dict[str, np.ndarray]:
    """{weighted site factor: score 0-100 per reference tract} (NaN where UNKNOWN or not rankable)."""
    scores: dict[str, np.ndarray] = {}
    for name in site_factors(profile):
        percentiles = reference.percentiles(name)
        if profile.design.location_weights[name].direction == "lower_is_better":
            percentiles = 100 - percentiles
        scores[name] = np.round(percentiles, 2)
    return scores


def tract_overall_scores(profile: Profile, reference: MetroReference) -> tuple[np.ndarray, list[str | None]]:
    """(overall score per tract, NaN when excluded; the exclusion reason per tract, None when ranked)."""
    count = len(reference.geoids)
    scores = factor_scores(profile, reference)
    weights = {name: profile.design.location_weights[name].weight for name in scores}
    overall = np.full(count, np.nan)
    reasons: list[str | None] = [None] * count
    for index in range(count):
        missing = [name for name in scores if np.isnan(reference.values[name][index])]
        if missing:
            reasons[index] = f"required data UNKNOWN: {missing[0]}"
            continue
        usable = [name for name in scores if not np.isnan(scores[name][index])]
        share = sum(weights[name] for name in usable)
        if share < MIN_SCORED_WEIGHT - WEIGHT_TOLERANCE:
            reasons[index] = f"too little data to score (factors with data carry {share:.0%} of the weight)"
            continue
        overall[index] = round(sum(weights[name] * scores[name][index] for name in usable) / share, 1)
    return overall, reasons


def ranked_indices(overall: np.ndarray, geoids: list[str]) -> list[int]:
    """Indices of ranked tracts, best first (ties broken by tract id)."""
    ranked = [index for index in range(len(geoids)) if not np.isnan(overall[index])]
    return sorted(ranked, key=lambda index: (-overall[index], geoids[index]))


def area_from_score(rank: int, site: SiteScore) -> SuggestedArea:
    """A suggested tract built from its full site score: strongest reasons first, a weak factor as a watch-out."""
    used = [item for item in site.factors if item.used_in_overall and item.normalized_score is not None]
    strongest = sorted(used, key=lambda item: -item.weight * item.normalized_score)[:TOP_REASONS]
    weakest = min(used, key=lambda item: item.normalized_score, default=None)
    if weakest is not None and weakest.normalized_score >= WATCH_OUT_BELOW:
        weakest = None
    return SuggestedArea(
        rank=rank,
        geoid=site.tract_geoid or "",
        lat=site.lat,
        lon=site.lon,
        district=site.district,
        overall_score=site.overall_score or 0.0,
        coverage_score=site.coverage_score,
        reasons=[f"{item.factor}: {item.reason}" for item in strongest],
        watch_out=f"{weakest.factor}: {weakest.reason}" if weakest is not None else None,
        factor_scores={item.factor: item.normalized_score for item in site.factors},
        demand_mode=site.demand.mode,
        demand_unit=site.demand.unit,
    )


def check_budget(ctx: MetroContext, profile: Profile, budget: Budget | None) -> BudgetCheck:
    """Compare the budget with the metro rent range and the profile's build-out range (it cannot filter tracts)."""
    notes = [
        "The budget does not filter or rank tracts: there is no tract-level rent or build-out cost data. "
        "It is checked against the metro rent range and the profile's build-out range only."
    ]
    if budget is None:
        return BudgetCheck(filters_tracts=False, notes=["No budget given.", *notes])
    rent = ctx.fabric.metro_inputs(profile)["commercial_rent"]
    if budget.max_monthly_rent_usd is not None and rent.low is not None and rent.high is not None:
        limit = budget.max_monthly_rent_usd
        where = "below" if limit < rent.low else ("above" if limit > rent.high else "inside")
        notes.append(
            f"Rent budget ${limit:,.0f}/month is {where} the metro rent range ${rent.low:,.0f}-${rent.high:,.0f} "
            f"({rent.status}: {rent.reason})."
        )
    if budget.max_build_out_usd is not None:
        low = sum(item.cost.bounds()[0] for item in profile.capex)
        high = sum(item.cost.bounds()[1] for item in profile.capex)
        limit = budget.max_build_out_usd
        where = "below" if limit < low else ("above" if limit > high else "inside")
        notes.append(
            f"Build-out budget ${limit:,.0f} is {where} the sum of the profile's capex ranges ${low:,.0f}-${high:,.0f} "
            "(ASSUMPTION ranges; contractor quotes replace them)."
        )
    return BudgetCheck(filters_tracts=False, notes=notes)


def geojson_layer(areas: list[SuggestedArea], market_miles: float, data_label: str) -> dict:
    """A GeoJSON FeatureCollection: a Point per suggested tract centroid and an approximate market-ring circle."""
    features = []
    for area in areas:
        properties = {
            "rank": area.rank,
            "geoid": area.geoid,
            "overall_score": area.overall_score,
            "coverage_score": area.coverage_score,
            "district": area.district,
            "reasons": area.reasons,
            "data_label": data_label,
        }
        point = {"type": "Point", "coordinates": [round(area.lon, 6), round(area.lat, 6)]}
        features.append({"type": "Feature", "geometry": point, "properties": {"kind": "tract_centroid", **properties}})
        ring = {"type": "Polygon", "coordinates": [circle_polygon(area.lat, area.lon, market_miles)]}
        ring_properties = {
            "kind": "market_ring",
            "rank": area.rank,
            "geoid": area.geoid,
            "radius_miles": market_miles,
            "note": "approximate circle around the tract centroid; not a tract boundary",
            "data_label": data_label,
        }
        features.append({"type": "Feature", "geometry": ring, "properties": ring_properties})
    return {"type": "FeatureCollection", "features": features}


def suggest_notes(ctx: MetroContext, n_tracts: int) -> list[str]:
    """Labels and method notes shown with the suggestions."""
    mode, reason = ctx.mode()
    notes = []
    if ctx.fabric.data_label == SYNTHETIC_LABEL:
        notes.append("SYNTHETIC metro: generated data for demonstration and validation; not a real place.")
    if mode == "limited":
        notes.append(f"Limited mode: {reason}.")
    if n_tracts == 0:
        notes.append("No tracts are available for this metro, so no area can be suggested.")
    notes.append(
        "Tracts are ranked by the same overall score as score_site at the tract centroid. Tracts missing required "
        "data are excluded, not guessed. A score ranks locations within this metro; it does not predict sales."
    )
    return notes


def suggest_areas(
    profile: Profile | str,
    metro: MetroConfig | str,
    budget: Budget | None = None,
    *,
    top_n: int = TOP_N,
    context: MetroContext | None = None,
    live: PoliteClient | None = None,
    seed: int | None = None,
) -> Suggestions:
    """The top tracts of the metro for the business type (see the module docstring)."""
    profile, ctx = resolve(profile, metro, context, live, seed)
    rings = scaled_rings(profile, ctx.metro)
    reference = reference_for(ctx, profile, rings)
    overall, reasons = tract_overall_scores(profile, reference)
    order = ranked_indices(overall, reference.geoids)
    areas = []
    for rank, index in enumerate(order[:top_n], start=1):
        lat, lon = float(ctx.tracts.lats[index]), float(ctx.tracts.lons[index])
        areas.append(area_from_score(rank, score_site(profile, ctx.metro, lat, lon, context=ctx)))
    counts = Counter(reason for reason in reasons if reason is not None)
    excluded = [Exclusion(reason=reason, count=count) for reason, count in counts.most_common()]
    mode, mode_reason = ctx.mode()
    return Suggestions(
        profile_id=profile.id,
        metro_id=ctx.metro.id,
        metro_name=ctx.metro.name,
        data_label=ctx.fabric.data_label,
        mode=mode,
        mode_reason=mode_reason,
        n_tracts=len(reference.geoids),
        n_ranked=len(order),
        areas=areas,
        excluded=excluded,
        budget=check_budget(ctx, profile, budget),
        geojson=geojson_layer(areas, near_and_market(rings)[1], ctx.fabric.data_label),
        notes=suggest_notes(ctx, len(reference.geoids)),
    )
