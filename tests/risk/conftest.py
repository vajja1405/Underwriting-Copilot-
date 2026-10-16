"""Shared fixtures for the scoring API tests: tiny SYNTHETIC models, trained once per test session.

test_service.py and test_client_service_integration.py both use them, so the models are trained only once
(about 4 s). The builders live in risk_service_helpers.py; see its docstring.
"""

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from risk_service_helpers import (
    synthetic_credit_loans,
    synthetic_credit_models,
    synthetic_insurance_bundle,
    synthetic_insurance_splits,
)

from uwcopilot.risk import credit, insurance
from uwcopilot.risk.service import create_app


@pytest.fixture(scope="session")
def insurance_data() -> dict[str, pd.DataFrame]:
    """SYNTHETIC freMTPL2-shaped policies, split 60/20/20, plus severity tables."""
    return synthetic_insurance_splits()


@pytest.fixture(scope="session")
def insurance_bundle(insurance_data: dict[str, pd.DataFrame]) -> insurance.InsuranceModels:
    """All four insurance models, with the metadata the training script writes (bands included)."""
    return synthetic_insurance_bundle(insurance_data)


@pytest.fixture(scope="session")
def insurance_dir(tmp_path_factory: pytest.TempPathFactory, insurance_bundle: insurance.InsuranceModels) -> Path:
    """The insurance models saved the way models/insurance is laid out."""
    path = tmp_path_factory.mktemp("models") / "insurance"
    insurance.save_models(insurance_bundle, path)
    return path


@pytest.fixture(scope="session")
def credit_loans() -> pd.DataFrame:
    """SYNTHETIC Lending Club-shaped loans."""
    return synthetic_credit_loans()


@pytest.fixture(scope="session")
def credit_models(credit_loans: pd.DataFrame) -> credit.CreditModels:
    """Credit models trained on SYNTHETIC loans with small, fast settings."""
    return synthetic_credit_models(credit_loans)


@pytest.fixture(scope="session")
def credit_dir(tmp_path_factory: pytest.TempPathFactory, credit_models: credit.CreditModels) -> Path:
    """The credit models saved the way models/credit is laid out."""
    path = tmp_path_factory.mktemp("models") / "credit"
    credit.save_credit_models(credit_models, path)
    return path


@pytest.fixture(scope="session")
def client(credit_dir: Path, insurance_dir: Path) -> Iterator[TestClient]:
    """The API with every product loaded."""
    with TestClient(create_app(credit_dir=credit_dir, insurance_dir=insurance_dir)) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def client_without_credit(tmp_path_factory: pytest.TempPathFactory, insurance_dir: Path) -> Iterator[TestClient]:
    """The API as a fresh checkout runs it: no credit model artifact (models/ is gitignored), insurance present."""
    missing = tmp_path_factory.mktemp("empty") / "credit"
    with TestClient(create_app(credit_dir=missing, insurance_dir=insurance_dir)) as test_client:
        yield test_client
