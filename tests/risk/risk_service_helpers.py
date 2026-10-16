"""Tiny SYNTHETIC risk models and small helpers for tests that run the real scoring API (uwcopilot.risk.service).

Used by tests/risk/conftest.py (the shared fixtures), test_service.py and test_client_service_integration.py.

Every model here is trained in a few seconds on SYNTHETIC freMTPL2-shaped policies or SYNTHETIC Lending Club-shaped
loans. Their predictions mean nothing. The tests only check that the API and its HTTP client serve them faithfully
and enforce their rules, so they need neither data/raw nor models/.
"""

from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from uwcopilot.risk import credit, insurance
from uwcopilot.risk.data.fremtpl2 import (
    CATEGORICAL_FEATURES,
    RATING_FEATURES,
    prepare_fremtpl2,
    severity_cap,
    severity_table,
    split_policies,
    synthetic_fremtpl2_frames,
)
from uwcopilot.risk.data.lending_club import finished_loans, prepare_loans, synthetic_accepted_loans
from uwcopilot.risk.scenario import build_feature_ranges

FREQUENCY_PARAMS = {**insurance.FREQUENCY_LGBM_PARAMS, "min_data_in_leaf": 50, "num_threads": 1}
SEVERITY_PARAMS = {**insurance.SEVERITY_LGBM_PARAMS, "min_data_in_leaf": 20, "num_threads": 1}
SYNTHETIC_CREDIT_VERSION = "uwcopilot_credit_pd v1 (SYNTHETIC test)"


def synthetic_insurance_splits() -> dict[str, pd.DataFrame]:
    """SYNTHETIC freMTPL2-shaped policies, split 60/20/20, plus the matching severity tables."""
    data = prepare_fremtpl2(*synthetic_fremtpl2_frames(n_policies=5000, seed=7))
    splits = split_policies(data.policies, seed=42)
    train_claims = data.claims.loc[data.claims["IDpol"].isin(splits["train"]["IDpol"]), "ClaimAmount"]
    cap = severity_cap(train_claims)
    for name in ("train", "validation", "test"):
        splits[f"severity_{name}"] = severity_table(splits[name], data.claims, cap)
    return splits


def synthetic_insurance_bundle(splits: dict[str, pd.DataFrame]) -> insurance.InsuranceModels:
    """All four insurance models with the metadata the training script writes (risk bands included).

    The preferred models are the same as in the real run (frequency LightGBM, severity GLM), so both code paths of
    the service run in tests.
    """
    bundle = insurance.InsuranceModels(
        frequency_glm=insurance.fit_frequency_glm(splits["train"], min_level_count=20),
        frequency_lgbm=insurance.fit_frequency_lgbm(
            splits["train"],
            splits["validation"],
            params=FREQUENCY_PARAMS,
            max_rounds=200,
            early_stopping_rounds=20,
        ),
        severity_glm=insurance.fit_severity_glm(splits["severity_train"], min_level_count=10),
        severity_lgbm=insurance.fit_severity_lgbm(
            splits["severity_train"],
            splits["severity_validation"],
            params=SEVERITY_PARAMS,
            max_rounds=100,
            early_stopping_rounds=10,
        ),
        feature_ranges=build_feature_ranges(splits["train"][RATING_FEATURES], categorical_columns=CATEGORICAL_FEATURES),
        metadata={},
    )
    preferred = {"frequency": "lgbm", "severity": "glm"}
    bundle.metadata = {
        "model_version": {
            "frequency_lgbm": {"registered_model": "insurance_frequency_lgbm", "version": "7"},
            "severity_lgbm": {"registered_model": "insurance_severity_lgbm", "version": "7"},
            "label": "SYNTHETIC test bundle",
        },
        "git_commit": "0123456789abcdef",
        "prediction_units": {"frequency": "claims per policy-year", "severity": "capped claim amount per claim"},
        "preferred_model": preferred,
        "risk_bands": insurance.risk_bands_for(bundle, splits["train"], preferred),
        "mlflow_runs": {"frequency": "run-frequency", "severity": "run-severity"},
    }
    return bundle


def synthetic_credit_loans() -> pd.DataFrame:
    """SYNTHETIC Lending Club-shaped loans, prepared the way the training script prepares the real file."""
    return prepare_loans(synthetic_accepted_loans(n_rows=2500, seed=11))


def synthetic_credit_models(loans: pd.DataFrame) -> credit.CreditModels:
    """Both credit models trained on SYNTHETIC loans with small, fast settings."""
    params = {**credit.DEFAULT_LGBM_PARAMS, "num_threads": 1, "min_data_in_leaf": 20, "num_leaves": 7}
    config = credit.CreditConfig(
        n_resamples=20,
        shap_sample_size=100,
        min_category_count=5,
        lgbm_params=params,
        max_boost_rounds=200,
        early_stopping_rounds=20,
    )
    _, models = credit.run_credit_pipeline(loans, config, dataset_label="SYNTHETIC test data")
    models.metadata["model_version"] = SYNTHETIC_CREDIT_VERSION
    return models


def json_ready(value: Any) -> Any:
    """A numpy scalar as a plain Python value, and NaN as None, so the value can be sent as JSON."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def policy_features(splits: dict[str, pd.DataFrame], position: int = 0) -> dict[str, Any]:
    """One SYNTHETIC test policy's nine rating features as JSON-ready values."""
    row = splits["test"].iloc[position]
    return {name: json_ready(row[name]) for name in RATING_FEATURES}


def applicant_features(loans: pd.DataFrame) -> dict[str, Any]:
    """One SYNTHETIC finished training loan's features as JSON-ready values (NaN becomes None).

    A training loan is inside every training range by construction, so the API must accept it.
    """
    train = credit.out_of_time_split(finished_loans(loans))["train"]
    row = train.iloc[0][credit.CREDIT_FEATURES].to_dict()
    return {name: json_ready(value) for name, value in row.items()}


class AppTransport(httpx.BaseTransport):
    """An httpx transport that hands every request to a FastAPI TestClient, so no socket is opened.

    It lets the real RiskApiClient talk to the real app in a test: the client builds and sends real HTTP requests,
    and the app parses them with its real request and response models.
    """

    def __init__(self, app_client: TestClient) -> None:
        """Forward to app_client; sent counts the requests that reached the app."""
        self.app_client = app_client
        self.sent = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Replay the request on the app and return its answer unchanged."""
        self.sent += 1
        answer = self.app_client.request(
            request.method, str(request.url), content=request.read(), headers=request.headers
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)
