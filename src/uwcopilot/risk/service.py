"""Scoring API for the Phase 1 risk models (FastAPI): POST /score and GET /health.

Run it with `make serve` (uvicorn on http://127.0.0.1:8000; interactive docs at /docs).

POST /score takes a product, the features of one applicant or policy, and optional scenario overrides:

    {"product": "insurance_frequency",
     "features": {"VehPower": 6, "VehAge": 2, "DrivAge": 45, "BonusMalus": 50, "Density": 1200,
                  "Area": "C", "VehGas": "Regular", "VehBrand": "B12", "Region": "R24"},
     "overrides": {"BonusMalus": 80}}

and returns the prediction, its risk band, the top-4 reason codes, and the model version. With overrides it
also returns the baseline and the scenario side by side (a what-if for stress tests).

Products and what "prediction" means:

    credit               probability of default of a finished loan over its life (Lending Club model)
    insurance_frequency  expected claims per policy-year (freMTPL2 model)
    insurance_severity   expected average claim amount per claim, capped basis (freMTPL2 model)

Each insurance product is served by the model that won on the validation split (recorded as
preferred_model in models/insurance/metadata.json), so the API serves the same model the results report.

Rules the API enforces:

- Risk bands come from quantiles of the served model's training predictions, stored in the model metadata
  by the training scripts (uwcopilot.risk.bands). No cut-off is typed by hand.
- Overrides are validated against the training range of each feature (uwcopilot.risk.scenario). An
  out-of-range or unknown value gets HTTP 422; nothing is clipped.
- Input features must all be present and inside their training ranges, categories must have been seen in
  training, and a feature whose training values were all whole numbers takes only whole numbers (HTTP 422
  otherwise), so the models never extrapolate silently. A credit feature may be sent as null only if it was
  missing in some training loans (for example months since the last delinquency); a key left out is
  rejected, never filled in.
- Credit requests pass the leakage guard (uwcopilot.risk.leakage) before anything else: a banned
  post-origination column in the features or overrides gets HTTP 422, even when no credit model is loaded.
- A product whose model artifact is missing gets HTTP 503 with the reason. Artifacts are gitignored, so a fresh
  checkout has none until `make train-insurance` (and, with a Kaggle token for the Lending Club data,
  `make train-credit`) has run.
- The shared HTTP client (uwcopilot.risk.client) speaks exactly this contract; the agent's and the MCP server's
  tool contract is translated there (docs/risk_service.md).
- The API supports decisions; it never makes one. A person decides (ground rule 8).
"""

import math
import pickle
import threading
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import lightgbm as lgb
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot import __version__
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.risk import credit, insurance
from uwcopilot.risk.bands import assign_band
from uwcopilot.risk.data.fremtpl2 import RATING_FEATURES
from uwcopilot.risk.explain import TreeShapExplainer, reason_codes
from uwcopilot.risk.leakage import LeakageError, assert_no_leakage
from uwcopilot.risk.scenario import InputFeaturesError, OverrideError, apply_overrides, validate_overrides

Product = Literal["credit", "insurance_frequency", "insurance_severity"]
PRODUCTS: tuple[str, ...] = ("credit", "insurance_frequency", "insurance_severity")
TOP_K_REASONS = 4

DEFAULT_CREDIT_DIR = REPO_ROOT / "models" / "credit"
DEFAULT_INSURANCE_DIR = REPO_ROOT / "models" / "insurance"

DECISION_NOTE = "Decision support only: a person makes every underwriting decision."
CREDIT_MISSING = (
    "No credit model artifact at {path}. Model artifacts are not committed (models/ is gitignored), and training "
    "the credit model needs the Lending Club data, which needs a Kaggle API token. Add the token, run `make data`, "
    "then `make train-credit`."
)
INSURANCE_MISSING = "No insurance model artifact at {path}. Run `make train-insurance` to train the freMTPL2 models."

# What can go wrong when reading a saved model: missing or unreadable files, bad JSON, missing metadata keys,
# a pickle written by different code, or a LightGBM file that does not parse.
LOAD_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    KeyError,
    AttributeError,
    ImportError,
    pickle.UnpicklingError,
    lgb.basic.LightGBMError,
)


class ScoringError(ValueError):
    """A request that cannot be scored as sent (HTTP 422). kind says which check failed."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Request and response schemas
# ---------------------------------------------------------------------------


class ScoreRequest(BaseModel):
    """One applicant (credit) or policy (insurance) to score, with optional scenario overrides."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "product": "insurance_frequency",
                    "features": {
                        "VehPower": 6,
                        "VehAge": 2,
                        "DrivAge": 45,
                        "BonusMalus": 50,
                        "Density": 1200,
                        "Area": "C",
                        "VehGas": "Regular",
                        "VehBrand": "B12",
                        "Region": "R24",
                    },
                    "overrides": {"BonusMalus": 80},
                }
            ]
        },
    )

    product: Product
    features: dict[str, Any] = Field(description="Feature name -> value for one applicant or policy.")
    overrides: dict[str, Any] | None = Field(
        default=None, description="Scenario changes (feature -> new value). Validated, never clipped."
    )


class ReasonCode(BaseModel):
    """One of the top reasons the model's risk estimate is higher than for an average training case."""

    feature: str
    value: str | int | float | None = Field(description="The feature value scored (null if it was missing).")
    contribution: float = Field(
        description="Log-scale contribution: log-odds for credit, log of the rate for insurance."
    )
    reason: str


class Assessment(BaseModel):
    """Prediction, risk band and reasons for one set of feature values."""

    prediction: float
    risk_band: str
    reason_codes: list[ReasonCode]


class ScenarioResult(BaseModel):
    """Baseline vs scenario after the overrides were applied."""

    overrides: dict[str, Any]
    baseline: Assessment
    scenario: Assessment
    prediction_change: float = Field(description="scenario prediction minus baseline prediction")
    relative_change: float | None = Field(description="prediction_change / baseline prediction (null if baseline is 0)")
    risk_band_changed: bool


class RiskBandDefinition(BaseModel):
    """How the risk bands were made: quantiles of the served model's training predictions."""

    method: str
    score: str
    population: str
    quantiles: list[float]
    cutoffs: list[float]
    labels: list[str]
    rule: str


class ScoreResponse(BaseModel):
    """Result of POST /score. The top-level prediction is the baseline (before overrides)."""

    product: Product
    prediction_name: str
    prediction_unit: str
    prediction: float
    risk_band: str
    reason_codes: list[ReasonCode]
    model: str
    model_version: str
    risk_band_definition: RiskBandDefinition
    scenario: ScenarioResult | None = None
    note: str = DECISION_NOTE


class ProductStatus(BaseModel):
    """Whether one product can be scored, and with which model."""

    loaded: bool
    model: str | None = None
    model_version: str | None = None
    prediction_name: str | None = None
    prediction_unit: str | None = None
    features: list[str] | None = None
    risk_band_definition: RiskBandDefinition | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    """Result of GET /health."""

    status: Literal["ok", "degraded"]
    version: str
    models_loaded: list[str]
    products: dict[str, ProductStatus]


# ---------------------------------------------------------------------------
# Scorers: one per product, each wrapping a fitted model
# ---------------------------------------------------------------------------


def _json_value(value: Any) -> Any:
    """A feature value that JSON can carry: NaN (a missing value) becomes None, numpy scalars become Python."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _clean_reasons(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reason codes with JSON-safe values."""
    return [{**reason, "value": _json_value(reason["value"])} for reason in reasons]


@dataclass
class ProductScorer:
    """What every product's scorer offers the API. Subclasses implement assess() and check_inputs()."""

    product: str
    model_name: str
    model_version: str
    prediction_name: str
    prediction_unit: str
    risk_bands: dict[str, Any]
    features: list[str] = field(default_factory=list)

    def check_inputs(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> None:
        """Raise ScoringError if the request cannot be scored."""
        raise NotImplementedError

    def assess(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> tuple[dict, dict | None]:
        """(baseline, scenario or None), each {"prediction", "risk_band", "reason_codes"}."""
        raise NotImplementedError


@dataclass
class CreditScorer(ProductScorer):
    """Lending Club PD model (the model chosen on the 2016 validation year)."""

    models: credit.CreditModels | None = None

    @classmethod
    def from_models(cls, models: credit.CreditModels) -> "CreditScorer":
        """Wrap loaded credit models; raises ValueError if the metadata has no risk bands."""
        return cls(
            product="credit",
            model_name=models.chosen,
            model_version=str(models.metadata.get("model_version", "unknown")),
            prediction_name="probability_of_default",
            prediction_unit="probability (0 to 1) that a finished loan is charged off or defaults over its life",
            risk_bands=models.risk_bands,
            features=list(credit.CREDIT_FEATURES),
            models=models,
        )

    def check_inputs(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> None:
        """Every credit feature present (null only where training loans had missing values), every value
        inside its training range or a category seen in training, and every override valid.

        The leakage guard has already run in the endpoint, before any model is needed.
        """
        try:
            self.models.check_features(features)
        except InputFeaturesError as exc:
            raise ScoringError("invalid_features", str(exc)) from exc
        try:
            validate_overrides(overrides, self.models.feature_ranges)
        except OverrideError as exc:
            raise ScoringError("invalid_override", str(exc)) from exc

    def assess(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> tuple[dict, dict | None]:
        """Score with CreditModels.score and rename its keys to the API's names."""
        try:
            result = self.models.score(features, overrides)
        except LeakageError as exc:
            raise ScoringError("leakage_guard", str(exc)) from exc
        except OverrideError as exc:
            raise ScoringError("invalid_override", str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise ScoringError("invalid_features", str(exc)) from exc

        def convert(entry: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "prediction": entry["pd"],
                "risk_band": entry["risk_band"],
                "reason_codes": _clean_reasons(entry["reason_codes"]),
            }

        scenario = convert(result["scenario"]) if result["scenario"] is not None else None
        return convert(result["baseline"]), scenario


def insurance_model_version(metadata: Mapping[str, Any], target: str, model_name: str) -> str:
    """Version string for a served insurance model.

    LightGBM models are in the MLflow model registry ("insurance_frequency_lgbm v2"). The GLMs are saved in
    models/insurance/ but not registered, so they are identified by their training run and git commit.
    """
    if model_name == "lgbm":
        entry = metadata["model_version"][f"{target}_lgbm"]
        return f"{entry['registered_model']} v{entry['version']}"
    run_id = metadata.get("mlflow_runs", {}).get(target, "unknown")
    commit = str(metadata.get("git_commit", "unknown"))[:12]
    return f"insurance_{target}_glm (MLflow run {run_id}, commit {commit})"


@dataclass
class InsuranceScorer(ProductScorer):
    """freMTPL2 frequency or severity model (the one preferred on the validation split)."""

    model: Any = None
    feature_ranges: dict[str, Any] = field(default_factory=dict)
    reason_map: dict[str, str] = field(default_factory=dict)
    explainer: TreeShapExplainer | None = None

    @classmethod
    def from_models(
        cls, models: insurance.InsuranceModels, target: Literal["frequency", "severity"]
    ) -> "InsuranceScorer":
        """Wrap the preferred model for one target; raises KeyError/ValueError if the metadata is incomplete."""
        metadata = models.metadata
        product = f"insurance_{target}"
        model_name = metadata["preferred_model"][target]
        bands = metadata["risk_bands"][product]
        if bands.get("model") != model_name:
            raise ValueError(f"{product}: risk bands were computed for '{bands.get('model')}', not '{model_name}'")
        model = models.model_for(target, model_name)
        explainer = TreeShapExplainer(model.booster) if isinstance(model, insurance.TreeModel) else None
        reasons = insurance.FREQUENCY_REASONS_INCREASE if target == "frequency" else insurance.SEVERITY_REASONS_INCREASE
        return cls(
            product=product,
            model_name=model_name,
            model_version=insurance_model_version(metadata, target, model_name),
            prediction_name=f"expected_claim_{target}",
            prediction_unit=str(metadata["prediction_units"][target]),
            risk_bands=bands,
            features=list(RATING_FEATURES),
            model=model,
            feature_ranges=models.feature_ranges,
            reason_map=reasons,
            explainer=explainer,
        )

    def check_inputs(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> None:
        """Every rating feature present, nothing else, every value inside its training range."""
        unknown = sorted(set(features) - set(RATING_FEATURES))
        missing = sorted(set(RATING_FEATURES) - set(features))
        if unknown or missing:
            parts = []
            if unknown:
                parts.append(f"unknown features {unknown} (only the nine rating features are accepted)")
            if missing:
                parts.append(f"missing features {missing}")
            raise ScoringError("invalid_features", "; ".join(parts))
        try:
            validate_overrides(features, self.feature_ranges, what="input features")
        except OverrideError as exc:
            raise ScoringError("invalid_features", str(exc)) from exc
        try:
            validate_overrides(overrides, self.feature_ranges)
        except OverrideError as exc:
            raise ScoringError("invalid_override", str(exc)) from exc

    def _assess_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Prediction, band and top reasons for one policy."""
        frame = pd.DataFrame([dict(row)])
        prediction = float(self.model.predict_rate(frame)[0])
        if isinstance(self.model, insurance.TreeModel):
            values, _ = self.model.contributions(frame, self.explainer)
        else:
            values, _ = self.model.contributions(frame)
        reasons = reason_codes(
            values[0],
            insurance.TREE_FEATURES,
            [row[name] for name in insurance.TREE_FEATURES],
            self.reason_map,
            top_k=TOP_K_REASONS,
        )
        return {
            "prediction": prediction,
            "risk_band": assign_band(prediction, self.risk_bands),
            "reason_codes": _clean_reasons(reasons),
        }

    def assess(self, features: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> tuple[dict, dict | None]:
        """Baseline, and the scenario after overrides (validated by check_inputs, applied without clipping)."""
        row = {name: features[name] for name in RATING_FEATURES}
        baseline = self._assess_row(row)
        if not overrides:
            return baseline, None
        scenario_row = apply_overrides(row, overrides, self.feature_ranges)
        return baseline, self._assess_row(scenario_row)


# ---------------------------------------------------------------------------
# Loading the models
# ---------------------------------------------------------------------------


def _shown(path: Path) -> str:
    """Path relative to the repo root when possible (messages never show machine-specific folders)."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT)) + "/"
    except ValueError:
        return str(path)


@dataclass
class ModelRegistry:
    """The scorers that loaded, and for every other product the reason it is unavailable."""

    scorers: dict[str, ProductScorer]
    unavailable: dict[str, str]

    @classmethod
    def load(cls, credit_dir: Path, insurance_dir: Path) -> "ModelRegistry":
        """Load whatever model artifacts exist. A missing or broken artifact makes its product unavailable."""
        scorers: dict[str, ProductScorer] = {}
        unavailable: dict[str, str] = {}

        if not (Path(credit_dir) / credit.METADATA_FILE).exists():
            unavailable["credit"] = CREDIT_MISSING.format(path=_shown(credit_dir))
        else:
            try:
                scorers["credit"] = CreditScorer.from_models(credit.load_credit_models(credit_dir))
            except LOAD_ERRORS as exc:
                unavailable["credit"] = f"credit model at {_shown(credit_dir)} could not be loaded: {exc}"

        if not (Path(insurance_dir) / insurance.METADATA_FILE).exists():
            for target in ("frequency", "severity"):
                unavailable[f"insurance_{target}"] = INSURANCE_MISSING.format(path=_shown(insurance_dir))
        else:
            try:
                models = insurance.load_models(insurance_dir)
            except LOAD_ERRORS as exc:
                models = None
                for target in ("frequency", "severity"):
                    unavailable[f"insurance_{target}"] = (
                        f"insurance models at {_shown(insurance_dir)} could not be loaded: {exc}"
                    )
            if models is not None:
                for target in ("frequency", "severity"):
                    product = f"insurance_{target}"
                    try:
                        scorers[product] = InsuranceScorer.from_models(models, target)
                    except LOAD_ERRORS as exc:
                        unavailable[product] = (
                            f"{product} cannot be served from {_shown(insurance_dir)} ({exc!r}); "
                            "rerun `make train-insurance` with the current code"
                        )
        return cls(scorers=scorers, unavailable=unavailable)


# ---------------------------------------------------------------------------
# The FastAPI application
# ---------------------------------------------------------------------------


def _scenario_result(overrides: Mapping[str, Any], baseline: dict, scenario: dict) -> ScenarioResult:
    """Side-by-side comparison for a scenario."""
    change = scenario["prediction"] - baseline["prediction"]
    relative = change / baseline["prediction"] if baseline["prediction"] != 0 else None
    return ScenarioResult(
        overrides=dict(overrides),
        baseline=Assessment(**baseline),
        scenario=Assessment(**scenario),
        prediction_change=change,
        relative_change=relative,
        risk_band_changed=scenario["risk_band"] != baseline["risk_band"],
    )


def _band_definition(bands: Mapping[str, Any]) -> RiskBandDefinition:
    """The stored band description, keeping only the fields the API shows."""
    return RiskBandDefinition(**{name: bands[name] for name in RiskBandDefinition.model_fields})


def create_app(credit_dir: Path = DEFAULT_CREDIT_DIR, insurance_dir: Path = DEFAULT_INSURANCE_DIR) -> FastAPI:
    """Build the API. Models are loaded once, at startup (or on the first request if there was no startup)."""
    lock = threading.Lock()

    def registry(app: FastAPI) -> ModelRegistry:
        with lock:
            if app.state.registry is None:
                app.state.registry = ModelRegistry.load(credit_dir, insurance_dir)
            return app.state.registry

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry(app)
        yield

    app = FastAPI(
        title="Underwriting Copilot risk scoring API",
        version=__version__,
        description=__doc__,
        lifespan=lifespan,
    )
    app.state.registry = None

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        """Which products can be scored, with which model version and risk bands."""
        loaded = registry(request.app)
        products: dict[str, ProductStatus] = {}
        for product in PRODUCTS:
            scorer = loaded.scorers.get(product)
            if scorer is None:
                products[product] = ProductStatus(loaded=False, detail=loaded.unavailable.get(product))
                continue
            products[product] = ProductStatus(
                loaded=True,
                model=scorer.model_name,
                model_version=scorer.model_version,
                prediction_name=scorer.prediction_name,
                prediction_unit=scorer.prediction_unit,
                features=scorer.features,
                risk_band_definition=_band_definition(scorer.risk_bands),
            )
        names = [product for product in PRODUCTS if product in loaded.scorers]
        return HealthResponse(
            status="ok" if len(names) == len(PRODUCTS) else "degraded",
            version=__version__,
            models_loaded=names,
            products=products,
        )

    @app.post("/score", response_model=ScoreResponse)
    def score(body: ScoreRequest, request: Request) -> ScoreResponse:
        """Score one applicant or policy; with overrides, also the scenario."""
        if body.product == "credit":
            # The leakage guard needs no model, so it runs first: a banned column is a bad request even today.
            try:
                assert_no_leakage(list(body.features) + list(body.overrides or {}))
            except LeakageError as exc:
                raise HTTPException(422, detail={"error": "leakage_guard", "message": str(exc)}) from exc
        loaded = registry(request.app)
        scorer = loaded.scorers.get(body.product)
        if scorer is None:
            detail = {
                "error": "model_unavailable",
                "product": body.product,
                "message": loaded.unavailable[body.product],
            }
            raise HTTPException(503, detail=detail)
        try:
            scorer.check_inputs(body.features, body.overrides)
            baseline, scenario = scorer.assess(body.features, body.overrides)
        except ScoringError as exc:
            raise HTTPException(422, detail={"error": exc.kind, "message": str(exc)}) from exc
        return ScoreResponse(
            product=body.product,
            prediction_name=scorer.prediction_name,
            prediction_unit=scorer.prediction_unit,
            prediction=baseline["prediction"],
            risk_band=baseline["risk_band"],
            reason_codes=[ReasonCode(**reason) for reason in baseline["reason_codes"]],
            model=scorer.model_name,
            model_version=scorer.model_version,
            risk_band_definition=_band_definition(scorer.risk_bands),
            scenario=_scenario_result(body.overrides, baseline, scenario) if scenario is not None else None,
        )

    return app


app = create_app()
