"""handoff (Studio S6): the existing agent graph runs on the founder package up to the human checkpoint with the
template memo, never decides, and the lender review request carries the founder ledger and next-best evidence."""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from package_helpers import log_dir, offline_handlers, package, screen_flagging

from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps
from uwcopilot.agent.runner import compile_agent, resume_run
from uwcopilot.agent.vocab import COMPLIANCE, FAKE_LLM_STAMP
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package import handoff, to_application

NO_DOCUMENTS = ("synthetic_metro", "car_wash")
MATCHING_STATEMENTS = ("synthetic_metro", "laundromat")
CONFLICTING_STATEMENTS = ("synthetic_metro", "motel")


def test_the_agent_stops_at_the_human_checkpoint_with_a_checked_template_memo(tmp_path: Path) -> None:
    built = package(*NO_DOCUMENTS)
    result = handoff(built, tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path))
    assert result.status == "awaiting_review" and result.reached_checkpoint
    assert result.decision_recorded_without_human is False
    assert result.memo_status == "passed" and result.memo_check_passed is True and result.unsupported_numbers == 0
    agent = result.review_request.agent
    assert agent["line"] == "startup_credit" and agent["stamp"] == FAKE_LLM_STAMP
    assert agent["required_reviewer_role"] == "senior_underwriter"
    assert [item["tool"] for item in agent["tool_results"]] == ["screen_entity", "screen_entity"]
    assert result.evidence_engine_recommendation == result.reference_recommendation
    assert DecisionLog(log_dir(tmp_path) / "decision_records.jsonl").records() == []


def test_the_review_request_carries_the_founder_ledger_with_statuses(tmp_path: Path) -> None:
    built = package(*NO_DOCUMENTS)
    review = handoff(built, tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path)).review_request
    assert len(review.founder_ledger) == len(built.ledger.claims)
    by_id = {item.claim_id: item for item in review.founder_ledger}
    assert by_id[ids.FOUNDER_EQUITY].status == "USER_STATED" and by_id[ids.FOUNDER_EQUITY].material
    assert by_id[ids.FOUNDER_EQUITY].source.startswith("[")
    assert review.status_counts == built.ledger.status_counts()
    assert review.disclaimer.startswith("Educational planning tool")


def test_next_best_evidence_asks_for_the_unverified_material_claims(tmp_path: Path) -> None:
    review = handoff(package(*NO_DOCUMENTS), tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path)).review_request
    requests = {request.request_id: request for request in review.evidence_requests}
    assert "equity_bank_statements" in requests
    assert requests["equity_bank_statements"].founder_claims[ids.FOUNDER_EQUITY] == "USER_STATED"
    drivers = [request for request in review.evidence_requests if request.request_id.startswith("driver_")]
    assert {request.params["driver"] for request in drivers} == {"1", "2", "3"}
    for request in drivers:
        assert set(request.founder_claims.values()) <= {"MODEL_ESTIMATE", "ASSUMPTION", "USER_STATED", "UNKNOWN"}
    assert [request.rank for request in review.evidence_requests] == list(range(1, len(review.evidence_requests) + 1))
    assert all(request.reason.startswith(("Requesting", '"')) for request in review.evidence_requests)


def test_verified_equity_is_not_requested_and_conflicting_equity_is(tmp_path: Path) -> None:
    verified = handoff(package(*MATCHING_STATEMENTS), tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path))
    assert "equity_bank_statements" not in {request.request_id for request in verified.review_request.evidence_requests}
    conflicting = handoff(package(*CONFLICTING_STATEMENTS), tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path))
    requests = {request.request_id: request for request in conflicting.review_request.evidence_requests}
    assert requests["equity_bank_statements"].founder_claims[ids.FOUNDER_EQUITY] == "CONFLICTING"


def test_a_screening_match_goes_to_compliance_and_still_waits_for_a_human(tmp_path: Path) -> None:
    built = package(*NO_DOCUMENTS)
    owner = built.applicant.owners[0].name
    result = handoff(built, tool_handlers={"screen_entity": screen_flagging({owner})}, log_dir=log_dir(tmp_path))
    agent = result.review_request.agent
    assert result.reached_checkpoint and agent["blocking_open"] is True and agent["review_queue"] == COMPLIANCE
    assert "approve" not in agent["allowed_actions"]


def test_only_a_human_can_finish_the_run(tmp_path: Path) -> None:
    built = package(*NO_DOCUMENTS)
    saver = InMemorySaver()
    log = DecisionLog(log_dir(tmp_path) / "decision_records.jsonl")
    result = handoff(built, tool_handlers=offline_handlers(), checkpointer=saver, decision_log=log)
    assert log.records() == []
    agent = compile_agent(
        AgentDeps(llm=FakeLLM(template_memo_responder), tool_handlers=offline_handlers(), decision_log=log), saver
    )
    decision = {
        "action": "request_more_information",
        "reviewer_id": "senior.one",
        "role": "senior_underwriter",
        "rationale": "Ask the founder for the bank statements and the checklist documents.",
    }
    done = resume_run(agent, result.thread_id, decision)
    assert done.status == "completed" and len(log.records()) == 1


def test_the_handoff_application_is_the_package_application(tmp_path: Path) -> None:
    built = package(*NO_DOCUMENTS)
    result = handoff(built, tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path))
    assert result.application_id == to_application(built).application_id
    assert result.review_request.package_id == built.package_id
