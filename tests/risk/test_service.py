"""Tests for the scoring API (uwcopilot.risk.service).

Every model here is a tiny one trained on SYNTHETIC data and saved to a temporary folder (the session fixtures in
tests/risk/conftest.py), so the tests need neither data/raw nor models/. The synthetic models' predictions mean
nothing; the tests only check that the API serves them faithfully and enforces its rules. The shared HTTP client
against this app is tested in test_client_service_integration.py.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from risk_service_helpers import SYNTHETIC_CREDIT_VERSION, applicant_features, policy_features

from uwcopilot.risk import credit, insurance
from uwcopilot.risk.bands import assign_band
from uwcopilot.risk.data.fremtpl2 import RATING_FEATURES
from uwcopilot.risk.service import ModelRegistry, create_app, insurance_model_version

# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_lists_loaded_models_and_band_definitions(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["models_loaded"] == ["credit", "insurance_frequency", "insurance_severity"]
    frequency = body["products"]["insurance_frequency"]
    assert frequency["model"] == "lgbm"
    assert frequency["model_version"] == "insurance_frequency_lgbm v7"
    assert frequency["features"] == RATING_FEATURES
    assert len(frequency["risk_band_definition"]["cutoffs"]) == len(frequency["risk_band_definition"]["labels"]) - 1


def test_health_without_credit_model_says_why(client_without_credit: TestClient) -> None:
    body = client_without_credit.get("/health").json()
    assert body["status"] == "degraded"
    assert body["models_loaded"] == ["insurance_frequency", "insurance_severity"]
    credit_status = body["products"]["credit"]
    assert credit_status["loaded"] is False
    assert "Kaggle" in credit_status["detail"] and "make train-credit" in credit_status["detail"]


# ---------------------------------------------------------------------------
# POST /score: insurance
# ---------------------------------------------------------------------------


def test_frequency_score_matches_the_model(
    client: TestClient, insurance_data: dict[str, pd.DataFrame], insurance_bundle: insurance.InsuranceModels
) -> None:
    policy = policy_features(insurance_data)
    response = client.post("/score", json={"product": "insurance_frequency", "features": policy})
    assert response.status_code == 200, response.text
    body = response.json()
    expected = float(insurance_bundle.frequency_lgbm.predict_rate(pd.DataFrame([policy]))[0])
    assert body["prediction"] == pytest.approx(expected)
    assert body["prediction_name"] == "expected_claim_frequency"
    assert body["prediction_unit"] == "claims per policy-year"
    assert body["model"] == "lgbm"
    assert body["model_version"] == "insurance_frequency_lgbm v7"
    bands = insurance_bundle.metadata["risk_bands"]["insurance_frequency"]
    assert body["risk_band"] == assign_band(expected, bands)
    assert body["risk_band_definition"]["cutoffs"] == pytest.approx(bands["cutoffs"])
    assert body["scenario"] is None
    reasons = body["reason_codes"]
    assert len(reasons) <= 4
    assert all(reason["contribution"] > 0 for reason in reasons)
    assert [reason["contribution"] for reason in reasons] == sorted((r["contribution"] for r in reasons), reverse=True)
    for reason in reasons:
        assert reason["value"] == policy[reason["feature"]]  # the original value, not an encoded one
        assert reason["reason"] == insurance.FREQUENCY_REASONS_INCREASE[reason["feature"]]


def test_severity_is_served_by_the_preferred_glm(
    client: TestClient, insurance_data: dict[str, pd.DataFrame], insurance_bundle: insurance.InsuranceModels
) -> None:
    policy = policy_features(insurance_data, position=3)
    body = client.post("/score", json={"product": "insurance_severity", "features": policy}).json()
    expected = float(insurance_bundle.severity_glm.predict_rate(pd.DataFrame([policy]))[0])
    assert body["prediction"] == pytest.approx(expected)
    assert body["model"] == "glm"
    assert body["model_version"] == "insurance_severity_glm (MLflow run run-severity, commit 0123456789ab)"
    values, base = insurance_bundle.severity_glm.contributions(pd.DataFrame([policy]))
    top = sorted((v for v in values[0] if v > 0), reverse=True)[:4]
    assert [reason["contribution"] for reason in body["reason_codes"]] == pytest.approx(top)
    assert all(
        reason["reason"] == insurance.SEVERITY_REASONS_INCREASE[reason["feature"]] for reason in body["reason_codes"]
    )


def test_override_of_the_same_value_changes_nothing(
    client: TestClient, insurance_data: dict[str, pd.DataFrame]
) -> None:
    policy = policy_features(insurance_data)
    body = client.post(
        "/score",
        json={"product": "insurance_frequency", "features": policy, "overrides": {"BonusMalus": policy["BonusMalus"]}},
    ).json()
    scenario = body["scenario"]
    assert scenario["scenario"]["prediction"] == pytest.approx(scenario["baseline"]["prediction"])
    assert scenario["prediction_change"] == pytest.approx(0.0)
    assert scenario["risk_band_changed"] is False


def test_scenario_returns_baseline_and_scenario_side_by_side(
    client: TestClient, insurance_data: dict[str, pd.DataFrame], insurance_bundle: insurance.InsuranceModels
) -> None:
    policy = policy_features(insurance_data)
    worst = insurance_bundle.feature_ranges["BonusMalus"].max_value
    overrides = {"BonusMalus": worst, "Area": "F"}
    body = client.post(
        "/score", json={"product": "insurance_frequency", "features": policy, "overrides": overrides}
    ).json()
    scenario = body["scenario"]
    assert scenario["overrides"] == overrides
    assert scenario["baseline"]["prediction"] == pytest.approx(body["prediction"])  # top level = baseline
    expected = float(insurance_bundle.frequency_lgbm.predict_rate(pd.DataFrame([{**policy, **overrides}]))[0])
    assert scenario["scenario"]["prediction"] == pytest.approx(expected)
    change = scenario["scenario"]["prediction"] - scenario["baseline"]["prediction"]
    assert scenario["prediction_change"] == pytest.approx(change)
    assert scenario["relative_change"] == pytest.approx(change / scenario["baseline"]["prediction"])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"BonusMalus": 10_000}, "outside the training range"),
        ({"Area": "Z"}, "not an allowed value"),
        ({"NotAFeature": 1}, "unknown feature"),
        ({"DrivAge": "forty"}, "is numeric"),
    ],
)
def test_invalid_overrides_are_rejected_not_clipped(
    client: TestClient, insurance_data: dict[str, pd.DataFrame], overrides: dict[str, Any], message: str
) -> None:
    response = client.post(
        "/score",
        json={"product": "insurance_frequency", "features": policy_features(insurance_data), "overrides": overrides},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_override"
    assert message in detail["message"] and "never clipped" in detail["message"]


def test_invalid_insurance_features_are_rejected(client: TestClient, insurance_data: dict[str, pd.DataFrame]) -> None:
    policy = policy_features(insurance_data)
    missing = {name: value for name, value in policy.items() if name != "Region"}
    cases = [
        ({**policy, "ClaimNb": 1}, "unknown features ['ClaimNb']"),  # an outcome column is never an input
        (missing, "missing features ['Region']"),
        ({**policy, "DrivAge": 150}, "outside the training range"),
    ]
    for features, message in cases:
        response = client.post("/score", json={"product": "insurance_severity", "features": features})
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_features"
        assert message in response.json()["detail"]["message"]


def test_fractional_values_of_whole_number_features_are_rejected(
    client: TestClient, insurance_data: dict[str, pd.DataFrame], insurance_bundle: insurance.InsuranceModels
) -> None:
    """VehPower and DrivAge are whole numbers in training, so 6.5 is outside the evidence even if in range."""
    policy = policy_features(insurance_data)
    ranges = insurance_bundle.feature_ranges
    assert ranges["VehPower"].integer_only and ranges["DrivAge"].integer_only
    fractional_power = ranges["VehPower"].min_value + 0.5
    as_input = client.post(
        "/score", json={"product": "insurance_frequency", "features": {**policy, "VehPower": fractional_power}}
    )
    assert as_input.status_code == 422
    assert as_input.json()["detail"]["error"] == "invalid_features"
    assert "not a whole number" in as_input.json()["detail"]["message"]
    fractional_age = ranges["DrivAge"].min_value + 0.5
    as_override = client.post(
        "/score",
        json={"product": "insurance_frequency", "features": policy, "overrides": {"DrivAge": fractional_age}},
    )
    assert as_override.status_code == 422
    assert as_override.json()["detail"]["error"] == "invalid_override"
    whole_float = client.post(
        "/score", json={"product": "insurance_frequency", "features": {**policy, "VehPower": float(policy["VehPower"])}}
    )
    assert whole_float.status_code == 200, whole_float.text


def test_unknown_product_and_extra_fields_are_rejected(client: TestClient) -> None:
    assert client.post("/score", json={"product": "mortgage", "features": {}}).status_code == 422
    assert client.post("/score", json={"product": "credit", "features": {}, "debug": True}).status_code == 422


# ---------------------------------------------------------------------------
# POST /score: credit
# ---------------------------------------------------------------------------


def test_credit_returns_503_when_no_model_artifact(
    client_without_credit: TestClient, credit_loans: pd.DataFrame
) -> None:
    response = client_without_credit.post(
        "/score", json={"product": "credit", "features": applicant_features(credit_loans)}
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "model_unavailable" and detail["product"] == "credit"
    assert "Kaggle" in detail["message"]


@pytest.mark.parametrize("which", ["features", "overrides"])
def test_leakage_guard_runs_even_without_a_credit_model(
    client_without_credit: TestClient, credit_loans: pd.DataFrame, which: str
) -> None:
    applicant = applicant_features(credit_loans)
    request = {"product": "credit", "features": applicant}
    if which == "features":
        request["features"] = {**applicant, "total_pymnt": 1234.0}
    else:
        request["overrides"] = {"recoveries": 0.0}
    response = client_without_credit.post("/score", json=request)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "leakage_guard"


def test_credit_score_matches_the_model(
    client: TestClient, credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    applicant = applicant_features(credit_loans)
    response = client.post("/score", json={"product": "credit", "features": applicant})
    assert response.status_code == 200, response.text
    body = response.json()
    expected = credit_models.score({k: (np.nan if v is None else v) for k, v in applicant.items()})["baseline"]
    assert body["prediction"] == pytest.approx(expected["pd"])
    assert body["risk_band"] == expected["risk_band"] == assign_band(expected["pd"], credit_models.risk_bands)
    assert body["model"] == credit_models.chosen
    assert body["model_version"] == SYNTHETIC_CREDIT_VERSION
    assert body["prediction_name"] == "probability_of_default"
    assert 0 < len(body["reason_codes"]) <= 4
    assert all(reason["reason"] == credit.REASON_MAP[reason["feature"]] for reason in body["reason_codes"])


@pytest.mark.parametrize(
    ("request_part", "error"),
    [
        ({"features_extra": {"recoveries": 10.0}}, "leakage_guard"),
        ({"overrides": {"last_fico_range_low": 700}}, "leakage_guard"),
        ({"overrides": {"dti": 10_000.0}}, "invalid_override"),
        ({"features_extra": {"favourite_colour": "blue"}}, "invalid_features"),
    ],
)
def test_credit_rejections(
    client: TestClient, credit_loans: pd.DataFrame, request_part: dict[str, Any], error: str
) -> None:
    applicant = applicant_features(credit_loans)
    body = {"product": "credit", "features": {**applicant, **request_part.get("features_extra", {})}}
    if "overrides" in request_part:
        body["overrides"] = request_part["overrides"]
    response = client.post("/score", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == error


def test_invalid_credit_features_are_rejected(
    client: TestClient, credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    """The base applicant gets the same checks as an override: nothing is filled in, nothing extrapolated."""
    applicant = applicant_features(credit_loans)
    dti_max = credit_models.feature_ranges["dti"].max_value
    never_missing = next(
        name for name in credit.CREDIT_FEATURES if not credit_models.feature_ranges[name].missing_allowed
    )
    without_dti = {name: value for name, value in applicant.items() if name != "dti"}
    cases = [
        ({}, "missing features"),
        (without_dti, "missing features ['dti']"),
        ({**applicant, "dti": 100 * dti_max}, "outside the training range"),
        ({**applicant, "purpose": "time_travel"}, "not an allowed value"),
        ({**applicant, never_missing: None}, f"'{never_missing}' cannot be missing"),
    ]
    for features, message in cases:
        response = client.post("/score", json={"product": "credit", "features": features})
        assert response.status_code == 422, (features, response.text)
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_features"
        assert message in detail["message"]


def test_credit_score_rejects_incomplete_features_directly(
    credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    """CreditModels.score itself refuses an empty applicant (not only the API)."""
    with pytest.raises(ValueError, match="missing features"):
        credit_models.score({})


def test_credit_scenario_and_missing_values(
    client: TestClient, credit_loans: pd.DataFrame, credit_models: credit.CreditModels
) -> None:
    applicant = applicant_features(credit_loans)
    assert credit_models.feature_ranges["mths_since_last_delinq"].missing_allowed
    applicant["mths_since_last_delinq"] = None  # missing in training too, so null is accepted and stays valid JSON
    dti_range = credit_models.feature_ranges["dti"]
    response = client.post(
        "/score", json={"product": "credit", "features": applicant, "overrides": {"dti": dti_range.max_value}}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario"]["overrides"] == {"dti": dti_range.max_value}
    assert body["scenario"]["baseline"]["prediction"] == pytest.approx(body["prediction"])
    json.dumps(body, allow_nan=False)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_registry_reports_broken_artifacts(tmp_path: Path, insurance_dir: Path) -> None:
    broken = tmp_path / "credit"
    broken.mkdir()
    (broken / credit.METADATA_FILE).write_text("{not json")
    registry = ModelRegistry.load(broken, tmp_path / "no_insurance")
    assert registry.scorers == {}
    assert "could not be loaded" in registry.unavailable["credit"]
    assert "make train-insurance" in registry.unavailable["insurance_frequency"]


def test_registry_refuses_metadata_without_risk_bands(tmp_path: Path, insurance_dir: Path) -> None:
    copy = tmp_path / "insurance"
    copy.mkdir()
    for path in insurance_dir.iterdir():
        (copy / path.name).write_bytes(path.read_bytes())
    metadata = json.loads((copy / insurance.METADATA_FILE).read_text())
    del metadata["risk_bands"]
    (copy / insurance.METADATA_FILE).write_text(json.dumps(metadata))
    registry = ModelRegistry.load(tmp_path / "no_credit", copy)
    assert "insurance_frequency" not in registry.scorers
    assert "make train-insurance" in registry.unavailable["insurance_frequency"]


def test_lazy_loading_without_startup(credit_dir: Path, insurance_dir: Path) -> None:
    """Without the startup event (no `with`), the first request loads the models."""
    test_client = TestClient(create_app(credit_dir=credit_dir, insurance_dir=insurance_dir))
    assert test_client.get("/health").json()["status"] == "ok"


def test_insurance_model_version_strings() -> None:
    metadata = {
        "model_version": {"frequency_lgbm": {"registered_model": "insurance_frequency_lgbm", "version": "3"}},
        "mlflow_runs": {"severity": "abc"},
        "git_commit": "fedcba9876543210",
    }
    assert insurance_model_version(metadata, "frequency", "lgbm") == "insurance_frequency_lgbm v3"
    assert insurance_model_version(metadata, "severity", "glm") == (
        "insurance_severity_glm (MLflow run abc, commit fedcba987654)"
    )
