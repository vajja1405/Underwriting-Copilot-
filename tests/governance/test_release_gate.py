"""Release gate (governance/release_gate.py, scripts/release_gate.py) with fault injection.

Most tests copy the committed results files the gate reads into a temporary folder, make a baseline from them with
update_baseline, then break one thing in the temporary copy (a regressed AUC, an escalation rate of 0.98, a missing
file, NaN, ...) and check the row's status and the verdict. Nothing here downloads data or calls a model; the
committed results are read-only inputs.
"""

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import release_gate as release_gate_script
from uwcopilot.agent.evaluation import rate
from uwcopilot.common.paths import CONFIG_DIR, RESULTS_DIR
from uwcopilot.governance.freeze_proof import migration_section_sha256
from uwcopilot.governance.migration_gate import load_migration_settings
from uwcopilot.governance.release_gate import (
    MANIFEST_NAME,
    GateConfigError,
    GateReport,
    GateRow,
    GateSettings,
    Reading,
    RowResult,
    classify,
    decide,
    evaluate_gate,
    file_matches_head,
    load_gate_settings,
    lookup,
    update_baseline,
)
from uwcopilot.risk.results import not_measured

CONFIG = CONFIG_DIR / "release_gate.yaml"
# The parsed `migration:` section's fingerprint when the `gate:` section was added: adding the gate must not move it.
MIGRATION_SECTION_SHA256 = "fa781320491cfc397f7fdb62b26881023f7ae2f7d02f756c43044b78f6b8c26d"
AUC_LOW = "credit.results.models.lightgbm.test.auc.ci_low"
FREQUENCY_HIGH = "insurance_frequency.results.test_metrics.lgbm.mean_poisson_deviance.ci_high"
ESCALATIONS = "deterministic_path.escalation.all_required_escalations.value"
CITATION_LOW = "rules.citations.supported_all_values.ci_low"


@pytest.fixture(scope="module")
def settings() -> GateSettings:
    """The committed gate configuration."""
    return load_gate_settings(CONFIG)


@pytest.fixture()
def dirs(tmp_path: Path, settings: GateSettings) -> tuple[Path, Path]:
    """(results, baseline): copies of the committed gated results files, and a baseline made from them."""
    results_dir, baseline_dir = tmp_path / "results", tmp_path / "baseline"
    results_dir.mkdir()
    for name in settings.files():
        if (RESULTS_DIR / name).is_file():
            shutil.copyfile(RESULTS_DIR / name, results_dir / name)
    update_baseline(settings, results_dir, baseline_dir, repo_root=tmp_path)
    return results_dir, baseline_dir


def read_json(path: Path) -> Any:
    """Parse a JSON file (NaN allowed, as json.loads allows it)."""
    return json.loads(path.read_text(encoding="utf-8"))


def get_path(document: Any, dotted: str) -> Any:
    """The value at a dotted path (number segments index lists)."""
    for segment in dotted.split("."):
        document = document[int(segment)] if isinstance(document, list) else document[segment]
    return document


def parent_and_key(document: Any, dotted: str) -> tuple[Any, str]:
    """The container holding the last key of a dotted path, and that key."""
    *parents, last = dotted.split(".")
    return (get_path(document, ".".join(parents)) if parents else document), last


def set_path(path: Path, dotted: str, value: Any) -> None:
    """Rewrite one value of a JSON file in place (allow_nan so a test can plant NaN)."""
    document = read_json(path)
    parent, key = parent_and_key(document, dotted)
    parent[key] = value
    path.write_text(json.dumps(document, allow_nan=True), encoding="utf-8")


def delete_path(path: Path, dotted: str) -> None:
    """Remove one key of a JSON file in place."""
    document = read_json(path)
    parent, key = parent_and_key(document, dotted)
    del parent[key]
    path.write_text(json.dumps(document), encoding="utf-8")


def row_result(report: GateReport, row_id: str) -> RowResult:
    """The result of one row by id."""
    return next(result for result in report.results if result.row.id == row_id)


def make_row(**overrides: Any) -> GateRow:
    """A small baseline-relative higher-is-better row; overrides change any field."""
    fields: dict[str, Any] = {
        "id": "metric",
        "title": "A metric",
        "file": "metrics.json",
        "value_path": "metric.value",
        "gate_on": "value",
        "direction": "higher_is_better",
        "kind": "baseline_relative",
        "tolerance": 0.01,
        "required": True,
        "why_critical": "test row",
    }
    fields.update(overrides)
    return GateRow(**fields)


# --- The committed state --------------------------------------------------------------------------------------------


def test_committed_results_pass_against_the_committed_baseline(settings: GateSettings) -> None:
    report = evaluate_gate(settings, RESULTS_DIR, RESULTS_DIR / "baseline")
    assert report.verdict == "PASS", [(result.row.id, result.reason) for result in report.results]
    assert report.baseline_problems == [] and report.manifest_problem is None
    statuses = {result.row.id: result.status for result in report.results}
    not_measured_rows = {"llm_citation_accuracy", "llm_unsupported_claim_rate", "injection_asr_l1", "injection_asr_l2"}
    assert {row for row, status in statuses.items() if status == "NOT_MEASURED"} == not_measured_rows
    assert all(status == "PASS" for row, status in statuses.items() if row not in not_measured_rows)


def test_the_script_exits_0_on_the_committed_state(capsys: pytest.CaptureFixture) -> None:
    assert release_gate_script.main([]) == release_gate_script.EXIT_PASS
    out = capsys.readouterr().out
    assert "Verdict: PASS" in out and "NOT_MEASURED  llm_citation_accuracy" in out


def test_adding_the_gate_section_left_the_frozen_migration_section_alone() -> None:
    assert migration_section_sha256(CONFIG.read_bytes()) == MIGRATION_SECTION_SHA256
    assert len(load_migration_settings(CONFIG).metrics) == 5


def test_unchanged_copies_pass_and_dirty_provenance_is_only_a_warning(dirs: tuple[Path, Path], settings) -> None:
    report = evaluate_gate(settings, *dirs)
    assert report.verdict == "PASS"
    dirty_files = {warning["file"] for warning in report.warnings if warning["kind"] == "git_dirty"}
    assert "results/agent_metrics.json" in dirty_files  # versions.git.uncommitted_changes is true in that file
    assert "results/studio_handoff_metrics.json" not in dirty_files  # its provenance.git_dirty is false


# --- Fault injection: regressions -----------------------------------------------------------------------------------


def test_a_regressed_auc_lower_bound_fails(dirs: tuple[Path, Path], settings, capsys: pytest.CaptureFixture) -> None:
    results_dir, baseline_dir = dirs
    baseline_low = get_path(read_json(baseline_dir / "risk_metrics.json"), AUC_LOW)
    set_path(results_dir / "risk_metrics.json", AUC_LOW, baseline_low - 0.02)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    result = row_result(report, "credit_auc_ci_low")
    assert result.status == "FAIL" and "beyond the limit" in result.reason
    assert result.headroom is not None and result.headroom < 0
    assert report.verdict == "FAIL"
    arguments = ["--results-dir", str(results_dir), "--baseline-dir", str(baseline_dir)]
    assert release_gate_script.main(arguments) == release_gate_script.EXIT_FAIL
    assert "FAIL          credit_auc_ci_low" in capsys.readouterr().out


def test_auc_tolerance_edges(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    baseline_low = get_path(read_json(baseline_dir / "risk_metrics.json"), AUC_LOW)
    cases = {baseline_low - 0.005: "PASS", baseline_low - 0.005 - 1e-6: "FAIL", baseline_low + 0.01: "PASS"}
    for current, expected in cases.items():
        set_path(results_dir / "risk_metrics.json", AUC_LOW, current)
        assert row_result(evaluate_gate(settings, results_dir, baseline_dir), "credit_auc_ci_low").status == expected


def test_lower_is_better_tolerance_edges_for_insurance_deviance(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    baseline_high = get_path(read_json(baseline_dir / "risk_metrics.json"), FREQUENCY_HIGH)
    cases = {baseline_high + 0.005: "PASS", baseline_high + 0.005 + 1e-6: "FAIL", baseline_high - 0.01: "PASS"}
    for current, expected in cases.items():
        set_path(results_dir / "risk_metrics.json", FREQUENCY_HIGH, current)
        row = row_result(evaluate_gate(settings, results_dir, baseline_dir), "insurance_frequency_deviance_ci_high")
        assert row.status == expected


def test_decimal_edge_is_not_flipped_by_float_rounding() -> None:
    row = make_row(tolerance=0.005)
    status, _, limit = decide(row, Reading("measured", 0.7073), Reading("measured", 0.7123))
    assert status == "PASS" and limit == pytest.approx(0.7073)


def test_escalation_098_fails(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    set_path(results_dir / "agent_metrics.json", ESCALATIONS, 0.98)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert row_result(report, "agent_required_escalations").status == "FAIL"
    assert report.verdict == "FAIL"


def test_a_decision_recorded_without_a_human_fails(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    set_path(results_dir / "studio_handoff_metrics.json", "all_cases.decisions_recorded_without_a_human", 1)
    set_path(results_dir / "studio_handoff_metrics.json", "golden_founder_scenarios.untraced_numbers_pdf_text", 2)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert row_result(report, "studio_decisions_without_human").status == "FAIL"
    assert row_result(report, "studio_pdf_untraced_numbers").status == "FAIL"


def test_citation_support_regression_fails(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    baseline_low = get_path(read_json(baseline_dir / "docintel_metrics.json"), CITATION_LOW)
    set_path(results_dir / "docintel_metrics.json", CITATION_LOW, baseline_low - 0.003)
    assert row_result(evaluate_gate(settings, *dirs), "docintel_citation_support_ci_low").status == "FAIL"


# --- Fault injection: missing, invalid and not-measured evidence ----------------------------------------------------


def test_a_missing_results_file_fails_rows_the_baseline_measured(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    (results_dir / "agent_metrics.json").unlink()
    report = evaluate_gate(settings, results_dir, baseline_dir)
    escalation = row_result(report, "agent_required_escalations")
    assert escalation.status == "FAIL" and "agent_metrics.json is absent" in escalation.reason
    assert row_result(report, "agent_decisions_without_human").status == "FAIL"
    # The LLM rows were not measured in the baseline either, so they stay NOT_MEASURED.
    assert row_result(report, "llm_citation_accuracy").status == "NOT_MEASURED"
    assert report.verdict == "FAIL"


def test_nan_and_null_are_invalid_and_fail(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    set_path(results_dir / "risk_metrics.json", AUC_LOW, float("nan"))
    assert "NaN" in (results_dir / "risk_metrics.json").read_text(encoding="utf-8")
    result = row_result(evaluate_gate(settings, *dirs), "credit_auc_ci_low")
    assert result.status == "FAIL" and "not a finite number" in result.reason
    set_path(results_dir / "risk_metrics.json", AUC_LOW, None)
    result = row_result(evaluate_gate(settings, *dirs), "credit_auc_ci_low")
    assert result.status == "FAIL" and "null" in result.reason


def test_unreadable_results_file_fails(dirs: tuple[Path, Path], settings) -> None:
    results_dir, _ = dirs
    (results_dir / "docintel_metrics.json").write_text("{not json", encoding="utf-8")
    result = row_result(evaluate_gate(settings, *dirs), "docintel_citation_support_ci_low")
    assert result.status == "FAIL" and "not valid JSON" in result.reason


def test_measured_baseline_but_current_not_measured_fails(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    set_path(results_dir / "docintel_metrics.json", CITATION_LOW, not_measured("extractor not run"))
    result = row_result(evaluate_gate(settings, *dirs), "docintel_citation_support_ci_low")
    assert result.status == "FAIL" and "measured in the baseline" in result.reason
    set_path(results_dir / "agent_metrics.json", "deterministic_path.escalation", "not measured: agent not run")
    result = row_result(evaluate_gate(settings, *dirs), "agent_required_escalations")
    assert result.status == "FAIL" and "agent not run" in result.reason


def test_required_evidence_absent_in_both_is_missing(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    delete_path(results_dir / "studio_evidence_metrics.json", "headline")
    update_baseline(settings, results_dir, baseline_dir, repo_root=results_dir)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert row_result(report, "studio_narrative_untraced_numbers").status == "MISSING"
    assert report.verdict == "FAIL"


def test_a_new_measurement_needs_a_reviewed_baseline_then_is_gated_per_layer(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    layer = {"asr": {"all": {"successes": 1, "n": 60, "rate": 1 / 60, "wilson_ci95": [0.003, 0.089]}}}
    document = {"layers": {"L0": {"asr": "not measured (baseline layer)"}, "L1": layer, "L2": copy.deepcopy(layer)}}
    (results_dir / "injection_metrics.json").write_text(json.dumps(document), encoding="utf-8")
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert row_result(report, "injection_asr_l1").status == "MISSING"  # measured now, no reviewed baseline yet
    update_baseline(settings, results_dir, baseline_dir, repo_root=results_dir)
    assert evaluate_gate(settings, results_dir, baseline_dir).verdict == "PASS"
    set_path(results_dir / "injection_metrics.json", "layers.L2.asr.all.rate", 1 / 60 + 0.05)  # exactly on the limit
    assert row_result(evaluate_gate(settings, *dirs), "injection_asr_l2").status == "PASS"
    set_path(results_dir / "injection_metrics.json", "layers.L2.asr.all.rate", 5 / 60)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert row_result(report, "injection_asr_l2").status == "FAIL"
    assert row_result(report, "injection_asr_l1").status == "PASS"
    assert row_result(report, "injection_asr_l2").current["ci_bound"].value == pytest.approx(0.089)


def test_measured_llm_metrics_are_read_from_their_rate_block(dirs: tuple[Path, Path], settings) -> None:
    # A real LLM run writes rate() blocks {value, ci_low, ci_high, n}; the rows used to point at the block itself and
    # FAILed as "a dict, not a number" even against a baseline made from the same file.
    results_dir, baseline_dir = dirs
    agent_file = results_dir / "agent_metrics.json"
    set_path(agent_file, "llm_dependent.citation_accuracy", rate([True] * 18 + [False] * 2))
    set_path(agent_file, "llm_dependent.unsupported_claim_rate", rate([0.0] * 19 + [1.0]))
    update_baseline(settings, results_dir, baseline_dir, repo_root=results_dir)
    report = evaluate_gate(settings, results_dir, baseline_dir)
    citation = row_result(report, "llm_citation_accuracy")
    unsupported = row_result(report, "llm_unsupported_claim_rate")
    assert citation.status == "PASS" and citation.current["value"].value == pytest.approx(0.9)
    assert citation.current["ci_bound"].state == "measured"  # the lower bound is reported next to the value
    assert unsupported.status == "PASS" and unsupported.current["value"].value == pytest.approx(0.05)
    assert report.verdict == "PASS"
    set_path(agent_file, "llm_dependent.citation_accuracy", rate([True] * 17 + [False] * 3))  # 0.85 < 0.9 - 0.02
    assert row_result(evaluate_gate(settings, *dirs), "llm_citation_accuracy").status == "FAIL"


def test_both_not_measured_is_listed_and_does_not_block(dirs: tuple[Path, Path], settings) -> None:
    report = evaluate_gate(settings, *dirs)
    citation = row_result(report, "llm_citation_accuracy")
    assert citation.status == "NOT_MEASURED" and citation.current["value"].value is None
    assert "no LLM" in citation.reason  # the producer's own reason, never a filled-in value
    assert row_result(report, "injection_asr_l1").status == "NOT_MEASURED"
    assert report.counts()["NOT_MEASURED"] == 4 and report.verdict == "PASS"


# --- The baseline ---------------------------------------------------------------------------------------------------


def test_a_baseline_copy_edited_by_hand_blocks(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    set_path(baseline_dir / "risk_metrics.json", AUC_LOW, 0.5)  # loosening the baseline without the script
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert any("risk_metrics.json" in problem for problem in report.baseline_problems)
    assert report.verdict == "FAIL"


def test_a_copy_not_made_by_the_script_or_a_missing_manifest_blocks(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    (baseline_dir / "injection_metrics.json").write_text('{"layers": {}}', encoding="utf-8")
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert any("not in baseline_manifest.json" in problem for problem in report.baseline_problems)
    (baseline_dir / "injection_metrics.json").unlink()
    (baseline_dir / MANIFEST_NAME).unlink()
    report = evaluate_gate(settings, results_dir, baseline_dir)
    assert report.manifest_problem is not None and report.verdict == "FAIL"


def test_update_baseline_mirrors_the_results(dirs: tuple[Path, Path], settings) -> None:
    results_dir, baseline_dir = dirs
    (baseline_dir / "injection_metrics.json").write_text("{}", encoding="utf-8")  # an old copy of an absent file
    manifest, removed = update_baseline(settings, results_dir, baseline_dir, repo_root=results_dir)
    assert removed == ["injection_metrics.json"] and not (baseline_dir / "injection_metrics.json").exists()
    assert manifest["absent_when_made"] == ["injection_metrics.json"]
    assert set(manifest["files"]) == set(settings.files()) - {"injection_metrics.json"}
    assert manifest["files"]["agent_metrics.json"]["source_matches_head_commit"] == "unknown"  # outside any repo
    for name in manifest["files"]:
        assert (baseline_dir / name).read_bytes() == (results_dir / name).read_bytes()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_file_matches_head_compares_with_the_committed_bytes(tmp_path: Path) -> None:
    def git(*arguments: str) -> None:
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
        subprocess.run(["git", *identity, *arguments], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    git("add", "a.json")
    git("commit", "-q", "-m", "add a")
    assert file_matches_head(tmp_path / "a.json", tmp_path) is True
    (tmp_path / "a.json").write_text('{"changed": 1}', encoding="utf-8")
    assert file_matches_head(tmp_path / "a.json", tmp_path) is False
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")  # never committed
    assert file_matches_head(tmp_path / "b.json", tmp_path) is False


# --- Reading values and the configuration ---------------------------------------------------------------------------


def test_lookup_reads_lists_markers_and_missing_keys() -> None:
    document = {"a": {"ci": [0.1, 0.2], "flag": True, "text": "n/a", "block": "not measured: no run"}}
    assert lookup(document, "a.ci.1") == Reading("measured", 0.2)
    assert lookup(document, "a.flag").state == "invalid"  # a boolean is not a number
    assert lookup(document, "a.text").state == "invalid"
    assert lookup(document, "a.block.rate") == Reading("not_measured", detail="a.block: not measured: no run")
    missing = lookup(document, "a.ci.5")
    assert missing.state == "not_measured" and "'5'" in missing.detail
    assert classify(not_measured("no data")) == Reading("not_measured", detail="not measured: no data")
    assert classify(float("inf")).state == "invalid"


def test_absolute_rows_ignore_the_baseline_value_but_not_its_history() -> None:
    row = make_row(kind="absolute", target=0, direction="lower_is_better", tolerance=0)
    assert decide(row, Reading("measured", 0.0), Reading("not_measured", detail="x"))[0] == "PASS"
    assert decide(row, Reading("measured", 1.0), Reading("measured", 0.0))[0] == "FAIL"
    assert decide(row, Reading("not_measured", detail="gone"), Reading("measured", 0.0))[0] == "FAIL"
    optional = make_row(kind="absolute", target=0, direction="lower_is_better", tolerance=0, required=False)
    both_absent = (Reading("not_measured", detail="a"), Reading("not_measured", detail="b"))
    assert decide(optional, *both_absent)[0] == "NOT_MEASURED"
    assert decide(row, *both_absent)[0] == "MISSING"


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "absolute"},  # no target
        {"target": 1.0},  # a baseline-relative row with a target
        {"gate_on": "ci_bound"},  # no ci_bound_path
        {"file": "../secrets.json"},  # not a plain file name
        {"tolerance": -0.1},
        {"direction": "up"},
        {"unexpected": 1},  # unknown keys are refused
    ],
)
def test_invalid_rows_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_row(**overrides)


def test_duplicate_row_ids_are_refused() -> None:
    with pytest.raises(ValidationError):
        GateSettings(rows=[make_row(), make_row()])


def test_the_script_exits_2_on_an_invalid_configuration(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    config = tmp_path / "release_gate.yaml"
    config.write_text("migration: {}\n", encoding="utf-8")  # no gate: section
    assert release_gate_script.main(["--config", str(config)]) == release_gate_script.EXIT_CONFIG
    assert "no `gate:` section" in capsys.readouterr().err


def test_the_script_exits_2_on_malformed_yaml(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # yaml.YAMLError is not a ValueError: without the re-raise it escaped as a traceback with exit 1 ("regression").
    config = tmp_path / "release_gate.yaml"
    config.write_text("gate:\n  rows: [\n", encoding="utf-8")
    with pytest.raises(GateConfigError, match="not valid YAML"):
        load_gate_settings(config)
    assert release_gate_script.main(["--config", str(config)]) == release_gate_script.EXIT_CONFIG
    assert "not valid YAML" in capsys.readouterr().err


# --- Writing results ------------------------------------------------------------------------------------------------


def test_write_results_writes_the_release_gate_section(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    results_dir, baseline_dir = dirs
    output = tmp_path / "governance_metrics.json"
    arguments = ["--results-dir", str(results_dir), "--baseline-dir", str(baseline_dir)]
    assert release_gate_script.main([*arguments, "--write-results", "--output", str(output)]) == 0
    section = read_json(output)["release_gate"]
    payload = section["results"]
    assert payload["verdict"] == "PASS" and payload["exit_code"] == 0
    assert payload["counts"]["rows"] == len(payload["rows"]) == 15
    assert payload["counts"]["NOT_MEASURED"] == 4 and payload["counts"]["FAIL"] == 0
    assert "git_commit" in section["metadata"]
    text = output.read_text(encoding="utf-8").lower()
    assert "resistance" not in text  # the gate never turns a finite payload set into a resistance claim


def test_update_baseline_through_the_script(tmp_path: Path, settings, capsys: pytest.CaptureFixture) -> None:
    results_dir, baseline_dir = tmp_path / "results", tmp_path / "baseline"
    results_dir.mkdir()
    shutil.copyfile(RESULTS_DIR / "agent_metrics.json", results_dir / "agent_metrics.json")
    arguments = ["--results-dir", str(results_dir), "--baseline-dir", str(baseline_dir), "--update-baseline"]
    assert release_gate_script.main(arguments) == 0
    out = capsys.readouterr().out
    assert "copied agent_metrics.json" in out and "absent risk_metrics.json" in out
    assert (baseline_dir / MANIFEST_NAME).is_file()
