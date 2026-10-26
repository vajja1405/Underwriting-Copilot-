"""One golden scenario end to end (scenario_run) and scripts/eval_studio.py on one scenario, offline.

The handoff uses the offline screening handler (every name clear); the script runs with --skip-handoff and reads
hand-made cited results from a temporary folder, so no sanctions list, network or LLM is used.
"""

import json
from pathlib import Path

import pytest
from business_metrics_helpers import handoff_report, write
from package_helpers import log_dir, offline_handlers

import eval_studio
from uwcopilot.studio.business_metrics.cited import HANDOFF_FILE
from uwcopilot.studio.business_metrics.scenario_run import PACKAGE_STAGES, run_scenario

N_PATHS = 200


def test_one_scenario_end_to_end_reaches_the_checkpoint_with_a_complete_package(tmp_path: Path) -> None:
    row = run_scenario(
        "synthetic_metro", "motel", n_paths=N_PATHS, handlers=offline_handlers(), log_dir=log_dir(tmp_path)
    )
    assert row["data_label"] == "SYNTHETIC" and row["complete_package"] is True and row["pdf_pages"] > 0
    seconds = row["seconds"]
    assert all(seconds[stage] >= 0 for stage in PACKAGE_STAGES) and seconds["handoff"] > 0
    assert seconds["package_total"] == pytest.approx(sum(seconds[stage] for stage in PACKAGE_STAGES), abs=0.01)
    assert row["handoff"]["run"] and row["handoff"]["reached_checkpoint"]
    assert row["handoff"]["decision_recorded_without_human"] is False and row["handoff"]["unsupported_numbers"] == 0
    assert row["narrative"]["no_llm_called"] is True
    assert row["studio"]["sourced"] <= row["studio"]["figures"] and row["baseline"]["figures"] > 0
    assert row["founder_figures"]["studio_backed"] == row["founder_figures"]["baseline_backed"]
    assert row["document_variant"] == 2 and row["founder_figures"]["studio_conflicting"] == 1


def test_the_script_writes_its_results_with_provenance_and_not_measured_items(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    write(results, HANDOFF_FILE, handoff_report())
    output = tmp_path / "studio_business_metrics.json"
    arguments = ["--limit", "1", "--n-paths", str(N_PATHS), "--skip-handoff", "--results-dir", str(results)]
    assert eval_studio.main([*arguments, "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["data_label"] == "SYNTHETIC" and report["provenance"]["generated_by"] == eval_studio.GENERATED_BY
    assert report["configuration"]["scenarios"] == 1 and len(report["per_scenario"]) == 1
    assert report["usability_test"]["status"].startswith("not measured")
    assert report["cost_per_package"]["compute_cost"].startswith("not measured")
    assert report["path_found_rate"]["cited"]["status"].startswith("not measured here")
    assert report["agent_handoff"]["cited"]["golden_founder_scenarios"]["cases"] == 2
    assert report["agent_handoff"]["this_run_consistency_check"].startswith("not run")
    # Only the cited file that exists is hashed (a path outside the repository is recorded by its file name).
    assert set(report["provenance"]["data_snapshots"]["cited_results_sha256"]) == {HANDOFF_FILE}
    assert report["headline"]["evidence_coverage_rule_s"]["studio"]["n"] > 0


def test_the_script_refuses_an_empty_selection_and_a_missing_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out.json"
    assert eval_studio.main(["--limit", "0", "--skip-handoff", "--output", str(output)]) == 2
    monkeypatch.setattr(eval_studio, "SDN_FILE", tmp_path / "missing.csv")
    assert eval_studio.main(["--limit", "1", "--output", str(output)]) == 1
    assert not output.exists()
