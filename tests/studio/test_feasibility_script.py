"""scripts/eval_studio_feasibility.py writes a labeled results file with a provenance block (tiny settings)."""

import json

import eval_studio_feasibility as script
from uwcopilot.agent.policy_credit import DSCR_MINIMUM


def test_main_writes_results_with_provenance(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "TIMING_PATHS", 50)
    monkeypatch.setattr(script, "BOOTSTRAP_RESAMPLES", 20)
    output = tmp_path / "feasibility.json"
    args = ["--worlds", "2", "--paths", "50", "--timing-repeats", "1", "--extra-truths", "5", "--output", str(output)]
    assert script.main(args) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["data_label"] == "SYNTHETIC"
    assert report["simulation_label"] == "simulated, based on stated assumptions"
    assert report["real_metros"].startswith("not measured")
    assert set(report["conditions"]) == {"well_specified", "misspecified"}
    per_type = report["conditions"]["well_specified"]["per_business_type"]
    assert len(per_type) == 6 and all(block["n_worlds"] == 2 for block in per_type.values())
    assert report["settings"]["dscr_target"]["value"] == DSCR_MINIMUM["term_loan"]
    assert report["settings"]["extra_truths_per_world"] == 5
    assert report["method"]["coverage_reference_if_well_specified"]["value"] == round(0.8 * 49 / 51, 4)
    assert all("anchored_by" in block for block in per_type.values())
    headline = report["headline"][0]
    assert {"band_coverage_revenue", "expected_band_coverage_revenue"} <= set(headline)
    provenance = report["provenance"]
    assert provenance["generated_by"] == "scripts/eval_studio_feasibility.py"
    assert "config/studio/feasibility/assumptions.yaml" in provenance["eval_sets"]
    assert provenance["data_snapshots"]["studio_synthetic_metro"]["data_label"] == "SYNTHETIC"
    assert set(report["timing"]["per_business_type"]) == set(per_type)
    example = report["example_run"]
    assert example["profile"] == "car_wash" and "SYNTHETIC" in example["note"]
    assert set(example["p_dscr_at_least_target"]) == {"year_1", "year_2", "year_3"}
    assert [item["name"] for item in example["stress"]] == ["revenue_stress", "rate_stress"]
    assert "well_specified" in capsys.readouterr().out


def test_expected_band_hold_formula() -> None:
    assert script.expected_band_hold(1000) == 0.8 * 999 / 1001
    assert script.expected_band_hold(1000) < 0.8
