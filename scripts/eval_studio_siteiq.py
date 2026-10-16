"""Write results/studio_siteiq_metrics.json: SiteIQ checked against the hidden true demand of SYNTHETIC cities.

    uv run python scripts/eval_studio_siteiq.py          # or: make studio-siteiq

For each of N_CITIES SYNTHETIC cities (seeds after the demo city's seed, so the demo city is not one of them) and
each business type, SiteIQ ranks every tract; the script compares that ranking with the city's hidden true demand
(Spearman correlation, top-5 hit rate) and with two baselines (random ranking, the profile's single highest-weight
factor). Intervals: 1,000 bootstrap resamples, seed 42 (see studio.siteiq.validation for the method).

Read this before quoting any number: the SYNTHETIC city and SiteIQ were written by the same author. The result shows
how the method behaves in a world built on the author's assumptions; it is not evidence of real-world accuracy.

The real metros get a sanity table only (can their tracts be ranked from the snapshot on disk?), with no accuracy
claim. The script reads only configs, snapshots on disk and generated cities; it never calls the network.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.studio import DISCLAIMER, SYNTHETIC_LABEL
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.metros.loader import METROS_DIR, list_metros, load_metro
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.loader import PROFILES_DIR, load_all_profiles
from uwcopilot.studio.siteiq.models import SCORING_METHOD
from uwcopilot.studio.siteiq.reference import MIN_REFERENCE_VALUES
from uwcopilot.studio.siteiq.scoring import MIN_SCORED_WEIGHT
from uwcopilot.studio.siteiq.validation import (
    BOOTSTRAP_SEED,
    N_RESAMPLES,
    SYNTHETIC_STATEMENT,
    TOP_K,
    TRUE_TOP,
    ProfileRun,
    evaluate_profile,
    local_street_share,
    real_metro_sanity,
    summarize_profile,
    synthetic_context,
)

OUTPUT = RESULTS_DIR / "studio_siteiq_metrics.json"
GENERATED_BY = "scripts/eval_studio_siteiq.py"
SYNTHETIC_METRO = "synthetic_metro"
N_CITIES = 10


def city_seeds(metro: MetroConfig, n_cities: int) -> list[int]:
    """Seeds of the validation cities: the n seeds after the demo city's seed."""
    if metro.synthetic is None:
        raise ValueError(f"{metro.id} is not a SYNTHETIC metro")
    return [metro.synthetic.seed + offset for offset in range(1, n_cities + 1)]


def synthetic_runs(
    metro: MetroConfig, seeds: list[int], n_tracts: int | None
) -> tuple[dict[str, list[ProfileRun]], list[dict[str, Any]]]:
    """({profile id: one run per city}, per-city diagnostics)."""
    profiles = load_all_profiles()
    runs: dict[str, list[ProfileRun]] = {profile_id: [] for profile_id in profiles}
    diagnostics = []
    for seed in seeds:
        ctx, city = synthetic_context(metro, seed, profiles, n_tracts)
        for profile_id, profile in profiles.items():
            runs[profile_id].append(evaluate_profile(ctx, city, profile))
        diagnostics.append(
            {
                "seed": seed,
                "n_tracts": len(ctx.tracts),
                "share_of_tract_centroids_whose_nearest_road_is_the_generated_local_street": round(
                    local_street_share(ctx), 4
                ),
            }
        )
    return runs, diagnostics


def build_report(n_cities: int = N_CITIES, n_tracts: int | None = None, n_resamples: int = N_RESAMPLES) -> dict:
    """The report body (without the provenance block)."""
    metro = load_metro(SYNTHETIC_METRO)
    seeds = city_seeds(metro, n_cities)
    started = time.perf_counter()
    runs, diagnostics = synthetic_runs(metro, seeds, n_tracts)
    by_type = {profile_id: summarize_profile(items, n_resamples) for profile_id, items in runs.items()}
    elapsed = time.perf_counter() - started
    profiles = load_all_profiles()
    real = {
        metro_id: real_metro_sanity(metro_id, profiles)
        for metro_id in list_metros()
        if load_metro(metro_id).data_label == "PUBLIC"
    }
    return {
        "description": (
            "SiteIQ (Studio S2) validated on SYNTHETIC cities with a hidden true demand per tract and business type. "
            "siteiq_spearman: rank correlation between SiteIQ's overall score and the true demand over the tracts "
            "SiteIQ ranks; siteiq_top5_hit_rate: share of SiteIQ's 5 best tracts that are among the 20 best tracts by "
            "true demand (out of every tract of the city). Baselines: random ranking (expected values) and the "
            "profile's single highest-weight factor used alone."
        ),
        "data_label": SYNTHETIC_LABEL,
        "statement": SYNTHETIC_STATEMENT,
        "disclaimer": DISCLAIMER,
        "method": {
            "scoring_method": SCORING_METHOD,
            "weights": "the profiles' DESIGN_CHOICE weights, unchanged (not tuned on the SYNTHETIC cities)",
            "min_scored_weight_share": MIN_SCORED_WEIGHT,
            "min_reference_values": MIN_REFERENCE_VALUES,
            "metro": SYNTHETIC_METRO,
            "city_seeds": seeds,
            "demo_city_seed_excluded": metro.synthetic.seed if metro.synthetic else None,
            "n_tracts_per_city": n_tracts or (metro.synthetic.n_tracts if metro.synthetic else None),
            "top_k": TOP_K,
            "true_top": TRUE_TOP,
            "bootstrap": {"n_resamples": n_resamples, "seed": BOOTSTRAP_SEED, "ci": "95% percentile"},
            "elapsed_seconds_synthetic_part": round(elapsed, 1),
            "elapsed_note": "wall-clock time on a shared machine (several agents run at once); not a benchmark",
        },
        "by_business_type": by_type,
        "diagnostics": diagnostics,
        "real_metro_sanity": real,
    }


def config_inputs() -> list[Path]:
    """The config files the validation reads (SYNTHETIC metro, six profiles); provenance stores their SHA-256."""
    return [METROS_DIR / f"{SYNTHETIC_METRO}.yaml", *sorted(PROFILES_DIR.glob("*.yaml"))]


def headline_lines(report: dict) -> list[str]:
    """One printed line per business type, copied from the report."""

    def fmt(block: dict) -> str:
        if block.get("estimate") is None:
            return "n/a"
        return f"{block['estimate']:.3f} [{block['ci_low']:.3f}, {block['ci_high']:.3f}]"

    lines = []
    for profile_id, block in report["by_business_type"].items():
        lines.append(
            f"{profile_id:<18} rho={fmt(block['siteiq_spearman'])} "
            f"baseline({block['baseline_factor']})={fmt(block['baseline_single_factor_spearman'])} "
            f"diff={fmt(block['siteiq_minus_baseline_spearman'])} "
            f"top5={fmt(block['siteiq_top5_hit_rate'])} baseline_top5={fmt(block['baseline_top5_hit_rate'])} "
            f"random_top5={block['random_expected_top5_hit_rate']}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    """Run the validation, add provenance, write the JSON and print the headline."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", type=int, default=N_CITIES, help="number of SYNTHETIC cities (seeds)")
    parser.add_argument("--n-tracts", type=int, default=None, help="tracts per city (default: the metro config)")
    parser.add_argument("--resamples", type=int, default=N_RESAMPLES, help="bootstrap resamples")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    report = build_report(args.cities, args.n_tracts, args.resamples)
    snapshots = {metro_id: block["snapshot_fingerprint"] for metro_id, block in report["real_metro_sanity"].items()}
    report["provenance"] = run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        eval_sets=config_inputs(),
        data_snapshots={"synthetic_city_seeds": report["method"]["city_seeds"], "studio_snapshots": snapshots},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    for line in headline_lines(report):
        print(line)
    print(f"Wrote {display_path(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
