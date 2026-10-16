"""scripts/eval_studio_paths.py writes a labeled results file with a provenance block (tiny settings)."""

import json

import eval_studio_paths as script


def test_main_writes_results_with_provenance(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "BOOTSTRAP_RESAMPLES", 20)
    output = tmp_path / "paths.json"
    args = [
        "--plans",
        "1",
        "--exhaustive",
        "1",
        "--metros",
        "synthetic_metro",
        "--business-types",
        "car_wash",
        "--output",
        str(output),
    ]
    assert script.main(args) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["data_label"] == "SYNTHETIC"
    assert report["simulation_label"] == "simulated, based on stated assumptions"
    assert report["real_world_accuracy"].startswith("not measured")
    assert report["settings"]["business_types"] == ["car_wash"]
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["facts_moved"] == []
    assert row["s3_check_max_gap"] in (None, 0.0)
    assert report["checks"]["facts_moved"]["count"] == 0
    assert report["checks"]["facts_variant"]["fields_moved"] == 0
    assert row["facts_variant_change_sets"] >= 0 and row["facts_variant_moved"] == []
    assert "path_found_and_program_compliant_rate_full" in report["headline"]
    assert "two_target_top_change_set_fails_lender_base_case_nsp_4_2" in report["stress_target_effect"]
    assert "fabric" in report["runtime"]["call_path"]
    assert report["heuristic_vs_brute_force"]["n_plans"] == 1
    assert report["heuristic_vs_brute_force"]["heuristic_missed_a_path"] == 0
    assert set(report["runtime"]) >= {"full", "quick", "quick_repeat_warm_cache"}
    assert "path_found_rate_full" in report["headline"]
    assert report["metros"]["synthetic_metro"]["data_label"] == "SYNTHETIC"
    margins = report["structural_margin_check"]["car_wash"]
    assert margins["cost_ratio_sum_low"] <= margins["cost_ratio_sum_mode"] <= margins["cost_ratio_sum_high"]
    assert sum(report["anchoring_by_business_type"]["car_wash"].values()) == 1
    provenance = report["provenance"]
    assert provenance["generated_by"] == "scripts/eval_studio_paths.py"
    assert "config/studio/levers.yaml" in provenance["eval_sets"]
    assert "corpus/programs/northwind_startup_program.md" in provenance["eval_sets"]
    assert "wrote" in capsys.readouterr().out


def test_unknown_business_type_is_refused(tmp_path) -> None:
    try:
        script.main(["--business-types", "space_station", "--output", str(tmp_path / "x.json")])
    except SystemExit as error:
        assert "unknown business type" in str(error)
    else:
        raise AssertionError("expected SystemExit")
