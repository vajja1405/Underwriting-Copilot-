"""Review feedback at the human checkpoint (Phase 6 C): reason codes, legacy override_reason, review_action."""

from pathlib import Path

from agent_helpers import committed_by_scenario, credit_app, make_deps, memory_agent

from uwcopilot.agent.applications import screened_parties
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.hitl import APPROVED_OVERRIDE_REASONS, RESUME_INSTRUCTIONS, HumanDecision, validate_decision
from uwcopilot.agent.policy import PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.runner import resume_run, start_run
from uwcopilot.governance.feedback import FEEDBACK_VERSION, override_reason_codes

SENIOR = {"reviewer_id": "senior.one", "role": "senior_underwriter", "rationale": "Reviewed the full file."}


def refer_policy_case() -> tuple:
    """A credit file whose reference recommendation is refer (approving it is an override)."""
    application = credit_app(industry="freight_broker")
    screening = [ScreeningOutcome(party_id=party.party_id, status="clear") for party in screened_parties(application)]
    policy = evaluate_policy(application, PolicyInputs(screening=screening))
    assert policy.recommendation == "refer"
    return application, policy


def test_the_checkpoint_uses_the_configured_reason_list() -> None:
    assert APPROVED_OVERRIDE_REASONS == override_reason_codes()
    assert all(code in RESUME_INSTRUCTIONS for code in APPROVED_OVERRIDE_REASONS)


def test_an_override_with_reason_codes_only_is_accepted_and_mapped_to_the_legacy_field() -> None:
    application, policy = refer_policy_case()
    codes = ["documented_compensating_factor", "information_not_available_to_model"]
    accepted, errors = validate_decision({"action": "approve", **SENIOR, "reason_codes": codes}, policy, application)
    assert errors == [] and accepted is not None
    assert accepted.reason_codes == codes and accepted.override_reason == codes[0]


def test_a_legacy_override_reason_becomes_the_first_reason_code() -> None:
    decision = HumanDecision.model_validate(
        {
            "action": "approve",
            **SENIOR,
            "override_reason": "policy_exception_approved",
            "reason_codes": ["data_error_in_model_input"],
        }
    )
    assert decision.reason_codes == ["policy_exception_approved", "data_error_in_model_input"]
    assert decision.override_reason == "policy_exception_approved"


def test_an_override_without_any_reason_is_refused() -> None:
    application, policy = refer_policy_case()
    accepted, errors = validate_decision({"action": "approve", **SENIOR, "reason_codes": []}, policy, application)
    assert accepted is None and any("override_reason or reason_codes" in error for error in errors)


def test_unknown_duplicate_and_overlong_reasons_are_refused() -> None:
    application, policy = refer_policy_case()
    for extra in (
        {"reason_codes": ["because_i_said_so"]},
        {"reason_codes": ["policy_exception_approved", "policy_exception_approved"]},
        {"reason_codes": ["policy_exception_approved"], "reason_text": "x" * 1001},
        {"reason_codes": ["policy_exception_approved"], "reason_text": ""},
        {"reason_codes": "policy_exception_approved"},
    ):
        accepted, errors = validate_decision({"action": "approve", **SENIOR, **extra}, policy, application)
        assert accepted is None and errors, extra


def test_the_record_stores_review_action_reasons_and_reviewer_label(tmp_path: Path) -> None:
    application = committed_by_scenario("prohibited_industry")
    agent = memory_agent(make_deps(tmp_path))
    start_run(agent, application.model_dump(mode="json"), "t")
    decline = {
        "action": "decline",
        **SENIOR,
        "rationale": "Prohibited industry per NBC-2.2.",
        "reason_text": "A free-text note about the reasons.",
    }
    done = resume_run(agent, "t", decline)
    assert done.status == "completed"
    [stored] = DecisionLog(tmp_path / "decision_records.jsonl").records()
    reviewer = stored["reviewer"]
    assert reviewer["review_action"] == "accept" and reviewer["is_override"] is False
    assert reviewer["reason_codes"] == [] and reviewer["reason_text"] == "A free-text note about the reasons."
    assert reviewer["feedback_version"] == FEEDBACK_VERSION
    assert stored["reviewer_label"] == "human"


def test_an_override_record_and_a_simulated_reviewer_label(tmp_path: Path) -> None:
    application = committed_by_scenario("clean_term_dental")
    deps = make_deps(tmp_path)
    deps.reviewer_label = "SIMULATED"
    agent = memory_agent(deps)
    start_run(agent, application.model_dump(mode="json"), "t")
    decline = {"action": "decline", **SENIOR, "reason_codes": ["data_error_in_model_input"]}
    done = resume_run(agent, "t", decline)
    assert done.status == "completed" and done.state["status"] == "declined_by_human"
    [stored] = DecisionLog(tmp_path / "decision_records.jsonl").records()
    assert stored["reviewer"]["review_action"] == "override" and stored["reviewer"]["is_override"] is True
    assert stored["reviewer"]["override_reason"] == "data_error_in_model_input"
    assert stored["reviewer_label"] == "SIMULATED"
