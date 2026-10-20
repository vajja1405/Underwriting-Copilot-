"""Tests for scripts/lineage.py: print one record's lineage and flag STALE values (offline, fake repository)."""

from pathlib import Path

import pytest
from lineage_helpers import fake_repo, lineage_for

import lineage as lineage_script
from uwcopilot.agent.decision_record import DecisionLog


def write_log(root: Path, with_lineage: bool = True) -> Path:
    """A decision log with one record (and its lineage built against the fake repository)."""
    log = DecisionLog(root / "log.jsonl")
    record = {
        "record_id": "DR-1",
        "application_id": "APP-C-001",
        "data_label": "SYNTHETIC",
        "policy_result": {"line": "credit"},
        "reference_recommendation": "approve",
        "final_status": "approved_by_human",
        "versions": {"graph_version": "g1", "prompt_version": "memo-v1"},
        "reviewer": {"action": "approve", "role": "underwriter", "is_override": False, "override_reason": None},
        "reviewer_label": "SIMULATED",
    }
    if with_lineage:
        record["lineage"] = lineage_for(root)
    log.append(record)
    return log.path


def test_prints_the_chain_and_marks_everything_ok(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = fake_repo(tmp_path)
    log = write_log(root)
    assert lineage_script.main([str(log), "DR-1"], repo_root=root) == 0
    out = capsys.readouterr().out
    assert "Decision record DR-1 - application APP-C-001 (credit, SYNTHETIC)" in out
    assert "review action accept (mapped from is_override: record written before Phase 6)" in out
    assert "credit: credit_pd v1" in out and "score_risk in this decision: ok, served m1" in out
    assert "insurance_frequency: freq v4" in out and "the model did not score this file" in out
    assert "dataset lending_club/loans.zip" in out and "[STALE" not in out
    assert "[credit] commit aaaaaaaaaaaa..." in out  # the provenance of the results that validated the model
    assert "Log chain: intact" in out
    assert "Summary:" in out and "0 STALE" in out and "-> current" in out


def test_a_changed_prompt_is_stale_and_strict_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = fake_repo(tmp_path)
    log = write_log(root)
    (root / "prompts" / "memo_prompt.py").write_text("a new prompt\n", encoding="utf-8")
    assert lineage_script.main([str(log), "DR-1"], repo_root=root) == 0
    assert "[STALE: recorded" in capsys.readouterr().out
    assert lineage_script.main([str(log), "DR-1", "--strict"], repo_root=root) == lineage_script.EXIT_STALE


def test_absent_data_is_not_checkable(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = fake_repo(tmp_path)
    log = write_log(root)
    (root / "data" / "raw" / "lending_club" / "loans.zip").unlink()
    assert lineage_script.main([str(log), "DR-1", "--strict"], repo_root=root) == 0
    assert "data file [NOT CHECKABLE: data file not present]" in capsys.readouterr().out


def test_old_records_and_missing_records(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = fake_repo(tmp_path)
    log = write_log(root, with_lineage=False)
    assert lineage_script.main([str(log), "DR-1"], repo_root=root) == 0
    assert "has no lineage block" in capsys.readouterr().out
    assert lineage_script.main([str(log), "DR-404"], repo_root=root) == lineage_script.EXIT_NOT_FOUND
    assert lineage_script.main([str(root / "none.jsonl"), "DR-1"], repo_root=root) == lineage_script.EXIT_NOT_FOUND
