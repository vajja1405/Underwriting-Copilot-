"""SiteIQ validation: metrics, bootstrap, one small SYNTHETIC city end to end, the real-metro table and the script."""

import json
import math

import numpy as np
import pytest

import eval_studio_siteiq as script
from uwcopilot.studio.data.fabric import Snapshot, SnapshotFabric
from uwcopilot.studio.metros import list_metros, load_metro
from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.siteiq import validation
from uwcopilot.studio.siteiq.validation import (
    ProfileRun,
    baseline_factor,
    city_bootstrap,
    evaluate_city,
    real_metro_sanity,
    rho,
    spearman,
    steps_not_ok,
    summarize_profile,
    top_k_hits,
    two_level_bootstrap,
)

PROFILES = load_all_profiles()


def no_snapshot_fabric(metro_id: str) -> SnapshotFabric:
    """A real metro's fabric with no snapshot, whatever is on this machine's disk (CI has none)."""
    return SnapshotFabric(metro=load_metro(metro_id), snapshot=None)


def test_spearman_and_top_k() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(x, x * 10) == pytest.approx(1.0) and spearman(x, -x) == pytest.approx(-1.0)
    assert math.isnan(spearman(x, np.ones(4)))
    geoids = ["a", "b", "c", "d", "e", "f"]
    scores = np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    assert top_k_hits(geoids, scores, {"a", "c", "f"}, k=3) == 2


def test_two_level_bootstrap_is_seeded_and_brackets_the_estimate() -> None:
    rng = np.random.default_rng(0)
    runs = []
    for seed in range(3):
        truth = rng.normal(size=80)
        arrays = {"siteiq": truth + rng.normal(scale=0.5, size=80), "truth": truth, "baseline": rng.normal(size=80)}
        runs.append(ProfileRun(seed, "p", 80, [str(i) for i in range(80)], arrays, set(), "x"))
    first = two_level_bootstrap(runs, rho("siteiq"), n_resamples=200)
    again = two_level_bootstrap(runs, rho("siteiq"), n_resamples=200)
    assert first == again and first["ci_low"] <= first["estimate"] <= first["ci_high"]
    assert first["n_resamples"] == 200 and first["seed"] == 42
    hits = city_bootstrap([0.2, 0.4, 0.6], n_resamples=200)
    assert hits["estimate"] == pytest.approx(0.4) and hits["ci_low"] <= 0.4 <= hits["ci_high"]


def test_baseline_factor_is_the_top_weight_site_factor() -> None:
    assert baseline_factor(PROFILES["car_wash"]) == "traffic_aadt"
    assert baseline_factor(PROFILES["laundromat"]) == "renter_households"
    assert baseline_factor(PROFILES["retail_storefront"]) == "resident_population"  # tie: first listed wins


def test_small_city_end_to_end() -> None:
    runs = evaluate_city(load_metro("synthetic_metro"), 5, PROFILES, n_tracts=50)
    assert {run.profile_id for run in runs} == set(PROFILES)
    for run in runs:
        assert len(run.true_top) == validation.TRUE_TOP and len(run.geoids) == len(run.arrays["truth"])
        assert run.n_tracts == 50 and sum(run.exclusions.values()) == 50 - len(run.geoids)
        summary = summarize_profile([run], n_resamples=30)
        for key in ("siteiq_spearman", "baseline_single_factor_spearman", "siteiq_minus_baseline_spearman"):
            assert summary[key]["n_resamples"] == 30
        assert 0 <= summary["random_expected_top5_hit_rate"] <= 1
        assert set(summary["factor_spearman_mean_over_cities"]) == set(run.factor_arrays)
    motel = summarize_profile([next(run for run in runs if run.profile_id == "motel")], n_resamples=10)
    assert motel["demand_estimate_spearman"]["estimate"] is None  # the motel estimate does not vary by site


def test_real_metro_table_without_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(validation, "get_fabric", no_snapshot_fabric)
    table = real_metro_sanity("chicago_il_in", PROFILES)
    assert table["mode"] == "limited" and table["snapshot_date"] is None and table["tracts_in_snapshot"] == 0
    assert table["snapshot_steps_not_ok"] == {}
    for row in table["by_business_type"].values():
        assert row["status"].startswith("not measured") and row["coverage_share"] == 0.0


def test_failed_snapshot_steps_are_copied_from_the_manifest(tmp_path) -> None:
    steps = [
        {"name": "tract_geography", "status": "ok", "detail": "fine"},
        {"name": "acs_tracts", "status": "failed", "detail": "api.census.gov answered with an HTML page"},
        {"name": "enhancement:str_comps:x", "status": "not_available", "detail": "not verified"},
    ]
    snapshot = Snapshot(folder=tmp_path, manifest={"snapshot_date": "2026-10-13", "steps": steps}, tracts={})
    assert steps_not_ok(snapshot) == {
        "acs_tracts": "failed: api.census.gov answered with an HTML page",
        "enhancement:str_comps:x": "not_available: not verified",
    }
    assert steps_not_ok(None) == {}


def test_script_writes_metrics_with_provenance(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(validation, "get_fabric", no_snapshot_fabric)
    output = tmp_path / "siteiq.json"
    assert script.main(["--cities", "1", "--n-tracts", "40", "--resamples", "20", "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provenance"]["generated_by"] == "scripts/eval_studio_siteiq.py"
    recorded = report["provenance"]["eval_sets"]
    assert "config/studio/metros/synthetic_metro.yaml" in recorded
    assert {path for path in recorded if "business_types" in path} == {
        f"config/studio/business_types/{profile_id}.yaml" for profile_id in PROFILES
    }
    assert report["data_label"] == "SYNTHETIC" and "same project author" in report["statement"]
    assert "Not financial" in report["disclaimer"]
    assert report["method"]["city_seeds"] == [load_metro("synthetic_metro").synthetic.seed + 1]
    assert set(report["by_business_type"]) == set(PROFILES)
    real = {metro_id for metro_id in list_metros() if load_metro(metro_id).data_label == "PUBLIC"}
    assert set(report["real_metro_sanity"]) == real
    printed = capsys.readouterr().out
    assert "car_wash" in printed and "Wrote" in printed
