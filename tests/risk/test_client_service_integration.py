"""The shared risk client (uwcopilot.risk.client) against the real scoring app (uwcopilot.risk.service).

The agent, the MCP server and the tool registry all score through RiskApiClient. Before commit 548afa3 it posted
the tool model RiskScoreRequest as-is (with a "scenario" label the service forbids, HTTP 422) and expected the tool
model RiskScoreResponse back, which the service never sends. Unit tests mocked the wrong contract on both sides, so
nothing caught it. This file wires the real client to the real FastAPI app through AppTransport (no socket, no
network) and the tiny SYNTHETIC models from tests/risk/conftest.py, so the two contracts cannot drift apart again.

It also pins the domain gap (docs/risk_service.md): the agent's commercial-loan and commercial-property features are
not the features the models were trained on (consumer Lending Club loans, French motor policies), so the service
refuses them and the agent records no score.
"""

import typing
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ValidationError
from risk_service_helpers import AppTransport, applicant_features, policy_features

from uwcopilot.agent.applications import AnyApplication, CreditApplication, InsuranceApplication, load_application
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.fake_memo import template_memo_responder
from uwcopilot.agent.graph import AgentDeps
from uwcopilot.agent.runner import compile_agent, start_run
from uwcopilot.agent.tool_plan import ToolRequest, plan_tool_calls
from uwcopilot.agent.tool_runner import execute_call, vet_requests
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.risk import credit, insurance
from uwcopilot.risk import service as risk_service
from uwcopilot.risk.client import (
    RiskApiClient,
    RiskApiError,
    RiskApiUnavailable,
    ServiceAssessment,
    ServiceReason,
    ServiceScenario,
    ServiceScore,
    service_body,
)
from uwcopilot.risk.data.fremtpl2 import RATING_FEATURES
from uwcopilot.tools.registry import ToolUnavailable
from uwcopilot.tools.schemas import RiskProduct, RiskScoreRequest, RiskScoreResponse

BASE_URL = "http://testserver"
APPLICATIONS_DIR = REPO_ROOT / "eval" / "applications"


@pytest.fixture
def api(client: TestClient) -> RiskApiClient:
    """The real client, sending real HTTP requests to the app with every product loaded."""
    return RiskApiClient(BASE_URL, transport=AppTransport(client))


@pytest.fixture
def api_without_credit(client_without_credit: TestClient) -> RiskApiClient:
    """The real client against the app as a fresh checkout runs it (no credit model artifact)."""
    return RiskApiClient(BASE_URL, transport=AppTransport(client_without_credit))


def direct_score(app_client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    """POST the service's own request format straight to the app and return the 200 body."""
    response = app_client.post("/score", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def committed_applications() -> list[AnyApplication]:
    """The committed SYNTHETIC credit and insurance applications the agent is evaluated on."""
    return [load_application(path) for path in sorted(APPLICATIONS_DIR.glob("APP-*.json"))]


def planned_risk_request(application: AnyApplication) -> ToolRequest:
    """The score_risk call the agent's deterministic planner makes for this application."""
    requests = [request for request in plan_tool_calls(application) if request.tool == "score_risk"]
    assert len(requests) == 1
    return requests[0]


def application_by_id(application_id: str) -> AnyApplication:
    """One committed SYNTHETIC application."""
    return load_application(APPLICATIONS_DIR / f"{application_id}.json")


# ---------------------------------------------------------------------------
# The mismatch, reproduced: why the client cannot send or accept the tool models as they are
# ---------------------------------------------------------------------------


def test_the_tool_request_sent_as_is_is_refused_by_the_service(
    client: TestClient, insurance_data: dict[str, pd.DataFrame]
) -> None:
    """What the old client sent: RiskScoreRequest.model_dump(), which always carries scenario='baseline'."""
    request = RiskScoreRequest(product="insurance_frequency", features=policy_features(insurance_data))
    response = client.post("/score", json=request.model_dump())
    assert response.status_code == 422
    assert [(error["type"], error["loc"]) for error in response.json()["detail"]] == [
        ("extra_forbidden", ["body", "scenario"])
    ]


def test_the_service_answer_is_not_a_tool_response(client: TestClient, insurance_data: dict[str, pd.DataFrame]) -> None:
    """What the old client expected: a RiskScoreResponse. The real 200 body never is one, so it must be translated."""
    body = direct_score(client, {"product": "insurance_frequency", "features": policy_features(insurance_data)})
    assert body["reason_codes"], "this SYNTHETIC policy has reason codes, so their mismatch shows too"
    with pytest.raises(ValidationError) as raised:
        RiskScoreResponse.model_validate(body)
    # Six errors at the top level and three per reason code, so the count depends on the policy; the kinds do not.
    assert raised.value.error_count() == 6 + 3 * len(body["reason_codes"])
    assert {(error["type"], error["loc"][0]) for error in raised.value.errors()} == {
        ("extra_forbidden", "prediction_name"),
        ("extra_forbidden", "prediction_unit"),
        ("extra_forbidden", "model"),
        ("extra_forbidden", "risk_band_definition"),
        ("extra_forbidden", "note"),
        ("extra_forbidden", "reason_codes"),  # each code's "value" and "reason"
        ("missing", "reason_codes"),  # each code's "description"
        ("string_type", "scenario"),  # the service's scenario block (null here), not the tool's label
    }
    ServiceScore.model_validate(body)  # the client's copy of the service response accepts it as is


# ---------------------------------------------------------------------------
# Contract drift guards (no HTTP): the client's copy of the service contract must match the service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_model", "service_model"),
    [
        (ServiceScore, risk_service.ScoreResponse),
        (ServiceAssessment, risk_service.Assessment),
        (ServiceScenario, risk_service.ScenarioResult),
        (ServiceReason, risk_service.ReasonCode),
    ],
)
def test_the_clients_response_models_have_the_services_fields(
    client_model: type[BaseModel], service_model: type[BaseModel]
) -> None:
    """The client refuses unknown fields, so a field added to the service must be added to the client too."""
    assert set(client_model.model_fields) == set(service_model.model_fields)


def test_the_request_contracts_differ_only_by_the_scenario_label() -> None:
    assert set(RiskScoreRequest.model_fields) - {"scenario"} == set(risk_service.ScoreRequest.model_fields)
    assert typing.get_args(RiskProduct) == typing.get_args(risk_service.Product)


def test_the_body_on_the_wire_is_a_valid_service_request() -> None:
    with_overrides = RiskScoreRequest(
        product="insurance_frequency", features={"BonusMalus": 50}, overrides={"BonusMalus": 80}, scenario="stress"
    )
    assert set(service_body(with_overrides)) == {"product", "features", "overrides"}
    risk_service.ScoreRequest.model_validate(service_body(with_overrides))
    baseline = RiskScoreRequest(product="credit", features={"dti": 10.0})
    assert set(service_body(baseline)) == {"product", "features"}
    risk_service.ScoreRequest.model_validate(service_body(baseline))


# ---------------------------------------------------------------------------
# Round trips: the real client against the real app
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("product", "position"), [("insurance_frequency", 0), ("insurance_severity", 3)])
def test_baseline_round_trip(
    api: RiskApiClient,
    client: TestClient,
    insurance_data: dict[str, pd.DataFrame],
    product: str,
    position: int,
) -> None:
    policy = policy_features(insurance_data, position)
    scored = api.score(RiskScoreRequest(product=product, features=policy))
    direct = direct_score(client, {"product": product, "features": policy})
    assert scored.product == product
    assert scored.prediction == pytest.approx(direct["prediction"])
    assert scored.risk_band == direct["risk_band"]
    assert scored.model_version == direct["model_version"]
    assert scored.scenario == "baseline"
    assert scored.unknown_features == []
    assert [(reason.feature, reason.contribution, reason.description) for reason in scored.reason_codes] == [
        (reason["feature"], pytest.approx(reason["contribution"]), reason["reason"])
        for reason in direct["reason_codes"]
    ]


def test_scenario_round_trip_returns_the_scenario_with_its_label(
    api: RiskApiClient,
    client: TestClient,
    insurance_data: dict[str, pd.DataFrame],
    insurance_bundle: insurance.InsuranceModels,
) -> None:
    policy = policy_features(insurance_data)
    worst = insurance_bundle.feature_ranges["BonusMalus"].max_value
    overrides = {"BonusMalus": worst}
    scored = api.score(
        RiskScoreRequest(
            product="insurance_frequency", features=policy, overrides=overrides, scenario="bonus_malus_max"
        )
    )
    direct = direct_score(client, {"product": "insurance_frequency", "features": policy, "overrides": overrides})
    expected = float(insurance_bundle.frequency_lgbm.predict_rate(pd.DataFrame([{**policy, **overrides}]))[0])
    assert scored.prediction == pytest.approx(expected)
    assert scored.prediction == pytest.approx(direct["scenario"]["scenario"]["prediction"])
    assert scored.risk_band == direct["scenario"]["scenario"]["risk_band"]
    assert scored.model_version == direct["model_version"]
    assert scored.scenario == "bonus_malus_max"
    assert [reason.description for reason in scored.reason_codes] == [
        reason["reason"] for reason in direct["scenario"]["scenario"]["reason_codes"]
    ]


def test_empty_overrides_are_a_baseline(api: RiskApiClient, insurance_data: dict[str, pd.DataFrame]) -> None:
    policy = policy_features(insurance_data)
    baseline = api.score(RiskScoreRequest(product="insurance_frequency", features=policy))
    empty = api.score(RiskScoreRequest(product="insurance_frequency", features=policy, overrides={}))
    assert empty.prediction == pytest.approx(baseline.prediction)
    assert empty.risk_band == baseline.risk_band


def test_credit_round_trip_lists_missing_values_as_unknown(
    api: RiskApiClient, client: TestClient, credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    applicant = applicant_features(credit_loans)
    assert credit_models.feature_ranges["mths_since_last_delinq"].missing_allowed
    applicant["mths_since_last_delinq"] = None  # missing in training too, so the service accepts null
    scored = api.score(RiskScoreRequest(product="credit", features=applicant))
    direct = direct_score(client, {"product": "credit", "features": applicant})
    assert scored.prediction == pytest.approx(direct["prediction"])
    assert scored.risk_band == direct["risk_band"]
    assert scored.model_version == direct["model_version"]
    assert "mths_since_last_delinq" in scored.unknown_features
    assert scored.unknown_features == sorted(name for name, value in applicant.items() if value is None)


def test_credit_scenario_round_trip(
    api: RiskApiClient, client: TestClient, credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    applicant = applicant_features(credit_loans)
    overrides = {"dti": credit_models.feature_ranges["dti"].max_value}
    scored = api.score(RiskScoreRequest(product="credit", features=applicant, overrides=overrides, scenario="dti_max"))
    direct = direct_score(client, {"product": "credit", "features": applicant, "overrides": overrides})
    assert scored.prediction == pytest.approx(direct["scenario"]["scenario"]["prediction"])
    assert scored.scenario == "dti_max"


# ---------------------------------------------------------------------------
# The service's refusals become structured client errors, never a score
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "service_error"),
    [
        ("missing_feature", "invalid_features"),
        ("override_out_of_range", "invalid_override"),
        ("leakage", "leakage_guard"),
    ],
)
def test_refusals_are_rejected_request_errors(
    api: RiskApiClient,
    insurance_data: dict[str, pd.DataFrame],
    credit_loans: pd.DataFrame,
    case: str,
    service_error: str,
) -> None:
    policy = policy_features(insurance_data)
    requests = {
        "missing_feature": RiskScoreRequest(
            product="insurance_frequency", features={k: v for k, v in policy.items() if k != "Region"}
        ),
        "override_out_of_range": RiskScoreRequest(
            product="insurance_frequency", features=policy, overrides={"BonusMalus": 10_000}
        ),
        "leakage": RiskScoreRequest(
            product="credit", features={**applicant_features(credit_loans), "total_pymnt": 1234.0}
        ),
    }
    with pytest.raises(RiskApiError) as raised:
        api.score(requests[case])
    info = raised.value.info
    assert not isinstance(raised.value, RiskApiUnavailable)  # the service answered; the request was wrong
    assert info.code == "risk_api_rejected_request"
    assert info.http_status == 422
    assert info.endpoint == f"{BASE_URL}/score"
    assert service_error in info.message


def test_an_answer_for_another_product_is_refused(client: TestClient, insurance_data: dict[str, pd.DataFrame]) -> None:
    """A real 200 answer for insurance_severity must not come back labelled as the insurance_frequency asked for
    (tool_response copies the product from the request, so only the client's product check stops it)."""
    policy = policy_features(insurance_data)
    severity_answer = direct_score(client, {"product": "insurance_severity", "features": policy})
    replay = RiskApiClient(BASE_URL, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=severity_answer)))
    with pytest.raises(RiskApiError) as raised:
        replay.score(RiskScoreRequest(product="insurance_frequency", features=policy))
    assert raised.value.info.code == "risk_api_invalid_response"
    assert replay.score(RiskScoreRequest(product="insurance_severity", features=policy)).product == "insurance_severity"


def test_a_product_without_a_model_is_a_server_error(
    api_without_credit: RiskApiClient, credit_loans: pd.DataFrame
) -> None:
    with pytest.raises(RiskApiError) as raised:
        api_without_credit.score(RiskScoreRequest(product="credit", features=applicant_features(credit_loans)))
    assert raised.value.info.code == "risk_api_server_error"
    assert raised.value.info.http_status == 503
    assert "model_unavailable" in raised.value.info.message


# ---------------------------------------------------------------------------
# The domain gap: the agent's commercial features are not the models' features
# ---------------------------------------------------------------------------


def test_the_agents_features_are_not_the_models_features() -> None:
    """Every committed SYNTHETIC application: the planned features share at most one name with the model's."""
    applications = committed_applications()
    lines = {type(application) for application in applications}
    assert lines == {CreditApplication, InsuranceApplication}
    for application in applications:
        arguments = planned_risk_request(application).arguments
        sent = set(arguments["features"])
        if isinstance(application, CreditApplication):
            assert arguments["product"] == "credit"
            # term_months is the only shared name, and even its population differs (Lending Club: 36 or 60).
            assert sent & set(credit.CREDIT_FEATURES) == {"term_months"}
        else:
            assert arguments["product"] == "insurance_frequency"
            assert sent & set(RATING_FEATURES) == set()


@pytest.mark.parametrize(
    ("application_id", "unknown_example", "missing_example"),
    [("APP-C-001", "stated_annual_revenue", "annual_inc"), ("APP-I-001", "total_insured_value", "BonusMalus")],
)
def test_the_service_refuses_the_agents_planned_request(
    api: RiskApiClient, client: TestClient, application_id: str, unknown_example: str, missing_example: str
) -> None:
    """With the service running and its models loaded, the agent's request is refused: it never gets a score."""
    arguments = planned_risk_request(application_by_id(application_id)).arguments
    with pytest.raises(RiskApiError) as raised:
        api.score(RiskScoreRequest.model_validate(arguments))
    info = raised.value.info
    assert info.code == "risk_api_rejected_request" and info.http_status == 422
    assert "invalid_features" in info.message and "unknown features" in info.message
    # The client quotes at most MAX_DETAIL_CHARS of the detail; the service's full answer names both sides of the gap.
    detail = client.post("/score", json=arguments).json()["detail"]
    assert detail["error"] == "invalid_features"
    assert unknown_example in detail["message"] and missing_example in detail["message"]


def test_without_a_credit_model_the_agents_credit_request_gets_no_score(api_without_credit: RiskApiClient) -> None:
    arguments = planned_risk_request(application_by_id("APP-C-001")).arguments
    with pytest.raises(RiskApiError) as raised:
        api_without_credit.score(RiskScoreRequest.model_validate(arguments))
    assert raised.value.info.http_status == 503


def test_the_agent_records_the_refusal_without_a_score(api: RiskApiClient) -> None:
    """Through the agent's own tool gate and runner: the call is made, refused, and recorded as an error."""
    request = planned_risk_request(application_by_id("APP-C-001"))
    approved, rejections = vet_requests([request], executed_signatures=set(), executed_count=0)
    assert rejections == [] and len(approved) == 1
    result = execute_call(approved[0], {"score_risk": api.score})
    assert result.status == "error"
    assert result.output is None
    assert "HTTP 422" in (result.error or "") and "invalid_features" in (result.error or "")


def not_used_here(request: Any) -> Any:
    """Stand-in for search_guidelines and screen_entity: this test needs no Qdrant index and no sanctions lists."""
    raise ToolUnavailable("not used in this test")


@pytest.mark.parametrize("application_id", ["APP-C-001", "APP-I-001"])
def test_a_full_agent_run_reaches_a_human_without_a_model_score(
    client: TestClient, tmp_path: Path, application_id: str
) -> None:
    """The real graph with score_risk wired to the real app: the service refuses, no score reaches the policy, and
    the run still stops at the human checkpoint with nothing decided (ground rule 8)."""
    transport = AppTransport(client)
    handlers = {
        "search_guidelines": not_used_here,
        "screen_entity": not_used_here,
        "score_risk": RiskApiClient(BASE_URL, transport=transport).score,
    }
    log = DecisionLog(tmp_path / "decision_records.jsonl")
    deps = AgentDeps(llm=FakeLLM(template_memo_responder), tool_handlers=handlers, decision_log=log)
    application = application_by_id(application_id)
    outcome = start_run(compile_agent(deps, InMemorySaver()), application.model_dump(mode="json"), "risk-gap")

    assert outcome.status == "awaiting_review"
    assert log.records() == []
    assert transport.sent == 1  # the service was really asked, once
    [risk] = [result for result in outcome.state["tool_results"] if result["tool"] == "score_risk"]
    assert risk["status"] == "error" and risk["output"] is None
    assert "HTTP 422" in risk["error"] and "invalid_features" in risk["error"]
    if isinstance(application, CreditApplication):
        not_evaluated = outcome.state["policy_result"]["not_evaluated"]
        [skipped] = [item for item in not_evaluated if item["rule"] == "risk_model_pd"]
        assert skipped["section_ids"] == ["NBC-8.1"]
        assert skipped["reason"].startswith("Model PD not available: the score_risk tool was error")
