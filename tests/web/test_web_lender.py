"""Lender Desk demo API: the queue, the agent run to the human checkpoint, guideline sections, DEMO decisions through
the graph's own checkpoint, decision records, and the access rules.

The files are the repo's SYNTHETIC applications (eval/applications, eval/startup_applications). The tools are
offline stand-ins: screening answers "clear" against a fake OFAC snapshot, and the guideline search and the risk
model report "unavailable" (as they do when the index or the risk service is missing). The memo is the
deterministic template memo; no language model is called. Nothing touches the network.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from web_helpers import csrf_headers, dev_login

from uwcopilot.agent.decision_record import record_hash
from uwcopilot.tools.registry import ToolUnavailable
from uwcopilot.tools.schemas import ListSnapshot, ScreeningRequest, ScreeningResult
from uwcopilot.web.app import create_app
from uwcopilot.web.services.compute import ComputeRunner
from uwcopilot.web.services.lender_demo import CachedTool, GuidelineSearchAdapter, LenderDemoService
from uwcopilot.web.services.settings import DEFAULT_DEMO_DIRS, ServiceSettings

CLEAN_CREDIT = "APP-C-001"  # SYNTHETIC; the policy suggests approve; 90,000 USD (inside an underwriter's limit)
STARTUP = "APP-S-001"  # SYNTHETIC startup file: only a senior_underwriter may approve (NSP-9.1)
INJECTED = "APP-C-008"  # SYNTHETIC file whose notes hold an embedded instruction (a blocking referral)
FAKE_SNAPSHOT = ListSnapshot(source="OFAC", file_name="sdn.csv", sha256="c" * 64, snapshot_date="2026-10-12")


def fake_handlers() -> dict[str, Any]:
    """Offline tools: screening is always clear; the search and the risk model are unavailable."""

    def screen(request: ScreeningRequest) -> ScreeningResult:
        return ScreeningResult(request=request, candidates=[], status="clear", list_snapshots=[FAKE_SNAPSHOT])

    def unavailable(request: Any) -> Any:
        raise ToolUnavailable("not available in this offline test")

    return {"search_guidelines": unavailable, "screen_entity": screen, "score_risk": unavailable}


@pytest.fixture
def lender(make_settings) -> Iterator[TestClient]:
    """A dev-login app with offline tools, signed in as a lender_reviewer."""
    with TestClient(create_app(make_settings(UWC_DEV_AUTH="1"))) as client:
        client.app.state.services.lender = LenderDemoService(DEFAULT_DEMO_DIRS, handlers_factory=fake_handlers)
        dev_login(client, "reviewer_a", "lender_reviewer")
        yield client


def decide(client: TestClient, file_id: str, action: str, **extra: Any) -> Any:
    """POST a decision with a valid rationale."""
    body = {"action": action, "rationale": "Reviewed the SYNTHETIC demo file in a test.", **extra}
    return client.post(f"/api/lender/files/{file_id}/decision", json=body, headers=csrf_headers(client))


# --- queue ---


def test_queue_lists_synthetic_demo_files(lender: TestClient) -> None:
    body = lender.get("/api/lender/queue").json()
    assert body["label"] == "SYNTHETIC DEMO" and body["notice"] == "The copilot never decides — a person does."
    assert body["total"] == len(body["files"]) == sum(line["count"] for line in body["lines"]) > 0
    assert {line["id"] for line in body["lines"]} == {"credit", "insurance", "startup_credit"}
    item = next(item for item in body["files"] if item["id"] == CLEAN_CREDIT)
    assert item["line"] == "credit" and item["label"] == "SYNTHETIC DEMO" and item["requested_amount"] > 0
    assert "ground_truth" not in lender.get("/api/lender/queue").text


def test_queue_filters_by_line_and_text(lender: TestClient) -> None:
    startup = lender.get("/api/lender/queue", params={"line": "startup_credit"}).json()
    assert startup["total"] > 0 and {item["line"] for item in startup["files"]} == {"startup_credit"}
    one = lender.get("/api/lender/queue", params={"q": CLEAN_CREDIT.lower()}).json()
    assert [item["id"] for item in one["files"]] == [CLEAN_CREDIT]
    name = one["files"][0]["applicant"]
    assert CLEAN_CREDIT in [
        item["id"] for item in lender.get("/api/lender/queue", params={"q": name.upper()}).json()["files"]
    ]
    assert lender.get("/api/lender/queue", params={"line": "mortgage"}).status_code == 422


# --- the file view ---


def test_file_view_shows_the_run_up_to_the_checkpoint(lender: TestClient) -> None:
    response = lender.get(f"/api/lender/files/{CLEAN_CREDIT}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "SYNTHETIC DEMO" and body["notice"].startswith("The copilot never decides")
    assert body["recommendation_text"].endswith("you decide.") and body["your_reviewer_role"] == "underwriter"
    assert body["memo_writer"] == "template" and body["stamp"] == "FAKE LLM — test mode"
    assert body["memo"]["reasons"] and body["memo_check"]["passed"] is True
    assert body["guideline_search"]["status"] == "unavailable" and body["risk_model"]["status"] == "unavailable"
    screening = body["screening"]
    assert screening["lists_used"][0]["snapshot_date"] == "2026-10-12"
    assert {row["status"] for row in screening["results"]} == {"clear"}
    assert any(calc["calc_id"].startswith("CALC-") for calc in body["calculations"])
    assert body["cited_sections"] and all(not key.startswith(("NBC-", "HM-", "NSP-")) for key in body["evidence"])
    checks = {check["action"]: check for check in body["action_checks"]}
    assert checks["refer"]["allowed"] is True
    assert checks["decline"]["allowed"] is False
    assert any("senior_underwriter may decline" in reason for reason in checks["decline"]["reasons"])
    assert body["decision"] is None
    assert "ground_truth" not in response.text


def test_the_run_is_cached_per_file(lender: TestClient) -> None:
    service = lender.app.state.services.lender
    calls: list[str] = []
    original = service.run_file

    def counting(file_id: str) -> Any:
        calls.append(file_id)
        return original(file_id)

    service.run_file = counting
    lender.get(f"/api/lender/files/{STARTUP}")
    lender.get(f"/api/lender/files/{STARTUP}")
    assert calls == [STARTUP]


@pytest.mark.parametrize(
    ("role", "reviewer_role"),
    [
        ("lender_senior", "senior_underwriter"),
        ("lender_reviewer", "underwriter"),
        ("founder", "underwriter"),
        ("admin", "underwriter"),
    ],
)
def test_web_roles_map_to_reviewer_roles(lender: TestClient, role: str, reviewer_role: str) -> None:
    dev_login(lender, f"user_{role}", role)
    assert lender.get(f"/api/lender/files/{STARTUP}").json()["your_reviewer_role"] == reviewer_role


def test_injected_file_shows_the_hit_and_blocks_approval(lender: TestClient) -> None:
    dev_login(lender, "senior_a", "lender_senior")
    body = lender.get(f"/api/lender/files/{INJECTED}").json()
    assert body["injection_hits"] and body["injection_hits"][0]["field"] == "notes" and body["blocking_open"]
    checks = {check["action"]: check for check in body["action_checks"]}
    assert checks["approve"]["allowed"] is False and checks["refer"]["allowed"] is True


def test_unknown_and_malformed_file_ids(lender: TestClient) -> None:
    assert lender.get("/api/lender/files/APP-C-999").status_code == 404
    assert lender.get("/api/lender/files/../../etc").status_code in (404, 422)
    assert lender.get("/api/lender/files/not-an-id").status_code == 422


def test_a_slow_run_is_503(lender: TestClient) -> None:
    service = lender.app.state.services.lender
    lender.app.state.services.compute = ComputeRunner(ServiceSettings(timeout_s=0.05))
    original = service.run_file

    def slow(file_id: str) -> Any:
        time.sleep(0.3)
        return original(file_id)

    service.run_file = slow
    response = lender.get(f"/api/lender/files/{STARTUP}")
    assert response.status_code == 503 and "keeps running" in response.json()["detail"]


# --- guideline sections ---


def test_sections_serve_cited_text(lender: TestClient) -> None:
    section_id = lender.get(f"/api/lender/files/{CLEAN_CREDIT}").json()["cited_sections"][0]
    body = lender.get(f"/api/lender/sections/{section_id}").json()
    assert body["section_id"] == section_id and body["text"] and body["data_label"] == "SYNTHETIC"
    assert "SYNTHETIC" in body["document"]
    assert lender.get("/api/lender/sections/NBC-99.9").status_code == 404
    assert lender.get("/api/lender/sections/not a section").status_code == 422


# --- decisions ---


def test_a_refused_decision_is_422_with_the_checkpoint_reasons(lender: TestClient) -> None:
    response = decide(lender, CLEAN_CREDIT, "decline")
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "The checkpoint refused this decision."
    assert any("only a senior_underwriter may decline" in reason for reason in body["reasons"])
    assert lender.get(f"/api/lender/files/{CLEAN_CREDIT}/record").status_code == 404


def test_the_decision_goes_through_the_graphs_checkpoint(lender: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_everything(raw: Any, policy: Any, application: Any) -> tuple[None, list[str]]:
        return None, ["refused by the test's stand-in for validate_decision"]

    monkeypatch.setattr("uwcopilot.agent.graph.validate_decision", refuse_everything)
    response = decide(lender, CLEAN_CREDIT, "refer")
    assert response.status_code == 422
    assert response.json()["reasons"] == ["refused by the test's stand-in for validate_decision"]


def test_blocking_referral_refuses_a_senior_approval(lender: TestClient) -> None:
    dev_login(lender, "senior_a", "lender_senior")
    response = decide(lender, INJECTED, "approve", override_reason="documented_compensating_factor")
    assert response.status_code == 422
    assert any("referral" in reason for reason in response.json()["reasons"])


def test_unknown_override_reason_and_private_numbers_are_refused(lender: TestClient) -> None:
    bad_reason = decide(lender, CLEAN_CREDIT, "refer", override_reason="because")
    assert bad_reason.status_code == 422
    leaked = lender.post(
        f"/api/lender/files/{CLEAN_CREDIT}/decision",
        json={"action": "refer", "rationale": "Applicant SSN 123-45-6789 checked."},
        headers=csrf_headers(lender),
    )
    assert leaked.status_code == 422 and "privacy" in leaked.json()["detail"]
    too_short = lender.post(
        f"/api/lender/files/{CLEAN_CREDIT}/decision",
        json={"action": "refer", "rationale": "ok"},
        headers=csrf_headers(lender),
    )
    assert too_short.status_code == 422


def test_an_accepted_decision_is_recorded_per_user(lender: TestClient) -> None:
    response = decide(lender, CLEAN_CREDIT, "refer")
    assert response.status_code == 200, response.text
    body = response.json()
    decision = body["decision"]
    assert body["label"] == "DEMO" and decision["final_status"] == "referred_by_human"
    assert decision["reviewer_role"] == "underwriter" and decision["is_override"] is False
    assert "DEMO decision record" in body["markdown"] and "SYNTHETIC" in body["markdown"]

    record = lender.get(f"/api/lender/files/{CLEAN_CREDIT}/record").json()
    assert record["decision"] == decision and record_hash(record["record"]) == decision["record_sha256"]
    assert record["record"]["reviewer"]["reviewer_id"] == "reviewer_a"
    assert lender.get(f"/api/lender/files/{CLEAN_CREDIT}").json()["decision"] == decision
    assert decide(lender, CLEAN_CREDIT, "refer").status_code == 409

    dev_login(lender, "reviewer_b", "lender_reviewer")
    assert lender.get(f"/api/lender/files/{CLEAN_CREDIT}/record").status_code == 404
    assert lender.get(f"/api/lender/files/{CLEAN_CREDIT}").json()["decision"] is None
    assert lender.delete(f"/api/lender/files/{CLEAN_CREDIT}/record", headers=csrf_headers(lender)).status_code == 404

    dev_login(lender, "reviewer_a", "lender_reviewer")
    deleted = lender.delete(f"/api/lender/files/{CLEAN_CREDIT}/record", headers=csrf_headers(lender))
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert decide(lender, CLEAN_CREDIT, "request_more_information").status_code == 200


def test_the_record_keeps_the_rationale_lines_inside_its_fence(lender: TestClient) -> None:
    rationale = "First line of the reason.\nSecond line with ``` backticks that try to close the fence."
    response = decide(lender, CLEAN_CREDIT, "refer", rationale=rationale)
    assert response.status_code == 200, response.text
    markdown = response.json()["markdown"]
    block = markdown.split("```text\n", 1)[1].split("\n```", 1)[0]
    assert block == "First line of the reason.\nSecond line with ''' backticks that try to close the fence."


def test_a_senior_may_decline_with_an_override_reason(lender: TestClient) -> None:
    dev_login(lender, "senior_a", "lender_senior")
    response = decide(lender, CLEAN_CREDIT, "decline", override_reason="information_not_available_to_model")
    assert response.status_code == 200, response.text
    decision = response.json()["decision"]
    assert decision["final_status"] == "declined_by_human" and decision["is_override"] is True
    assert decision["reviewer_role"] == "senior_underwriter"


# --- the tool wrappers ---


def test_cached_tool_remembers_outputs_but_not_failures() -> None:
    calls: list[str] = []

    def handler(request: ScreeningRequest) -> str:
        calls.append(request.name)
        if request.name == "Broken Name":
            raise ToolUnavailable("down")
        return f"screened {request.name}"

    tool = CachedTool(handler)
    first = ScreeningRequest(name="Test Applicant LLC", entity_type="organization")
    assert tool(first) == tool(ScreeningRequest(name="Test Applicant LLC", entity_type="organization"))
    broken = ScreeningRequest(name="Broken Name", entity_type="person")
    for _ in range(2):
        with pytest.raises(ToolUnavailable):
            tool(broken)
    assert calls == ["Test Applicant LLC", "Broken Name", "Broken Name"]


def test_a_missing_guideline_index_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from uwcopilot.rag.index import IndexNotBuiltError

    adapter = GuidelineSearchAdapter()

    def not_built(query: Any) -> Any:
        raise IndexNotBuiltError("collection not found (test)")

    monkeypatch.setattr(adapter, "tool", not_built)
    with pytest.raises(ToolUnavailable, match="not built"):
        adapter({"query": "DSCR minimum"})


# --- access rules ---


def test_signed_out_users_get_401(client: TestClient) -> None:
    assert client.get("/api/lender/queue").status_code == 401
    assert client.get(f"/api/lender/files/{CLEAN_CREDIT}").status_code == 401
    assert client.get("/api/lender/sections/NBC-4.2").status_code == 401
    assert client.get(f"/api/lender/files/{CLEAN_CREDIT}/record").status_code == 401
    body = {"action": "refer", "rationale": "Reviewed the SYNTHETIC demo file."}
    response = client.post(f"/api/lender/files/{CLEAN_CREDIT}/decision", json=body, headers=csrf_headers(client))
    assert response.status_code == 401


def test_state_changing_requests_need_the_csrf_token(lender: TestClient) -> None:
    body = {"action": "refer", "rationale": "Reviewed the SYNTHETIC demo file."}
    assert lender.post(f"/api/lender/files/{CLEAN_CREDIT}/decision", json=body).status_code == 403
    assert lender.delete(f"/api/lender/files/{CLEAN_CREDIT}/record").status_code == 403


def test_lender_responses_are_not_cached(lender: TestClient) -> None:
    assert lender.get("/api/lender/queue").headers["cache-control"] == "no-store"
