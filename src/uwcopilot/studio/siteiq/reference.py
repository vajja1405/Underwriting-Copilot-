"""Reference distributions: each factor measured at every tract location of the metro, for percentile ranks.

Normalization method (stated in every FactorScore): a site's raw value is ranked against the same factor measured,
in the same way, at every tract centroid of the metro. The percentile is the mid-rank

    percentile = 100 x (number of reference values below the site's value + half the number equal to it) / n

so a site exactly in the middle of the metro scores 50 and ties share a percentile. Because every metro is ranked
against itself, a score of 80 means "better than most locations in this metro", in New York or in Houston alike.

Real metros: tract factors come from the snapshot for every tract. Places and roads come from the snapshot's stored
layers (Overture places and the HPMS road layer, see data/snapshot_layers.py), so they are measured at every tract
centroid exactly as at a site. Only when a snapshot lacks those layers and a live client is given are the layers
live per site; they are then measured at a seeded sample of tract centroids (live_sample, default 0 = none). A factor
with fewer than MIN_REFERENCE_VALUES reference values is not normalized: its score is left out, never guessed.
"""

import hashlib
from dataclasses import dataclass

import numpy as np

from uwcopilot.studio.profiles.factors import LOCATION_FACTORS
from uwcopilot.studio.profiles.models import Profile
from uwcopilot.studio.siteiq.context import MetroContext
from uwcopilot.studio.siteiq.factors import measure_point

# A design choice: fewer reference values than this and a percentile would be too coarse to mean anything.
MIN_REFERENCE_VALUES = 20
SITE_SCOPES: tuple[str, ...] = ("tract", "poi_layer", "road_layer")


@dataclass(frozen=True)
class MetroReference:
    """One profile's factors measured at reference locations (tract centroids) of one metro. NaN = UNKNOWN."""

    profile_id: str
    rings: tuple[float, ...]
    geoids: list[str]
    values: dict[str, np.ndarray]
    saturation: np.ndarray
    sorted_known: dict[str, np.ndarray]
    points_note: str

    def size(self, factor: str) -> int:
        """How many reference locations have a value for the factor."""
        known = self.sorted_known.get(factor)
        return 0 if known is None else len(known)

    def percentile(self, factor: str, value: float) -> float | None:
        """Mid-rank percentile of value among the factor's reference values (None when too few exist)."""
        if self.size(factor) < MIN_REFERENCE_VALUES:
            return None
        return float(percentile_ranks(self.sorted_known[factor], np.array([value]))[0])

    def percentiles(self, factor: str) -> np.ndarray:
        """Mid-rank percentile of every reference location's own value (NaN where UNKNOWN or too few values)."""
        values = self.values.get(factor)
        if values is None or self.size(factor) < MIN_REFERENCE_VALUES:
            return np.full(len(self.geoids), np.nan)
        return percentile_ranks(self.sorted_known[factor], values)

    def median_saturation(self) -> float | None:
        """Metro median of competitors per 10,000 residents (None when unknown everywhere)."""
        known = self.saturation[~np.isnan(self.saturation)]
        return float(np.median(known)) if len(known) else None


def percentile_ranks(sorted_known: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Mid-rank percentiles of values against sorted reference values; NaN stays NaN."""
    below = np.searchsorted(sorted_known, values, side="left")
    at_or_below = np.searchsorted(sorted_known, values, side="right")
    ranks = 100.0 * (below + 0.5 * (at_or_below - below)) / len(sorted_known)
    return np.where(np.isnan(values), np.nan, ranks)


def site_factors(profile: Profile) -> list[str]:
    """The profile's weighted factors that vary by site (tract, POI or road layers), in YAML order."""
    return [name for name in profile.design.location_weights if LOCATION_FACTORS[name].scope in SITE_SCOPES]


def sample_indices(count: int, live_sample: int, seed: int) -> set[int]:
    """A seeded sample of reference locations for live layers (all of them when the layers are local)."""
    if live_sample <= 0:
        return set()
    rng = np.random.default_rng(seed)
    return set(rng.choice(count, size=min(live_sample, count), replace=False).tolist())


def build_reference(
    ctx: MetroContext, profile: Profile, rings: tuple[float, ...], live_sample: int = 0, seed: int = 0
) -> MetroReference:
    """Measure the profile's site factors at every tract centroid of the metro."""
    count = len(ctx.tracts)
    local = ctx.point_layers_local
    sampled = set(range(count)) if local else sample_indices(count, live_sample, seed)
    factors = site_factors(profile)
    values = {factor: np.full(count, np.nan) for factor in factors}
    saturation = np.full(count, np.nan)
    for index in range(count):
        lat, lon = float(ctx.tracts.lats[index]), float(ctx.tracts.lons[index])
        measured = measure_point(ctx, profile, lat, lon, rings, point_layers=index in sampled)
        for factor in factors:
            value = measured[factor].value
            values[factor][index] = np.nan if value is None else value
        if "competition" in measured:
            ratio = measured["competition"].details.get("per_10k_residents_market")
            saturation[index] = np.nan if ratio is None else ratio
    sorted_known = {factor: np.sort(array[~np.isnan(array)]) for factor, array in values.items()}
    if local:
        note = f"every tract centroid of the metro ({count})"
    else:
        note = f"every tract centroid for tract factors ({count}); {len(sampled)} seeded centroids for live layers"
    return MetroReference(
        profile_id=profile.id,
        rings=rings,
        geoids=list(ctx.tracts.ids),
        values=values,
        saturation=saturation,
        sorted_known=sorted_known,
        points_note=note,
    )


def reference_for(ctx: MetroContext, profile: Profile, rings: tuple[float, ...]) -> MetroReference:
    """The profile's reference in this metro, built once and kept in the context."""
    fingerprint = hashlib.sha256(profile.model_dump_json().encode("utf-8")).hexdigest()
    key = (profile.id, rings, fingerprint)
    if key not in ctx.references:
        ctx.references[key] = build_reference(ctx, profile, rings)
    return ctx.references[key]
