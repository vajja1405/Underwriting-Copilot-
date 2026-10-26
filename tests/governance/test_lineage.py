"""Tests for governance/lineage.py: building the lineage block and checking it against the files now (offline)."""

import hashlib
import json
from pathlib import Path

from lineage_helpers import DATA_SHA, EVAL_SETS, fake_repo, lineage_for, prompt_source, risk_results, write_json

from uwcopilot.governance.lineage import (
    DECISION_LOGIC_FILES,
    LINEAGE_VERSION,
    build_decision_lineage,
    overall_status,
    summarize_lineage_checks,
    verify_lineage,
)


def statuses(lineage: dict, root: Path, **options: object) -> dict[tuple[str, str], str]:
    """{(kind, item): status} of every check."""
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw", **options)
    return {(check.kind, check.item): check.status for check in checks}


def test_the_block_names_models_datasets_prompts_eval_sets_and_results(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    assert lineage["lineage_version"] == LINEAGE_VERSION
    assert lineage["code"]["git_sha"] == "unknown"  # tmp_path is not a git repository
    credit, insurance = lineage["models"]
    assert credit["product"] == "credit" and credit["model_version_label"] == "credit_pd v1"
    assert credit["tool_status"] == "ok" and credit["served_model_version"] == "m1"
    assert credit["measured_at_git_commit"] == "a" * 40
    assert credit["datasets"]["lending_club"][0]["sha256"] == DATA_SHA
    assert insurance["model_version_label"] == "freq v4" and insurance["served_model_version"] is None
    prompt_bytes = (root / "prompts" / "memo_prompt.py").read_bytes()
    assert lineage["prompts"]["memo"] == {
        "file": "prompts/memo_prompt.py",
        "sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "prompt_version": "memo-v1",
        "note": "test prompt",
    }
    assert lineage["eval_sets"]["docintel_packets_test"]["set_sha256"] == "e" * 64
    assert set(lineage["results_files"]) == {"results/risk_metrics.json", "results/agent_metrics.json"}
    rule_bytes = (root / "agent" / "policy.py").read_bytes()
    assert lineage["decision_logic"] == {"agent/policy.py": hashlib.sha256(rule_bytes).hexdigest()}
    assert lineage["not_recorded"] == []
    json.dumps(lineage)  # plain JSON, ready for the decision record


def test_the_default_decision_logic_is_the_agents_rule_files() -> None:
    names = [path.name for path in DECISION_LOGIC_FILES]
    assert names == ["policy.py", "policy_credit.py", "policy_insurance.py", "policy_startup.py", "hitl.py", "graph.py"]
    assert all(path.is_file() and path.parent.name == "agent" for path in DECISION_LOGIC_FILES)


def test_a_startup_decision_calls_no_model(tmp_path: Path) -> None:
    lineage = lineage_for(fake_repo(tmp_path), tool_results=[{"tool": "screen_entity", "status": "ok"}])
    assert lineage["models"] == []


def test_missing_files_are_reported_never_guessed(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    (root / "results" / "risk_metrics.json").unlink()
    (root / "prompts" / "memo_prompt.py").unlink()
    lineage = lineage_for(root)
    assert all("not_recorded" in model for model in lineage["models"])
    assert lineage["prompts"] == {}
    assert any("results/risk_metrics.json is not present" in reason for reason in lineage["not_recorded"])
    assert any("prompt memo" in reason for reason in lineage["not_recorded"])
    found = statuses(lineage, root)
    assert found[("model_version", "credit")] == "not_checkable"
    # Each missing part is one not_checkable check: a model's own reason is not counted a second time.
    model_reason = lineage["models"][0]["not_recorded"]
    assert ("not_recorded", model_reason) not in found
    assert found[("not_recorded", "prompt memo: prompts/memo_prompt.py is not present")] == "not_checkable"


def test_a_partly_recorded_lineage_is_never_called_current(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    (root / "results" / "agent_metrics.json").unlink()
    (root / "eval" / "startup_applications" / "manifest.json").unlink()
    lineage = lineage_for(root)
    found = statuses(lineage, root)
    assert found[("not_recorded", "results file results/agent_metrics.json is not present")] == "not_checkable"
    assert "STALE" not in found.values()
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw")
    assert overall_status(checks) == "current_where_checkable"
    summary = summarize_lineage_checks([{"lineage": lineage}], repo_root=root, raw_dir=root / "data" / "raw")
    assert summary["records_by_status"] == {"current_where_checkable": 1}


def test_a_lineage_with_no_check_at_all_is_not_current(tmp_path: Path) -> None:
    lineage = build_decision_lineage([], eval_sets={}, validating_results=(), repo_root=tmp_path, decision_logic=())
    checks = verify_lineage(lineage, repo_root=tmp_path, raw_dir=tmp_path / "data" / "raw")
    assert checks == [] and overall_status(checks) == "current_where_checkable"


def test_an_older_block_without_decision_logic_cannot_be_checked_for_rule_changes(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    del lineage["decision_logic"]
    found = statuses(lineage, root)
    assert found[("decision_logic_file", "decision logic")] == "not_checkable"
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw")
    assert overall_status(checks) == "current_where_checkable"


def test_a_changed_rule_is_stale_but_a_new_commit_alone_is_not(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    lineage["code"] = {"git_sha": "f" * 40, "git_dirty": True}  # the code commit is shown, never compared
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw")
    assert overall_status(checks) == "current"
    (root / "agent" / "policy.py").write_text("DSCR_MINIMUM = 1.10  # SYNTHETIC rule\n", encoding="utf-8")
    assert statuses(lineage, root)[("decision_logic_file", "agent/policy.py")] == "STALE"
    (root / "agent" / "policy.py").unlink()
    assert statuses(lineage, root)[("decision_logic_file", "agent/policy.py")] == "not_checkable"


def test_an_unchanged_repository_is_current(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    found = statuses(lineage, root, prompt_labels={"memo": "memo-v1"})
    assert set(found.values()) == {"ok"}
    assert found[("dataset_file", "lending_club/loans.zip")] == "ok"
    assert found[("dataset_checksum_log", "lending_club/loans.zip")] == "ok"
    assert found[("prompt_version", "memo")] == "ok"
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw")
    assert overall_status(checks) == "current"


def test_every_kind_of_change_is_flagged_stale(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    (root / "prompts" / "memo_prompt.py").write_text('SYSTEM_PROMPT = "You draft different memos."\n', encoding="utf-8")
    write_json(root / "eval" / "packets" / "manifest.json", {"test_truth_sha256": "f" * 64})
    write_json(root / "results" / "risk_metrics.json", risk_results(credit_version="2"))
    (root / "data" / "raw" / "lending_club" / "loans.zip").write_bytes(b"other rows\n")
    (root / "agent" / "policy.py").write_text("DSCR_MINIMUM = 1.10  # SYNTHETIC rule\n", encoding="utf-8")
    found = statuses(lineage, root, prompt_labels={"memo": "memo-v2"})
    for key in [
        ("decision_logic_file", "agent/policy.py"),
        ("prompt_file", "prompts/memo_prompt.py"),
        ("prompt_version", "memo"),
        ("eval_set_file", "eval/packets/manifest.json"),
        ("eval_set_hash", "eval/packets/manifest.json:test_truth_sha256"),
        ("results_file", "results/risk_metrics.json"),
        ("model_version", "results/risk_metrics.json:credit"),
        ("dataset_file", "lending_club/loans.zip"),
    ]:
        assert found[key] == "STALE", key
    assert found[("model_version", "results/risk_metrics.json:insurance_frequency")] == "ok"
    assert found[("eval_set_file", "eval/applications/manifest.json")] == "ok"


def test_absent_data_and_log_lines_are_not_checkable(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    (root / "data" / "raw" / "lending_club" / "loans.zip").unlink()
    (root / "data" / "raw" / "checksums.jsonl").write_text("", encoding="utf-8")
    found = statuses(lineage, root)
    assert found[("dataset_file", "lending_club/loans.zip")] == "not_checkable"
    assert found[("dataset_checksum_log", "lending_club/loans.zip")] == "not_checkable"
    checks = verify_lineage(lineage, repo_root=root, raw_dir=root / "data" / "raw")
    assert overall_status(checks) == "current_where_checkable"
    skipped = statuses(lineage, root, hash_data_files=False)
    assert not any(kind == "dataset_file" for kind, _ in skipped)


def test_a_changed_checksum_log_line_is_stale(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    lineage = lineage_for(root)
    line = {"dataset": "lending_club", "file": "loans.zip", "sha256": "0" * 64}
    with (root / "data" / "raw" / "checksums.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")  # the latest line wins
    assert statuses(lineage, root)[("dataset_checksum_log", "lending_club/loans.zip")] == "STALE"


def test_summary_counts_records_by_status(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    current = {"record_id": "DR-1", "lineage": lineage_for(root)}
    old = {"record_id": "DR-0"}  # written before Phase 6: no lineage block
    (root / "prompts" / "memo_prompt.py").write_text("changed\n", encoding="utf-8")
    stale = {"record_id": "DR-2", "lineage": lineage_for(root)}
    (root / "prompts" / "memo_prompt.py").write_text('SYSTEM_PROMPT = "You draft memos."\n', encoding="utf-8")
    summary = summarize_lineage_checks([current, old, stale], repo_root=root, raw_dir=root / "data" / "raw")
    assert summary["records_read"] == 3 and summary["records_with_lineage"] == 2
    assert summary["records_by_status"] == {"STALE": 1, "current": 1, "no_lineage_block": 1}
    assert summary["stale_items"] == {"prompt_file:prompts/memo_prompt.py": 1}


def test_build_accepts_an_explicit_risk_results_path(tmp_path: Path) -> None:
    root = fake_repo(tmp_path)
    moved = root / "elsewhere.json"
    (root / "results" / "risk_metrics.json").rename(moved)
    lineage = build_decision_lineage(
        [{"tool": "score_risk", "arguments": {"product": "credit"}, "status": "ok", "output": {"model_version": "m"}}],
        prompts=(prompt_source(root),),
        eval_sets=EVAL_SETS,
        risk_results_file=moved,
        validating_results=(),
        repo_root=root,
    )
    assert lineage["models"][0]["model_version_label"] == "credit_pd v1" and lineage["results_files"] == {}
