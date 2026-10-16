"""The LangGraph agent on SYNTHETIC startup credit files, end to end with the fake LLM and offline tools.

The run must reach the human checkpoint with a template memo that passes the claim checker (citing program
sections), call only the screening tool, record nothing without a human, and accept an approval only from a
senior underwriter (NSP-9.1).
"""

from pathlib import Path

import pytest
from agent_helpers import fake_handlers, make_deps, memory_agent, template_llm

from uwcopilot.agent.applications import load_application, load_applications
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.policy_startup import STARTUP_POLICY_VERSION
from uwcopilot.agent.runner import resume_run, start_run
from uwcopilot.agent.vocab import COMPLIANCE, FAKE_LLM_STAMP
from uwcopilot.common.paths import REPO_ROOT

STARTUP_DIR = REPO_ROOT / "eval" / "startup_applications"
UNDERWRITER_APPROVE = {
    "action": "approve",
    "reviewer_id": "uw.one",
    "role": "underwriter",
    "rationale": "The startup file meets the program.",
}
SENIOR_APPROVE = {**UNDERWRITER_APPROVE, "reviewer_id": "senior.one", "role": "senior_underwriter"}


def test_clean_startup_file_reaches_the_checkpoint_and_only_a_senior_can_approve(tmp_path: Path) -> None:
    application = load_application(STARTUP_DIR / "APP-S-001.json")
    llm = template_llm()
    agent = memory_agent(make_deps(tmp_path, llm=llm))
    outcome = start_run(agent, application.model_dump(mode="json"), "startup-1")

    assert outcome.status == "awaiting_review"
    review = outcome.review_request
    assert review["line"] == "startup_credit" and review["data_label"] == "SYNTHETIC"
    assert review["reference_recommendation"] == "approve" and review["memo_recommendation"] == "approve"
    assert review["memo_status"] == "passed" and review["memo_check"]["passed"] is True
    assert review["stamp"] == FAKE_LLM_STAMP
    assert review["required_reviewer_role"] == "senior_underwriter" and review["review_queue"] == "Senior Underwriter"
    assert [result["tool"] for result in review["tool_results"]] == ["screen_entity"] * 3
    assert {item["rule"] for item in review["not_evaluated"]} >= {"risk_model_pd", "simulation_outputs"}
    cited = {citation for reason in review["memo"]["reasons"] for citation in reason["citations"]}
    assert {"NSP-4.1", "NSP-3.1", "CALC-startup_projected_dscr"} <= cited
    assert DecisionLog(tmp_path / "decision_records.jsonl").records() == []

    prompt = llm.calls[0]["messages"][0].content
    assert application.applicant.business_name not in prompt
    assert application.applicant.site_description not in prompt
    assert application.ground_truth.scenario not in prompt

    refused = resume_run(agent, "startup-1", UNDERWRITER_APPROVE)
    assert refused.status == "awaiting_review"
    errors = refused.review_request["previous_rejections"][0]["errors"]
    assert "only a senior_underwriter may approve a startup file (NSP-9.1)" in errors
    assert DecisionLog(tmp_path / "decision_records.jsonl").records() == []

    done = resume_run(agent, "startup-1", SENIOR_APPROVE)
    assert done.status == "completed" and done.state["status"] == "approved_by_human"
    log = DecisionLog(tmp_path / "decision_records.jsonl")
    [record] = log.records()
    assert log.verify_chain() == []
    assert record["versions"]["policy_version"] == STARTUP_POLICY_VERSION
    assert record["reviewer"]["role"] == "senior_underwriter" and record["reviewer"]["is_override"] is False
    assert len(record["rejected_reviewer_attempts"]) == 1
    assert all(call["tool"] == "screen_entity" for call in record["tool_calls"])


@pytest.mark.parametrize("path", sorted(STARTUP_DIR.glob("APP-S-*.json")), ids=lambda path: path.stem)
def test_every_startup_example_reaches_the_checkpoint_with_a_checked_memo(path: Path, tmp_path: Path) -> None:
    application = load_application(path)
    outcome = start_run(memory_agent(make_deps(tmp_path)), application.model_dump(mode="json"), "t")
    review = outcome.review_request
    assert outcome.status == "awaiting_review" and outcome.state["decision_record"] is None
    assert review["reference_recommendation"] == application.ground_truth.expected_recommendation
    assert review["memo_check"]["passed"] is True and review["memo_status"] == "passed"
    assert review["required_reviewer_role"] == "senior_underwriter"


def test_flagged_startup_owner_goes_to_compliance_and_blocks_approval(tmp_path: Path) -> None:
    application = load_applications(STARTUP_DIR)[0]
    flagged = application.owners[1].name
    deps = make_deps(tmp_path, handlers=fake_handlers(flagged_names=[flagged]))
    agent = memory_agent(deps)
    review = start_run(agent, application.model_dump(mode="json"), "t").review_request
    assert review["reference_recommendation"] == "refer" and review["blocking_open"] is True
    assert review["review_queue"] == COMPLIANCE and review["memo_check"]["passed"] is True
    assert "approve" not in review["allowed_actions"]
    waiting = resume_run(agent, "t", SENIOR_APPROVE)
    assert waiting.status == "awaiting_review"
    assert any("cannot approve" in error for error in waiting.review_request["previous_rejections"][0]["errors"])
