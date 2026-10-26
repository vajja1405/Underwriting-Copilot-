"""Every decision record the graph writes carries a lineage block (Phase 6 A); older records still load."""

import json
from pathlib import Path

from agent_helpers import SENIOR_REFER, committed_by_scenario, make_deps, memory_agent

from uwcopilot.agent import policy as policy_module
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.graph import MEMO_PROMPT_SOURCE
from uwcopilot.agent.memo import PROMPT_VERSION
from uwcopilot.agent.runner import resume_run, start_run
from uwcopilot.common.files import sha256_of_file
from uwcopilot.governance.feedback import summarize_feedback
from uwcopilot.governance.lineage import LINEAGE_VERSION, verify_lineage


def decided_record(tmp_path: Path, scenario: str, line: str | None = None, **deps_changes: object) -> dict:
    """Run one committed application to the checkpoint, refer it as a senior underwriter, return the record."""
    application = committed_by_scenario(scenario, line=line)
    deps = make_deps(tmp_path)
    for name, value in deps_changes.items():
        setattr(deps, name, value)
    agent = memory_agent(deps)
    start_run(agent, application.model_dump(mode="json"), "t")
    assert resume_run(agent, "t", SENIOR_REFER).status == "completed"
    [record] = DecisionLog(tmp_path / "decision_records.jsonl").records()
    return record


def test_the_record_names_the_model_prompt_file_and_eval_sets(tmp_path: Path) -> None:
    record = decided_record(tmp_path, "clean_term_dental")
    lineage = record["lineage"]
    assert lineage["lineage_version"] == LINEAGE_VERSION
    [model] = lineage["models"]
    [risk_call] = [call for call in record["tool_calls"] if call["tool"] == "score_risk"]
    assert model["product"] == "credit" and model["tool_status"] == risk_call["status"]
    assert model["results_file"] == "results/risk_metrics.json" and model["results_section"] == "credit"
    prompt = lineage["prompts"]["agent_memo_prompt"]
    assert prompt["file"] == "src/uwcopilot/agent/memo.py" and prompt["prompt_version"] == PROMPT_VERSION
    assert prompt["sha256"] == sha256_of_file(MEMO_PROMPT_SOURCE.path)
    assert lineage["eval_sets"]["agent_applications"]["set_hash_field"] == "applications_sha256"
    assert lineage["eval_sets"]["docintel_packets_test"]["set_hash_field"] == "test_truth_sha256"
    policy_file = Path(policy_module.__file__)
    assert lineage["decision_logic"]["src/uwcopilot/agent/policy.py"] == sha256_of_file(policy_file)
    assert "src/uwcopilot/agent/hitl.py" in lineage["decision_logic"]
    checks = verify_lineage(lineage, hash_data_files=False, prompt_labels={"agent_memo_prompt": PROMPT_VERSION})
    assert not [check for check in checks if check.status == "STALE"]
    assert any(check.kind == "decision_logic_file" and check.status == "ok" for check in checks)


def test_the_lineage_builder_can_be_replaced(tmp_path: Path) -> None:
    record = decided_record(tmp_path, "clean_term_dental", lineage_builder=lambda results: {"fixed": len(results)})
    assert record["lineage"] == {"fixed": len(record["tool_calls"])}


def test_insurance_files_name_the_frequency_model(tmp_path: Path) -> None:
    record = decided_record(tmp_path, "clean_office", line="insurance")
    assert [model["product"] for model in record["lineage"]["models"]] == ["insurance_frequency"]


def test_a_record_written_before_phase_6_still_loads_and_reads(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "old.jsonl")
    old = {
        "record_id": "DR-old",
        "policy_result": {"line": "credit"},
        "reference_recommendation": "refer",
        "reviewer": {"action": "approve", "role": "senior_underwriter", "is_override": True, "override_reason": "x"},
    }
    log.append(old)
    reread = DecisionLog(tmp_path / "old.jsonl")
    assert reread.verify_chain() == [] and "lineage" not in reread.find("DR-old")
    summary = summarize_feedback(reread.records(), known_codes=["x"])
    assert summary["overrides"] == 1 and summary["legacy_records_mapped"] == 1
    json.dumps(summary)
