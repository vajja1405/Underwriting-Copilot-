"""score_site: score one candidate site for one business type in one metro (Studio S2).

    from uwcopilot.studio.siteiq import score_site
    result = score_site("car_wash", "synthetic_metro", lat, lon)
    result.overall_score, result.coverage_score, result.demand.mode

How a score is made:
1. Measure every factor the profile needs at the site (factors.py), each with status, reason and sources.
2. Normalize each weighted factor by its percentile rank among the same factor measured at every tract location of
   the metro (reference.py); for "lower is better" factors the score is 100 minus the percentile.
3. overall_score = the weighted mean of the factors that have a score AND are backed by data (DATA, SYNTHETIC or
   FOUNDER_INPUT), with the weights rescaled over those factors. If they carry less than MIN_SCORED_WEIGHT of the
   profile's weight, there is no overall score: too little is known to rank the site.
4. coverage_score = the weighted share of the profile's required factors that are backed by data at this site
   (the same rule as the S1 coverage report: demand-only inputs count with the average weight).
5. The demand estimate (demand.py) and notes for every ASSUMPTION or UNKNOWN input.

The weights are the profile's DESIGN_CHOICE weights (config/studio/business_types/*.yaml); SiteIQ does not change
them. A score ranks locations within one metro; it is not a probability and not a prediction of sales.
"""

from uwcopilot.studio import SYNTHETIC_LABEL
from uwcopilot.studio.data.fabric import required_factors
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.factors import DEMAND_FORMULAS, LOCATION_FACTORS
from uwcopilot.studio.profiles.loader import load_profile
from uwcopilot.studio.profiles.models import FactorWeight, Profile
from uwcopilot.studio.siteiq.context import MetroContext, SiteOutsideMetro, build_context, metro_context
from uwcopilot.studio.siteiq.demand import FORMULA_TERMS, estimate_demand
from uwcopilot.studio.siteiq.enhancements import demand_area, license_context
from uwcopilot.studio.siteiq.factors import RawFactor, area_mask, measure_point, measure_tract_factor, tract_features_in
from uwcopilot.studio.siteiq.models import (
    COVERED_STATUSES,
    DemandEstimate,
    FactorScore,
    SiteInputs,
    SiteScore,
)
from uwcopilot.studio.siteiq.reference import MIN_REFERENCE_VALUES, MetroReference, reference_for
from uwcopilot.studio.siteiq.trade_area import Area, scaled_rings

# A design choice: below this share of the profile's weight, there is no overall score.
MIN_SCORED_WEIGHT = 0.5
WEIGHT_TOLERANCE = 1e-9
# Every normalization text of a factor without a score starts with this.
NOT_NORMALIZED = "not normalized: "


def resolve(
    profile: Profile | str,
    metro: MetroConfig | str,
    context: MetroContext | None,
    live: PoliteClient | None,
    seed: int | None,
) -> tuple[Profile, MetroContext]:
    """The profile (with the metro's overrides when given by id) and the metro context to score in."""
    metro_id = metro if isinstance(metro, str) else metro.id
    if context is None:
        context = build_context(metro_id, live=live, seed=seed) if live is not None else metro_context(metro_id, seed)
    if context.metro.id != metro_id:
        raise ValueError(f"the context is for {context.metro.id!r}, not {metro_id!r}")
    resolved = load_profile(profile, metro_id=metro_id) if isinstance(profile, str) else profile
    return resolved, context


def normalize(
    name: str, weight: FactorWeight, raw: RawFactor, reference: MetroReference
) -> tuple[float | None, float | None, str, int]:
    """(score 0-100, percentile, method text, reference size) for one factor; score None when it cannot be ranked."""
    if name == "commercial_rent":
        return normalize_rent(weight, raw)
    size = reference.size(name)
    if raw.value is None:
        return None, None, NOT_NORMALIZED + "the value is UNKNOWN", size
    percentile = reference.percentile(name, raw.value)
    if percentile is None:
        method = (
            f"{NOT_NORMALIZED}only {size} metro locations have a value (a percentile needs at least "
            f"{MIN_REFERENCE_VALUES}; reference: {reference.points_note})"
        )
        return None, None, method, size
    method = f"percentile rank among {size} tract-centroid locations in this metro (mid-rank for ties)"
    if weight.direction == "lower_is_better":
        return round(100 - percentile, 2), round(percentile, 2), method + "; lower is better: 100 - percentile", size
    return round(percentile, 2), round(percentile, 2), method, size


def normalize_rent(weight: FactorWeight, raw: RawFactor) -> tuple[float | None, float | None, str, int]:
    """A founder's quote is placed within the metro rent range; without a quote rent cannot rank sites."""
    low, high = raw.details.get("metro_low"), raw.details.get("metro_high")
    if raw.status != "FOUNDER_INPUT" or raw.value is None:
        return None, None, NOT_NORMALIZED + "one metro-wide value, the same for every site (enter a rent quote)", 0
    if low is None or high is None or high <= low:
        return None, None, NOT_NORMALIZED + "the metro has no rent range to compare the quote with", 0
    position = min(max((raw.value - low) / (high - low), 0.0), 1.0)
    score = 100 * (1 - position) if weight.direction == "lower_is_better" else 100 * position
    method = (
        f"position of the quote within the metro rent range ${low:,.0f}-${high:,.0f}/month "
        f"(status {raw.details.get('metro_status')}: not market data); the low end scores 100"
    )
    return round(score, 2), None, method, 0


def unscored_why(method: str) -> str:
    """Why a factor with a value has no score, from its normalization text ('not normalized: <why>')."""
    return method.removeprefix(NOT_NORMALIZED)


def factor_reason(
    raw: RawFactor,
    weight: FactorWeight,
    score: float | None,
    percentile: float | None,
    size: int,
    extra: str,
    method: str,
) -> str:
    """One plain-language sentence or two: what was measured, and how it compares within the metro."""
    if raw.value is None:
        return f"UNKNOWN: {raw.reason}. Left out of the overall score (never filled in)."
    if score is None:
        return f"{raw.reason}. Not scored: {unscored_why(method)}; left out of the overall score."
    if percentile is None:
        return f"{raw.reason}. Scores {score:.0f} of 100 against the metro range."
    better = "fewer is better" if weight.direction == "lower_is_better" else "more is better"
    text = f"{raw.reason}{extra}. Percentile {percentile:.0f} of {size} locations in this metro ({better}): "
    return text + f"scores {score:.0f} of 100."


def competition_extra(raw: RawFactor, reference: MetroReference) -> str:
    """Saturation per 10,000 residents compared with the metro median, for the competition reason."""
    saturation = raw.details.get("per_10k_residents_market")
    median = reference.median_saturation()
    if saturation is None or median is None:
        return ""
    return f"; {saturation:.2f} per 10,000 residents in the market ring vs a metro median of {median:.2f}"


def score_factor(name: str, weight: FactorWeight, raw: RawFactor, reference: MetroReference) -> FactorScore:
    """One factor's FactorScore. used_in_overall: it has a score and is backed by data."""
    score, percentile, method, size = normalize(name, weight, raw, reference)
    extra = competition_extra(raw, reference) if name == "competition" else ""
    return FactorScore(
        factor=name,
        description=LOCATION_FACTORS[name].description,
        unit=LOCATION_FACTORS[name].unit,
        raw_value=raw.value,
        status=raw.status,
        area=raw.area,
        sources=list(raw.sources),
        normalized_score=score,
        percentile=percentile,
        normalization=method,
        reference_size=size,
        weight=weight.weight,
        direction=weight.direction,
        used_in_overall=score is not None and raw.status in COVERED_STATUSES,
        reason=factor_reason(raw, weight, score, percentile, size, extra, method),
        details=raw.details,
    )


def score_factors(
    profile: Profile, raw: dict[str, RawFactor], reference: MetroReference
) -> tuple[list[FactorScore], float]:
    """(factor scores, share of the weight that has a usable score).

    When that share is below MIN_SCORED_WEIGHT, no factor is used: the site gets no overall score.
    """
    factors = [
        score_factor(name, weight, raw[name], reference) for name, weight in profile.design.location_weights.items()
    ]
    share = round(sum(item.weight for item in factors if item.used_in_overall), 4)
    if share < MIN_SCORED_WEIGHT - WEIGHT_TOLERANCE:
        factors = [item.model_copy(update={"used_in_overall": False}) for item in factors]
    return factors, share


def overall_score(factors: list[FactorScore]) -> float | None:
    """Weighted mean of the factors used in the overall score (weights rescaled over them), or None."""
    used = [item for item in factors if item.used_in_overall and item.normalized_score is not None]
    total = sum(item.weight for item in used)
    if not used or total <= 0:
        return None
    return round(sum(item.weight * item.normalized_score for item in used) / total, 1)


def site_coverage(profile: Profile, raw: dict[str, RawFactor]) -> float:
    """Weighted share of the profile's required factors backed by data at this site (S1's coverage rule)."""
    needed = required_factors(profile)
    positive = [weight for weight in needed.values() if weight > 0]
    fallback = sum(positive) / len(positive) if positive else 1.0
    weights = {factor: weight if weight > 0 else fallback for factor, weight in needed.items()}
    covered = sum(
        weights[factor] for factor in needed if raw[factor].value is not None and raw[factor].status in COVERED_STATUSES
    )
    return round(covered / sum(weights.values()), 4)


def demand_data(
    ctx: MetroContext, profile: Profile, raw: dict[str, RawFactor], area: Area, lat: float, lon: float
) -> dict[str, RawFactor]:
    """The data the demand formula reads: the ring measurements, or tract sums re-measured in a drive-time area."""
    if area.kind == "ring":
        return raw
    mask = area_mask(ctx, area, ctx.own_tract(lat, lon))
    features = tract_features_in(ctx, mask)
    data = dict(raw)
    for term in FORMULA_TERMS[profile.demand.formula]:
        factor = term.data_factor
        if factor is not None and LOCATION_FACTORS[factor].scope == "tract":
            data[factor] = measure_tract_factor(ctx, factor, area, mask, features)
    return data


def build_notes(
    ctx: MetroContext, factors: list[FactorScore], overall: float | None, share: float, demand: DemandEstimate
) -> list[str]:
    """Notes for the founder: SYNTHETIC or limited mode, every ASSUMPTION or UNKNOWN input, missing scores."""
    mode, reason = ctx.mode()
    notes: list[str] = []
    if ctx.fabric.data_label == SYNTHETIC_LABEL:
        notes.append("SYNTHETIC metro: generated data for demonstration and validation; not a real place.")
    if mode == "limited":
        notes.append(f"Limited mode: {reason}. Factors without data are UNKNOWN and left out.")
    for item in factors:
        if item.status == "UNKNOWN":
            notes.append(f"{item.factor} is UNKNOWN: left out of the overall score.")
        elif not item.used_in_overall and item.normalized_score is None:
            notes.append(f"{item.factor} is {item.status} but not scored: {unscored_why(item.normalization)}.")
    if overall is None:
        notes.append(
            f"No overall score: factors with data carry {share:.0%} of the weight; at least "
            f"{MIN_SCORED_WEIGHT:.0%} is needed."
        )
    if demand.basis == "UNKNOWN":
        notes.append("Demand estimate UNKNOWN: the data its formula needs is missing.")
    elif not demand.complete:
        notes.append("Demand estimate is incomplete (a term was left out): " + "; ".join(demand.missing))
    notes.append(
        f"Demand rates are the profile's ASSUMPTION ranges ({DEMAND_FORMULAS[demand.formula].description}); "
        "replace them with sourced values or the founder's own figures."
    )
    return notes


def score_site(
    profile: Profile | str,
    metro: MetroConfig | str,
    lat: float,
    lon: float,
    inputs: SiteInputs | None = None,
    *,
    context: MetroContext | None = None,
    live: PoliteClient | None = None,
    seed: int | None = None,
) -> SiteScore:
    """Score one site. Raises SiteOutsideMetro when the metro has tracts and none is near the site."""
    profile, ctx = resolve(profile, metro, context, live, seed)
    own = ctx.own_tract(lat, lon)
    if len(ctx.tracts) and own is None:
        raise SiteOutsideMetro(f"({lat:.5f}, {lon:.5f}) is not near any tract of {ctx.metro.name}")
    rings = scaled_rings(profile, ctx.metro)
    raw = measure_point(ctx, profile, lat, lon, rings, inputs=inputs, explain=True)
    reference = reference_for(ctx, profile, rings)
    factors, share = score_factors(profile, raw, reference)
    overall = overall_score(factors)
    client = live or ctx.live
    area, trade_area = demand_area(ctx.metro, rings, lat, lon, client)
    demand = estimate_demand(profile, demand_data(ctx, profile, raw, area, lat, lon), area.label)
    mode, mode_reason = ctx.mode()
    geoid = ctx.tracts.ids[own] if own is not None else None
    return SiteScore(
        profile_id=profile.id,
        metro_id=ctx.metro.id,
        metro_name=ctx.metro.name,
        data_label=ctx.fabric.data_label,
        mode=mode,
        mode_reason=mode_reason,
        lat=lat,
        lon=lon,
        label=inputs.label if inputs else None,
        tract_geoid=geoid,
        district=ctx.fabric.tract(geoid).district if geoid else None,
        trade_area=trade_area,
        factors=factors,
        overall_score=overall,
        scored_weight_share=share,
        coverage_score=site_coverage(profile, raw),
        demand=demand,
        metro_inputs=ctx.fabric.metro_inputs(profile),
        context=license_context(ctx.metro, profile, lat, lon, trade_area.near_miles, client),
        notes=build_notes(ctx, factors, overall, share, demand),
    )
