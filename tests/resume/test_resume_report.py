"""Tests for uwcopilot.resume.report and its bullets: numbers only from results, omissions, no forbidden wording."""

import json
from pathlib import Path

import pytest
from resume_fixtures import allowed_numbers, fake_repo, numbers_in, wilson, write_json, write_results

from uwcopilot.common.paths import REPO_ROOT, RESULTS_DIR
from uwcopilot.resume.report import ResumeReport, build_report, render_omissions, render_projects
from uwcopilot.resume.text import LENDER, STUDIO, check_wording

FORBIDDEN_IN_OUTPUT = ("blocked", "100%", "deploy", "cloud", "aws", "bedrock", "terraform", "p95", "$", "production")


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """A complete fixture results folder."""
    return write_results(tmp_path / "results")


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """A repo root with the credit model card and a CI workflow that runs the release gate."""
    return fake_repo(tmp_path / "repo")


def edit(folder: Path, name: str, *keys: str, value: object) -> None:
    """Set data[key1][key2]... = value in folder/name."""
    path = folder / name
    data = json.loads(path.read_text(encoding="utf-8"))
    node = data
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    path.write_text(json.dumps(data), encoding="utf-8")


def bullet_text(report: ResumeReport, bullet_id: str) -> str:
    """The text of one bullet ('' when it was left out)."""
    return next((bullet.text for bullet in report.bullets if bullet.bullet_id == bullet_id), "")


def omitted_parts(report: ResumeReport, bullet_id: str) -> list[str]:
    """The parts recorded as left out for one bullet."""
    return [item.part for item in report.omissions if item.bullet_id == bullet_id]


def test_full_fixture_gives_six_lender_and_six_studio_bullets(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    assert sum(1 for bullet in report.bullets if bullet.section == LENDER) == 6
    assert sum(1 for bullet in report.bullets if bullet.section == STUDIO) == 6


def test_bullets_use_only_numbers_from_the_results_folder(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    allowed = allowed_numbers(results_dir)
    for bullet in report.bullets:
        unknown = [token for token in numbers_in(bullet.text) if token not in allowed]
        assert not unknown, f"{bullet.bullet_id}: numbers not in the results folder: {unknown}"


def test_fixture_values_appear_with_their_intervals(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    assert "recall 0.8123 [0.7011, 0.9034] and precision 0.6543 [0.5432, 0.7654]" in bullet_text(
        report, "evidence_platform"
    )
    assert "0.7234 [0.7123, 0.7345] vs 0.7012 [0.6901, 0.7123]" in bullet_text(report, "credit_model")
    assert "+0.0222 [+0.0111, +0.0333]" in bullet_text(report, "credit_model")
    assert "48.7 [41.2, 55.3] alerts per 1,000" in bullet_text(report, "kyc_screening")
    assert "9 of 33 frozen attack payloads after text normalization (0.2727 [Wilson 0.1508, 0.4428])" in bullet_text(
        report, "document_extraction"
    )
    assert "better than the single-factor baseline for 1, worse for 1, no clear difference for 1" in bullet_text(
        report, "studio_siteiq"
    )


def test_not_measured_clause_is_dropped_and_reported(results_dir: Path, repo_root: Path) -> None:
    edit(
        results_dir,
        "kyc_metrics.json",
        "operating_points",
        "50",
        "rules_only",
        "test",
        "clean_false_positive_rate",
        value="not measured (test)",
    )
    report = build_report(results_dir, repo_root)
    kyc = bullet_text(report, "kyc_screening")
    assert kyc and "false-positive rate" not in kyc and "0.0444" not in kyc
    assert "clean false-positive rate" in omitted_parts(report, "kyc_screening")


def test_not_measured_core_metric_drops_the_bullet(results_dir: Path, repo_root: Path) -> None:
    edit(
        results_dir,
        "risk_metrics.json",
        "credit",
        "results",
        "models",
        "lightgbm",
        "test",
        "auc",
        value="not measured (no Lending Club data)",
    )
    report = build_report(results_dir, repo_root)
    assert bullet_text(report, "credit_model") == ""
    assert "whole bullet" in omitted_parts(report, "credit_model")
    assert "0.3456" not in "\n".join(render_projects(report))  # its KS is not printed elsewhere


def test_missing_results_file_drops_the_bullet_with_its_reason(results_dir: Path, repo_root: Path) -> None:
    (results_dir / "kyc_metrics.json").unlink()
    report = build_report(results_dir, repo_root)
    assert bullet_text(report, "kyc_screening") == ""
    reasons = [item.reason for item in report.omissions if item.bullet_id == "kyc_screening"]
    assert any("results/kyc_metrics.json does not exist" in reason for reason in reasons)


def test_empty_results_folder_prints_no_metric(tmp_path: Path, repo_root: Path) -> None:
    report = build_report(tmp_path, repo_root)
    assert report.bullets == []
    text = "\n".join(render_projects(report))
    assert "(no measured bullet: see stderr)" in text
    assert any("Prompt 8 asks for 4-6" in note for note in report.notes)


def test_circular_next_best_evidence_agreement_is_never_quoted(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    assert "0.9911" not in "\n".join(render_projects(report))
    assert "next-best-evidence top-3 accuracy" in omitted_parts(report, "evidence_platform")


def test_injection_attack_success_is_left_out_without_a_full_run(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    assert "attack success" not in bullet_text(report, "document_extraction")
    assert "prompt-injection attack success by defense layer" in omitted_parts(report, "document_extraction")


def full_injection_run(status: str = "measured") -> dict:
    """A results/injection_metrics.json with every layer scored."""
    layers = {}
    for name, (hits, rate, low, high) in {
        "L0": (12, 0.3636, 0.2245, 0.5302),
        "L1": (8, 0.2424, 0.1283, 0.4093),
        "L2": (3, 0.0909, 0.0314, 0.2357),
    }.items():
        layers[name] = {
            "n_scored": 33,
            "asr": {"all": wilson(hits, 33, rate, low, high)},
            "under_attack": {"not_found": wilson(2, 33, 0.0606, 0.0168, 0.1966)},
        }
    detector = {"recall_normalized": {"all": wilson(9, 33, 0.2727, 0.1508, 0.4428)}}
    action = {"status": "enforced by tests/security/test_containment.py", "tests": ["test_one"]}
    return {"status": status, "layers": layers, "detector": detector, "action_level": action}


def test_injection_attack_success_is_quoted_per_layer_from_a_measured_run(results_dir: Path, repo_root: Path) -> None:
    write_json(results_dir, "injection_metrics.json", full_injection_run())
    text = bullet_text(build_report(results_dir, repo_root), "document_extraction")
    assert "attack success by defense layer on 33 frozen payloads" in text
    assert "L0 0.3636 [Wilson 0.2245, 0.5302]" in text
    assert "L1 0.2424 [Wilson 0.1283, 0.4093]" in text
    assert "L2 0.0909 [Wilson 0.0314, 0.2357] with not-found rate 0.0606 [Wilson 0.0168, 0.1966]" in text


def test_a_partial_injection_run_is_not_quoted(results_dir: Path, repo_root: Path) -> None:
    write_json(results_dir, "injection_metrics.json", full_injection_run(status="partial"))
    report = build_report(results_dir, repo_root)
    assert "attack success" not in bullet_text(report, "document_extraction")


def test_no_forbidden_wording_in_the_output(results_dir: Path, repo_root: Path) -> None:
    write_json(results_dir, "injection_metrics.json", full_injection_run())
    report = build_report(results_dir, repo_root)
    text = "\n".join(render_projects(report)).lower()
    for phrase in FORBIDDEN_IN_OUTPUT:
        assert phrase not in text, phrase
    for bullet in report.bullets:
        check_wording(bullet.text)


def test_local_container_runs_are_never_called_a_deployment(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    assert "deploy" not in "\n".join(render_projects(report)).lower()
    assert "on a local container, 2 of 3 Playwright end-to-end flows passed" in bullet_text(report, "studio_platform")
    reasons = [item.reason for item in report.omissions if item.part == "deployment, cloud and IaC"]
    assert reasons and "a local container measurement is not a deployment" in reasons[0]


def test_model_card_and_ci_clauses_follow_the_repo_files(results_dir: Path, tmp_path: Path) -> None:
    bare = fake_repo(tmp_path / "bare", model_card=False, ci_gate=False)
    report = build_report(results_dir, bare)
    assert "SR 26-2" not in bullet_text(report, "credit_model")
    assert report.footnotes == []
    assert "a release gate (make release-gate)" in bullet_text(report, "governance")
    full = build_report(results_dir, fake_repo(tmp_path / "full"))
    assert "aligned to the structure of SR 26-2" in bullet_text(full, "credit_model")
    assert "a release gate in the CI workflow" in bullet_text(full, "governance")
    assert full.footnotes and "not a compliance claim" in full.footnotes[0]


def test_stress_testing_and_tracing_clauses_are_reported_as_left_out(results_dir: Path, repo_root: Path) -> None:
    report = build_report(results_dir, repo_root)
    stderr = "\n".join(render_omissions(report, []))
    assert "credit_model / stress testing" in stderr
    assert "LLM/tool-call tracing with cost and p95 per packet" in stderr


def test_studio_business_bullet_quotes_the_baseline_and_says_the_time_is_machine_time(
    results_dir: Path, repo_root: Path
) -> None:
    text = bullet_text(build_report(results_dir, repo_root), "studio_business")
    assert "0.8765 [0.8654, 0.8876] of printed figures carry a checkable source vs 0.0432 [0.0321, 0.0543]" in text
    assert "on 24 SYNTHETIC scenarios" in text
    assert "median 5.432 [5.123, 5.876] seconds from inputs to an audited PDF package (machine wall clock" in text
    assert "no language model is called" in text


def test_missing_studio_business_file_drops_its_bullet_with_the_reason(results_dir: Path, repo_root: Path) -> None:
    (results_dir / "studio_business_metrics.json").unlink()
    report = build_report(results_dir, repo_root)
    assert bullet_text(report, "studio_business") == ""
    stderr = "\n".join(render_omissions(report, []))
    assert "studio_business_metrics.json does not exist" in stderr


def test_committed_results_give_measured_bullets_only() -> None:
    """The real results folder: 4-6 lender bullets, and every number in them is a value of results/*.json."""
    report = build_report(RESULTS_DIR, REPO_ROOT)
    lender = [bullet for bullet in report.bullets if bullet.section == LENDER]
    assert 4 <= len(lender) <= 6
    allowed = allowed_numbers(RESULTS_DIR)
    for bullet in report.bullets:
        unknown = [token for token in numbers_in(bullet.text) if token not in allowed]
        assert not unknown, f"{bullet.bullet_id}: {unknown}"
