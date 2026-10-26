"""scripts/monitor.py end to end on SYNTHETIC freMTPL2-shaped data (offline, everything written under tmp_path)."""

import json
from pathlib import Path
from typing import Any

import pytest
from monitoring_test_helpers import run_paths, simulated_feedback_summary, small_config, write_inputs

import monitor
from uwcopilot.governance.model_status import ModelEntry, ModelStatusStore
from uwcopilot.governance.monitoring import read_alerts
from uwcopilot.governance.monitoring_config import MonitoringConfig
from uwcopilot.governance.monitoring_indicators import override_indicator_problems
from uwcopilot.governance.monitoring_model import glm_from_dict, glm_to_dict
from uwcopilot.risk.data.fremtpl2 import prepare_fremtpl2, synthetic_fremtpl2_frames
from uwcopilot.risk.insurance import fit_frequency_glm
from uwcopilot.risk.results import update_results


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], dict[str, Any], monitor.RunPaths]:
    """The same monitoring run twice: the second reuses the cached model."""
    tmp_path = tmp_path_factory.mktemp("monitor")
    policies, files, results_dir = write_inputs(tmp_path)
    paths = run_paths(tmp_path, results_dir)
    config = small_config()
    first = monitor.run(config, policies, files, paths)
    second = monitor.run(config, policies, files, paths)
    return first, second, paths


def test_the_run_writes_one_monitoring_section_with_provenance(
    two_runs: tuple[dict[str, Any], dict[str, Any], monitor.RunPaths],
) -> None:
    first, _, paths = two_runs
    contents = json.loads(paths.results.read_text(encoding="utf-8"))
    assert list(contents) == ["monitoring"]
    section = contents["monitoring"]
    assert section["metadata"]["datasets"]["fremtpl2"]["files"] == []
    assert section["results"]["data_label"] == "SYNTHETIC"
    assert section["results"]["credit_model"]["status"] == "not measured"
    assert set(first["validation"]) == {"no_drift", "covariate_shift_young_drivers", "claim_rate_up_25pct"}
    assert "false_alert_rate" in first["validation"]["no_drift"]
    assert "detection_rate" in first["validation"]["claim_rate_up_25pct"]
    assert first["config"]["sha256"] and first["monitored_model"]["training_split"]["seed"] == 42


def test_the_demonstration_breach_is_logged_and_marks_the_model_under_review(
    two_runs: tuple[dict[str, Any], dict[str, Any], monitor.RunPaths],
) -> None:
    first, second, paths = two_runs
    windows = first["demonstration_stream"]["windows"]
    assert [window["drifted"] for window in windows] == [False, False, True, True]
    assert all(window["alert_ids"] for window in windows if window["drifted"])
    status = first["model_status"]
    assert (status["status_before_run"], status["status_after_run"]) == (None, "under_review")
    assert status["history"][-1]["actor"] == "monitor"
    assert (second["model_status"]["status_before_run"], second["model_status"]["status_after_run"]) == (
        "under_review",
        "under_review",
    )
    logged = read_alerts(paths.alert_log)
    assert len(logged) == first["alerts"]["n_alerts_this_run"] + second["alerts"]["n_alerts_this_run"]


def test_an_indicator_breach_is_recorded_without_touching_the_registry(
    two_runs: tuple[dict[str, Any], dict[str, Any], monitor.RunPaths],
) -> None:
    first, _, _ = two_runs
    indicator_alerts = [record for record in first["alerts"]["records"] if record["component"] == "agent"]
    assert [record["subject"] for record in indicator_alerts] == ["required_escalations_escalated"]
    assert indicator_alerts[0]["action"] == "record_only" and indicator_alerts[0]["model_id"] is None
    names = {item["name"]: item["status"] for item in first["indicators"]["values"]}
    assert names["citation_support_rate"] == "not available" and names["unsupported_claim_rate"] == "not measured"


def test_the_second_run_reuses_the_cached_model(
    two_runs: tuple[dict[str, Any], dict[str, Any], monitor.RunPaths],
) -> None:
    first, second, _ = two_runs
    assert not first["monitored_model"]["cache"]["loaded_from_cache"]
    assert second["monitored_model"]["cache"]["loaded_from_cache"]
    assert (
        first["monitored_model"]["cache"]["model_file_sha256"]
        == second["monitored_model"]["cache"]["model_file_sha256"]
    )
    assert first["validation"] == second["validation"]


def test_the_cached_json_model_predicts_exactly_like_the_trained_one() -> None:
    frequency_raw, severity_raw = synthetic_fremtpl2_frames(n_policies=3000, seed=8)
    policies = prepare_fremtpl2(frequency_raw, severity_raw).policies
    model = fit_frequency_glm(policies)
    restored = glm_from_dict(json.loads(json.dumps(glm_to_dict(model))))
    assert (restored.predict_rate(policies) == model.predict_rate(policies)).all()


def test_a_family_already_in_use_leaves_alerts_record_only(tmp_path: Path) -> None:
    policies, files, results_dir = write_inputs(tmp_path)
    paths = run_paths(tmp_path, results_dir)
    store = ModelStatusStore(paths.registry)
    store.register(ModelEntry(model_id="other-model", family="fremtpl2_frequency", version="9", status="champion"))
    config = small_config()
    document = config.model_dump(mode="json")
    document["stream"]["n_streams"] = 1
    payload = monitor.run(MonitoringConfig.model_validate(document), policies, files, paths)
    assert payload["model_status"]["registration"].startswith("not registered")
    assert all(record["action"] == "record_only" for record in payload["alerts"]["records"])
    assert store.status("other-model") == "champion"


def test_the_monitor_reads_a_feedback_section_written_before_it(tmp_path: Path) -> None:
    """P6-STAT-1 and P6-STAT-2 end to end: feedback first, then the monitor (the order of make governance-results)."""
    policies, files, results_dir = write_inputs(tmp_path)
    paths = run_paths(tmp_path, results_dir)
    summary = simulated_feedback_summary([["referral_resolved_in_writing"], ["policy_exception_approved"]], 14)
    update_results(paths.results, "feedback", {"data_label": "SYNTHETIC", "summary": summary})
    document = small_config().model_dump(mode="json")
    document["stream"]["n_streams"] = 1
    payload = monitor.run(MonitoringConfig.model_validate(document), policies, files, paths)

    override = {item["name"]: item for item in payload["indicators"]["values"]}["override_rate"]
    assert (override["status"], override["value"], override["level"]) == ("measured", 0.125, "warn")
    assert override["detail"]["override_reasons"]["policy_exception_approved"] == 1
    assert payload["indicators"]["sources"]["results/governance_metrics.json"]["sha256"] is not None
    assert "override_reasons: " in paths.report.read_text(encoding="utf-8")
    assert override_indicator_problems(json.loads(paths.results.read_text(encoding="utf-8"))) == []

    # The feedback track runs again after the monitor: the file's two sections no longer agree.
    rerun = simulated_feedback_summary([["referral_resolved_in_writing"]], 14)
    update_results(paths.results, "feedback", {"data_label": "SYNTHETIC", "summary": rerun})
    (problem,) = override_indicator_problems(json.loads(paths.results.read_text(encoding="utf-8")))
    assert "differs" in problem


@pytest.mark.integration
def test_the_real_fremtpl2_run_writes_the_section(tmp_path: Path) -> None:
    """Needs data/raw/fremtpl2 (make data). Two validation streams per scenario, every output under tmp_path."""
    results = tmp_path / "governance_metrics.json"
    arguments = [
        "--n-streams",
        "2",
        "--results-path",
        str(results),
        "--report-path",
        str(tmp_path / "monitoring_report.html"),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--alert-log",
        str(tmp_path / "alerts.jsonl"),
        "--registry",
        str(tmp_path / "model_status.json"),
    ]
    assert monitor.main(arguments) == 0
    payload = json.loads(results.read_text(encoding="utf-8"))["monitoring"]["results"]
    assert payload["stream_design"]["pool_size"] > 100_000
    assert payload["validation"]["no_drift"]["false_alert_rate"]["denominator"] == 2
    assert payload["model_status"]["status_after_run"] == "under_review"
