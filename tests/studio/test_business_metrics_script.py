"""One golden scenario end to end (scenario_run) and scripts/eval_studio.py on one scenario, offline.

The handoff uses the offline screening handler (every name clear); the script runs with --skip-handoff, or with the
handoff screening against small SYNTHETIC list files set up as the configured lists (business_metrics_helpers), and
reads hand-made cited results from a temporary folder, so no sanctions list, network or LLM is used.
"""

import json
from pathlib import Path

import pytest
from business_metrics_helpers import configure_screening_lists, handoff_report, write
from package_helpers import log_dir, offline_handlers

import eval_studio
from uwcopilot.common.files import sha256_of_file
from uwcopilot.kyc.screening import ScreeningListsError
from uwcopilot.studio.business_metrics.cited import HANDOFF_FILE
from uwcopilot.studio.business_metrics.scenario_run import PACKAGE_STAGES, run_scenario
from uwcopilot.tools.schemas import ScreeningRequest

N_PATHS = 200
LIST_LABELS: list[tuple[tuple[str, ...], str]] = [
    (("ofac",), "full OFAC lists (sdn.csv, alt.csv)"),
    (("opensanctions",), "full OpenSanctions lists (targets.simple.csv)"),
    (("ofac", "opensanctions"), "full OFAC + OpenSanctions lists (sdn.csv, alt.csv, targets.simple.csv)"),
]


def use_lists(monkeypatch: pytest.MonkeyPatch, folder: Path, lists: tuple[str, ...]) -> dict[str, str]:
    """Configure these lists (SYNTHETIC files) for kyc.screening and the script; {file name: SHA-256} of their files."""
    files = configure_screening_lists(monkeypatch, folder, lists)
    monkeypatch.setattr(eval_studio, "configured_screening_lists", lambda: lists)
    monkeypatch.setattr(eval_studio, "LIST_FILES", files)
    expected = {}
    for family in lists:
        for path in files[family]:
            expected[path.name] = sha256_of_file(path)
    return expected


def results_folder(tmp_path: Path) -> Path:
    """A results folder holding only the hand-made S6 handoff file."""
    results = tmp_path / "results"
    results.mkdir()
    write(results, HANDOFF_FILE, handoff_report())
    return results


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
    results = results_folder(tmp_path)
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
    assert report["configuration"]["handoff_consistency_check"] == "skipped"
    assert report["provenance"]["data_snapshots"]["screening_list_snapshots"] is None
    # Only the cited file that exists is hashed (a path outside the repository is recorded by its file name).
    assert set(report["provenance"]["data_snapshots"]["cited_results_sha256"]) == {HANDOFF_FILE}
    assert report["headline"]["evidence_coverage_rule_s"]["studio"]["n"] > 0


def test_the_script_refuses_an_empty_selection_and_a_missing_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out.json"
    assert eval_studio.main(["--limit", "0", "--skip-handoff", "--output", str(output)]) == 2
    files = configure_screening_lists(monkeypatch, tmp_path / "lists", ("opensanctions",))
    files["opensanctions"][0].unlink()
    monkeypatch.setattr(eval_studio, "LIST_FILES", files)
    monkeypatch.setattr(eval_studio, "configured_screening_lists", lambda: ("opensanctions",))
    assert eval_studio.main(["--limit", "1", "--output", str(output)]) == 1
    assert not output.exists()


def test_only_the_files_of_the_configured_lists_must_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = configure_screening_lists(monkeypatch, tmp_path / "lists", ("ofac",))
    files["opensanctions"][0].unlink()
    monkeypatch.setattr(eval_studio, "LIST_FILES", files)
    assert eval_studio.missing_list_files(("ofac",)) == []
    assert eval_studio.missing_list_files(("ofac", "opensanctions")) == [files["opensanctions"][0]]


def test_the_script_refuses_an_invalid_list_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid() -> tuple[str, ...]:
        """UWC_SCREENING_LISTS names a list that does not exist."""
        raise ScreeningListsError("UWC_SCREENING_LISTS must be 'all' or a comma-separated list (got 'nope')")

    monkeypatch.setattr(eval_studio, "configured_screening_lists", invalid)
    output = tmp_path / "out.json"
    assert eval_studio.main(["--limit", "1", "--output", str(output)]) == 1
    assert not output.exists()


@pytest.mark.parametrize(("lists", "label"), LIST_LABELS)
def test_the_handoff_label_and_checksums_follow_the_configured_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lists: tuple[str, ...], label: str
) -> None:
    expected = use_lists(monkeypatch, tmp_path / "lists", lists)
    assert eval_studio.missing_list_files(lists) == []
    handlers = eval_studio.handlers_for(skip_handoff=False)
    assert handlers is not None
    handlers["screen_entity"](ScreeningRequest(name="Synthetic Founder Owner", entity_type="person"))
    snapshots = eval_studio.loaded_list_snapshots(handlers)
    assert eval_studio.handoff_check_label(snapshots) == label
    records = eval_studio.snapshot_records(snapshots)
    assert records is not None
    assert {record["file_name"]: record["sha256"] for record in records} == expected


def test_a_skipped_handoff_loads_no_list_and_says_skipped() -> None:
    assert eval_studio.loaded_list_snapshots(None) is None
    assert eval_studio.handoff_check_label(None) == "skipped"
    assert eval_studio.snapshot_records(None) is None


def test_the_script_names_the_lists_its_handoff_screened_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = use_lists(monkeypatch, tmp_path / "lists", ("opensanctions",))
    output = tmp_path / "studio_business_metrics.json"
    arguments = ["--limit", "1", "--n-paths", str(N_PATHS), "--results-dir", str(results_folder(tmp_path))]
    assert eval_studio.main([*arguments, "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["per_scenario"][0]["handoff"]["run"] is True
    assert report["configuration"]["handoff_consistency_check"] == "full OpenSanctions lists (targets.simple.csv)"
    snapshots = report["provenance"]["data_snapshots"]["screening_list_snapshots"]
    assert {item["file_name"]: item["sha256"] for item in snapshots} == expected
    assert snapshots[0]["source"] == "OpenSanctions" and snapshots[0]["attribution"]
