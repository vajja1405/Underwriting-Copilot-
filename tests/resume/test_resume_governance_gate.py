"""Tests for the governance bullet's release-gate clause: the verdict and the MISSING count are read, not only PASS,
FAIL and NOT_MEASURED, and only a PASS verdict with no blocking row is quoted."""

import json
from pathlib import Path
from typing import Any

import pytest
from resume_fixtures import fake_repo, write_results

from uwcopilot.resume.report import ResumeReport, build_report

GATE_FILE = "governance_metrics.json"


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """A complete fixture results folder."""
    return write_results(tmp_path / "results")


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """A repo root with a CI workflow that runs the release gate."""
    return fake_repo(tmp_path / "repo")


def set_gate(results: Path, gate: Any) -> None:
    """Replace release_gate.results of the fixture governance_metrics.json with gate."""
    path = results / GATE_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["release_gate"]["results"] = gate
    path.write_text(json.dumps(data), encoding="utf-8")


def gate(verdict: str | None, **counts: int) -> dict[str, Any]:
    """A release_gate.results section with the given verdict (left out when None) and counts."""
    section: dict[str, Any] = {"counts": {"rows": 15, "PASS": 10, "FAIL": 0, "MISSING": 0, "NOT_MEASURED": 4, **counts}}
    if verdict is not None:
        section["verdict"] = verdict
    return section


def governance_text(report: ResumeReport) -> str:
    """The governance bullet ('' when it was left out)."""
    return next((bullet.text for bullet in report.bullets if bullet.bullet_id == "governance"), "")


def gate_reasons(report: ResumeReport) -> list[str]:
    """The reasons recorded for leaving the release-gate clause out."""
    return [item.reason for item in report.omissions if item.bullet_id == "governance" and item.part == "release gate"]


def test_passing_gate_prints_the_verdict_and_the_missing_count(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, gate("PASS", PASS=11))
    text = governance_text(build_report(results_dir, repo_root))
    assert "(latest run: verdict PASS, 11 pass, 0 fail, 0 missing, 4 not measured)" in text


def test_failing_verdict_with_a_missing_row_is_not_quoted(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, gate("FAIL", MISSING=1))
    report = build_report(results_dir, repo_root)
    assert governance_text(report) == ""
    assert "10 pass, 0 fail" not in "\n".join(bullet.text for bullet in report.bullets)
    reasons = gate_reasons(report)
    assert len(reasons) == 1
    assert "release gate verdict is FAIL" in reasons[0]
    assert "10 pass, 0 fail, 1 missing, 4 not measured" in reasons[0]
    assert any(item.part == "whole bullet" for item in report.omissions if item.bullet_id == "governance")


def test_failing_verdict_with_a_failed_row_is_not_quoted(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, gate("FAIL", PASS=9, FAIL=1))
    report = build_report(results_dir, repo_root)
    assert governance_text(report) == ""
    assert "release gate verdict is FAIL" in gate_reasons(report)[0]


def test_pass_verdict_with_a_blocking_row_is_reported_as_inconsistent(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, gate("PASS", MISSING=1))
    report = build_report(results_dir, repo_root)
    assert governance_text(report) == ""
    assert "inconsistent" in gate_reasons(report)[0]


def test_gate_without_a_verdict_is_not_quoted(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, gate(None))
    report = build_report(results_dir, repo_root)
    assert governance_text(report) == ""
    assert gate_reasons(report) == [
        "not measured: results/governance_metrics.json has no usable release_gate.results.verdict"
    ]


def test_gate_without_a_missing_count_is_not_quoted(results_dir: Path, repo_root: Path) -> None:
    section = gate("PASS")
    del section["counts"]["MISSING"]
    set_gate(results_dir, section)
    report = build_report(results_dir, repo_root)
    assert governance_text(report) == ""
    assert gate_reasons(report) == [
        "not measured: results/governance_metrics.json has no usable release_gate.results.counts"
    ]


def test_gate_marked_not_measured_quotes_the_file(results_dir: Path, repo_root: Path) -> None:
    set_gate(results_dir, "not measured (no baseline)")
    report = build_report(results_dir, repo_root)
    assert gate_reasons(report) == ["results/governance_metrics.json says: not measured (no baseline)"]
