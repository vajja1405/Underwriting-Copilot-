"""Validation of SiteIQ on SYNTHETIC cities with known true demand, plus a sanity table for the real metros.

Read this first: the SYNTHETIC city (studio.data.synthetic_city) and SiteIQ were written by the same project author.
The city's hidden "true demand" uses a different formula from SiteIQ's scoring (gravity decay instead of rings,
saturating competition, interactions, a hidden neighborhood-appeal field and noise), so the check is not circular,
but it is still a check against the author's own idea of how demand works. It shows the method behaves sensibly; it
is NOT evidence of real-world accuracy, and no real outcome data exists to measure that.

What is measured, per business type, over several SYNTHETIC cities (seeds):
- Spearman rank correlation between SiteIQ's overall score and the true demand, over the tracts SiteIQ ranks;
- top-5 hit rate: the share of SiteIQ's 5 best tracts that are among the 20 best tracts by true demand (out of
  every tract of the city, including ones SiteIQ excluded for missing data);
- the same for two baselines: a random ranking (expected values computed exactly) and the profile's single
  highest-weight factor used alone ("single-factor baseline");
- the demand estimate's mode (demand.py) against the true demand, as a secondary check;
- each factor's own Spearman correlation (a diagnostic of which factors carry signal).

Confidence intervals (1,000 resamples, seed 42, percentile method):
- correlations: a two-level bootstrap. Each resample draws the cities with replacement and, inside each drawn city,
  its tracts with replacement; the correlation is computed per city and averaged. The draws depend only on the seed
  and the city sizes, so SiteIQ, the baseline and their difference are resampled with the same draws (paired).
- hit rates: the cities are resampled (a top-5 list cannot be resampled tract by tract); with few cities this
  interval is coarse.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import rankdata

from uwcopilot.studio.data.fabric import Snapshot, SyntheticFabric, coverage_share, get_fabric
from uwcopilot.studio.data.synthetic_city import SyntheticCity, generate_city
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.models import Profile
from uwcopilot.studio.siteiq.context import MetroContext
from uwcopilot.studio.siteiq.demand import FORMULA_TERMS, term_value
from uwcopilot.studio.siteiq.reference import MetroReference, reference_for, site_factors
from uwcopilot.studio.siteiq.suggest import factor_scores, ranked_indices, tract_overall_scores
from uwcopilot.studio.siteiq.trade_area import scaled_rings

TOP_K = 5
TRUE_TOP = 20
N_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
ALPHA = 0.05
SYNTHETIC_STATEMENT = (
    "Validation on SYNTHETIC cities whose generator was written by the same project author as SiteIQ. The true "
    "demand uses a different formula from SiteIQ's score (gravity decay, saturating competition, interactions, a "
    "hidden appeal field, noise), so the check is not circular, but it is not evidence of real-world accuracy."
)


@dataclass
class ProfileRun:
    """One business type in one SYNTHETIC city: SiteIQ, baselines and truth over the ranked tracts."""

    seed: int
    profile_id: str
    n_tracts: int
    geoids: list[str]
    arrays: dict[str, np.ndarray]
    true_top: set[str]
    baseline_factor: str
    exclusions: dict[str, int] = field(default_factory=dict)
    factor_arrays: dict[str, np.ndarray] = field(default_factory=dict)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation (average ranks for ties); NaN when either side is constant."""
    rank_x, rank_y = rankdata(x), rankdata(y)
    if np.std(rank_x) == 0 or np.std(rank_y) == 0:
        return float("nan")
    return float(np.corrcoef(rank_x, rank_y)[0, 1])


def top_k_hits(geoids: list[str], scores: np.ndarray, true_top: set[str], k: int = TOP_K) -> int:
    """How many of the k best tracts by score (ties broken by tract id) are in the true top set."""
    order = sorted(range(len(geoids)), key=lambda index: (-scores[index], geoids[index]))
    return sum(geoids[index] in true_top for index in order[:k])


def synthetic_context(
    metro: MetroConfig, seed: int, profiles: dict[str, Profile], n_tracts: int | None = None
) -> tuple[MetroContext, SyntheticCity]:
    """(MetroContext, SyntheticCity) for one SYNTHETIC city of the metro, generated with this seed."""
    settings = metro.synthetic
    if settings is None:
        raise ValueError(f"{metro.id} is not a SYNTHETIC metro")
    if n_tracts is not None:
        settings = settings.model_copy(update={"n_tracts": n_tracts})
        metro = metro.model_copy(update={"synthetic": settings})
    city = generate_city(
        metro.id,
        seed,
        settings.n_tracts,
        settings.origin_lat,
        settings.origin_lon,
        settings.extent_miles,
        profiles,
    )
    return MetroContext(metro=metro, fabric=SyntheticFabric(metro=metro, city=city)), city


def count_reasons(reasons: list[str | None]) -> dict[str, int]:
    """{exclusion reason: number of tracts} (ranked tracts have reason None and are not counted)."""
    counts: dict[str, int] = {}
    for reason in reasons:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def baseline_factor(profile: Profile) -> str:
    """The profile's highest-weight site factor (ties: the first listed), used alone as a baseline."""
    names = site_factors(profile)
    return max(names, key=lambda name: profile.design.location_weights[name].weight)


def demand_modes(profile: Profile, reference: MetroReference) -> np.ndarray:
    """The demand estimate's mode at every reference tract (NaN where a term's data is UNKNOWN or not measured)."""
    count = len(reference.geoids)
    rates = {name: quantity.point() for name, quantity in profile.demand.inputs.items()}
    total = np.zeros(count)
    for term in FORMULA_TERMS[profile.demand.formula]:
        if term.data_factor is None:
            total += term_value(term, 1.0, rates)
        elif term.data_factor in reference.values:
            total += reference.values[term.data_factor] * term_value(term, 1.0, rates)
        else:
            return np.full(count, np.nan)
    return total


def local_street_share(ctx: MetroContext) -> float:
    """Share of tract centroids whose nearest road is the generator's own local street (route 'SYN-LOCAL-...').

    A diagnostic for drive-by businesses: the SYNTHETIC city runs one low-traffic local street through every tract
    centroid, so 'the nearest road' at a centroid is usually that street rather than the busy road nearby.
    """
    hits = 0
    for index in range(len(ctx.tracts)):
        road = ctx.nearest_road(float(ctx.tracts.lats[index]), float(ctx.tracts.lons[index]))
        hits += road.segment is not None and (road.segment.route_name or "").startswith("SYN-LOCAL")
    return hits / max(len(ctx.tracts), 1)


def evaluate_profile(ctx: MetroContext, city: SyntheticCity, profile: Profile) -> ProfileRun:
    """SiteIQ's ranking of every tract of one city for one business type, with the baselines and the truth."""
    rings = scaled_rings(profile, ctx.metro)
    reference = reference_for(ctx, profile, rings)
    overall, reasons = tract_overall_scores(profile, reference)
    ranked = ranked_indices(overall, reference.geoids)
    truth_all = city.true_demand[profile.id]
    true_order = sorted(truth_all, key=lambda geoid: (-truth_all[geoid], geoid))
    scores = factor_scores(profile, reference)
    chosen = baseline_factor(profile)
    geoids = [reference.geoids[index] for index in ranked]
    arrays = {
        "siteiq": overall[ranked],
        "truth": np.array([truth_all[geoid] for geoid in geoids]),
        "baseline": scores[chosen][ranked],
        "demand_mode": demand_modes(profile, reference)[ranked],
    }
    return ProfileRun(
        seed=city.seed,
        profile_id=profile.id,
        n_tracts=len(reference.geoids),
        geoids=geoids,
        arrays=arrays,
        true_top=set(true_order[:TRUE_TOP]),
        baseline_factor=chosen,
        exclusions=count_reasons(reasons),
        factor_arrays={name: values[ranked] for name, values in scores.items()},
    )


def evaluate_city(
    metro: MetroConfig, seed: int, profiles: dict[str, Profile], n_tracts: int | None = None
) -> list[ProfileRun]:
    """Every business type in one SYNTHETIC city."""
    ctx, city = synthetic_context(metro, seed, profiles, n_tracts)
    return [evaluate_profile(ctx, city, profile) for profile in profiles.values()]


def interval(values: np.ndarray, estimate: float | None, seed: int, n_resamples: int, method: str) -> dict[str, Any]:
    """A percentile confidence interval from resampled values (NaN resamples are left out and counted)."""
    finite = values[np.isfinite(values)]
    low = float(np.percentile(finite, 100 * ALPHA / 2)) if len(finite) else None
    high = float(np.percentile(finite, 100 * (1 - ALPHA / 2))) if len(finite) else None
    return {
        "estimate": estimate,
        "ci_low": low,
        "ci_high": high,
        "n_resamples": n_resamples,
        "n_valid_resamples": int(len(finite)),
        "seed": seed,
        "alpha": ALPHA,
        "method": method,
    }


def finite_or_none(value: float) -> float | None:
    """The value, or None when it is NaN or infinite (JSON has no NaN)."""
    return float(value) if np.isfinite(value) else None


def mean_or_none(values: list[float]) -> float | None:
    """Mean of the finite values, or None when there is none."""
    finite = [value for value in values if np.isfinite(value)]
    return float(np.mean(finite)) if finite else None


def two_level_bootstrap(
    runs: list[ProfileRun],
    statistic: Callable[[dict[str, np.ndarray]], float],
    n_resamples: int = N_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Mean over cities of a per-city statistic, with a cities-then-tracts bootstrap interval."""
    estimate = mean_or_none([statistic(run.arrays) for run in runs])
    rng = np.random.default_rng(seed)
    resampled = np.empty(n_resamples)
    for draw in range(n_resamples):
        cities = rng.integers(0, len(runs), size=len(runs))
        values = []
        for city in cities:
            arrays = runs[city].arrays
            rows = rng.integers(0, len(arrays["truth"]), size=len(arrays["truth"]))
            values.append(statistic({name: array[rows] for name, array in arrays.items()}))
        mean = mean_or_none(values)
        resampled[draw] = np.nan if mean is None else mean
    return interval(resampled, estimate, seed, n_resamples, "two-level bootstrap (cities, then tracts), percentile")


def city_bootstrap(values: list[float], n_resamples: int = N_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Mean over cities with a bootstrap interval that resamples the cities."""
    array = np.array(values, dtype=float)
    rng = np.random.default_rng(seed)
    resampled = np.array([array[rng.integers(0, len(array), size=len(array))].mean() for _ in range(n_resamples)])
    return interval(resampled, float(array.mean()), seed, n_resamples, "bootstrap over cities, percentile")


def rho(name: str) -> Callable[[dict[str, np.ndarray]], float]:
    """Statistic: Spearman correlation of one score array with the truth."""
    return lambda arrays: spearman(arrays[name], arrays["truth"])


def rho_difference(arrays: dict[str, np.ndarray]) -> float:
    """Statistic: SiteIQ's Spearman correlation minus the single-factor baseline's."""
    return spearman(arrays["siteiq"], arrays["truth"]) - spearman(arrays["baseline"], arrays["truth"])


def summarize_profile(runs: list[ProfileRun], n_resamples: int = N_RESAMPLES) -> dict[str, Any]:
    """The headline numbers of one business type over every city."""
    hits = [top_k_hits(run.geoids, run.arrays["siteiq"], run.true_top) / TOP_K for run in runs]
    baseline_hits = [top_k_hits(run.geoids, run.arrays["baseline"], run.true_top) / TOP_K for run in runs]
    random_hits = [len(run.true_top & set(run.geoids)) / max(len(run.geoids), 1) for run in runs]
    demand_defined = any(np.isfinite(spearman(run.arrays["demand_mode"], run.arrays["truth"])) for run in runs)
    factor_names = list(runs[0].factor_arrays)
    return {
        "baseline_factor": runs[0].baseline_factor,
        "siteiq_spearman": two_level_bootstrap(runs, rho("siteiq"), n_resamples),
        "baseline_single_factor_spearman": two_level_bootstrap(runs, rho("baseline"), n_resamples),
        "siteiq_minus_baseline_spearman": two_level_bootstrap(runs, rho_difference, n_resamples),
        "random_expected_spearman": 0.0,
        "demand_estimate_spearman": (
            two_level_bootstrap(runs, rho("demand_mode"), n_resamples)
            if demand_defined
            else {"estimate": None, "note": "not defined: the estimate does not vary by site (no data in the formula)"}
        ),
        "siteiq_top5_hit_rate": city_bootstrap(hits, n_resamples),
        "baseline_top5_hit_rate": city_bootstrap(baseline_hits, n_resamples),
        "random_expected_top5_hit_rate": round(float(np.mean(random_hits)), 4),
        "factor_spearman_mean_over_cities": {
            name: mean_or_none([spearman(run.factor_arrays[name], run.arrays["truth"]) for run in runs])
            for name in factor_names
        },
        "per_city": [
            {
                "seed": run.seed,
                "n_tracts": run.n_tracts,
                "n_ranked": len(run.geoids),
                "excluded": run.exclusions,
                "siteiq_spearman": finite_or_none(spearman(run.arrays["siteiq"], run.arrays["truth"])),
                "baseline_spearman": finite_or_none(spearman(run.arrays["baseline"], run.arrays["truth"])),
                "siteiq_top5_hits": top_k_hits(run.geoids, run.arrays["siteiq"], run.true_top),
                "baseline_top5_hits": top_k_hits(run.geoids, run.arrays["baseline"], run.true_top),
            }
            for run in runs
        ],
    }


def real_metro_sanity(metro_id: str, profiles: dict[str, Profile]) -> dict[str, Any]:
    """Per business type: can SiteIQ rank this real metro's tracts from its snapshot? (No accuracy claim.)"""
    fabric = get_fabric(metro_id)
    mode, mode_reason = fabric.mode(profiles)
    snapshot = getattr(fabric, "snapshot", None)
    rows: dict[str, Any] = {}
    ctx: MetroContext | None = None
    for profile_id, profile in profiles.items():
        coverage_rows = fabric.coverage(profile)
        share = coverage_share(coverage_rows)
        if share == 0:
            unknown = next((row.reason for row in coverage_rows if row.weight > 0 and row.status == "UNKNOWN"), "")
            rows[profile_id] = {
                "status": "not measured: no real tract data for this business type in the snapshot",
                "coverage_share": share,
                "first_missing_reason": unknown,
            }
            continue
        ctx = ctx or MetroContext(metro=fabric.metro, fabric=fabric)
        reference = reference_for(ctx, profile, scaled_rings(profile, fabric.metro))
        overall, reasons = tract_overall_scores(profile, reference)
        ranked = overall[~np.isnan(overall)]
        rows[profile_id] = {
            "status": (
                "measured (counts and score spread only; no accuracy claim)"
                if len(ranked)
                else "not measured: no tract could be ranked (required data UNKNOWN)"
            ),
            "coverage_share": share,
            "tracts_ranked": int(len(ranked)),
            "excluded": count_reasons(reasons),
            "score_p10_p50_p90": [float(np.percentile(ranked, q)) for q in (10, 50, 90)] if len(ranked) else None,
        }
    return {
        "metro_id": metro_id,
        "name": fabric.metro.name,
        "mode": mode,
        "mode_reason": mode_reason,
        "tracts_in_snapshot": len(fabric.tract_ids()),
        "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot is not None else None,
        "snapshot_fingerprint": snapshot.fingerprint() if snapshot is not None else None,
        "snapshot_steps_not_ok": steps_not_ok(snapshot),
        "by_business_type": rows,
    }


def steps_not_ok(snapshot: Snapshot | None) -> dict[str, str]:
    """{step: 'status: detail'} for every snapshot step that did not succeed, copied from the snapshot manifest."""
    if snapshot is None:
        return {}
    return {
        str(step.get("name")): f"{step.get('status')}: {step.get('detail', '')}"
        for step in snapshot.manifest.get("steps", [])
        if step.get("status") != "ok"
    }
