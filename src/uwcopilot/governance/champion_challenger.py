"""Champion/challenger for the credit PD model (Phase 6 C): retrain on newer loans, compare on the same later loans.

The question a model validator has to answer: "the model was retrained with one more year of loans; is the new model
(the challenger) better than the model in use (the champion) on loans that neither of them has seen?" This module
computes the evidence and a recommendation from a rule fixed in advance. It never changes which model is in use: it
registers the champion as "champion" and the challenger as "challenger" in the model status registry
(governance/model_status.py), and only a model validator can promote a challenger (scripts/promote_model.py).

Windows (issue years, derived from the credit model's own split in risk/credit.py)
    champion:     fitted on loans issued <= 2014; early stopping and Platt recalibration on 2015
    challenger:   fitted on loans issued <= 2015; early stopping and Platt recalibration on 2016. That is one more
                  vintage, and exactly the split risk/credit.py uses for the credit model.
    out of time:  finished loans issued 2017-2018, which neither model saw. Both models score the SAME rows.

Model
    Both models are LightGBM fitted by risk/credit.py fit_lightgbm, the algorithm and code of the registered credit
    model (class weighting, early stopping on validation AUC, Platt recalibration on the validation year). Nothing of
    the training is copied here; only the years differ.

Comparison (on the out-of-time rows)
    Each model: AUC, KS, Gini, Brier and ECE with percentile bootstrap 95% CIs (risk/credit.py binary_metrics).
    The pair: paired bootstrap of challenger minus champion for AUC, KS, Brier and ECE (risk/bootstrap.py
    paired_bootstrap_diff; 1,000 resamples, seed 42; each resample uses the same rows for both models).

Recommendation (PromotionRule, fixed before any result is seen)
    "recommend_promotion" only if
      1. the lower end of the 95% CI of AUC(challenger) - AUC(champion) is above 0: the challenger ranks loans
         better and the gain is unlikely to be resampling noise; and
      2. the upper end of the 95% CI of ECE(challenger) - ECE(champion) is at most 0.01: calibration is not worse by
         more than one percentage point (a non-inferiority check).
    Otherwise "keep_champion". Either way it is advice to a model validator, never an action.

Data
    The Lending Club data is not in this checkout (it needs a Kaggle key), so scripts/champion_challenger.py runs this
    on SYNTHETIC Lending Club-shaped loans (risk/data/lending_club.py synthetic_accepted_loans). Every number it
    writes describes that SYNTHETIC data, not real loans.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import lightgbm as lgb
import numpy as np
import pandas as pd

from uwcopilot.governance.model_status import ModelEntry, ModelStatusStore
from uwcopilot.risk.bootstrap import paired_bootstrap_diff
from uwcopilot.risk.credit import (
    CREDIT_FEATURES,
    TEST_YEARS,
    TRAIN_LAST_YEAR,
    VALIDATION_YEAR,
    CategoryEncoder,
    CreditConfig,
    LightGbmModel,
    binary_metrics,
    ece_metric,
    fit_lightgbm,
)
from uwcopilot.risk.data.lending_club import finished_loans
from uwcopilot.risk.leakage import TARGET_COLUMN, assert_no_leakage
from uwcopilot.risk.metrics import brier_score, ks_statistic, roc_auc
from uwcopilot.risk.results import to_jsonable

ModelRole = Literal["champion", "challenger"]
Recommendation = Literal["recommend_promotion", "keep_champion"]

FAMILY = "synthetic_lc_credit_pd"
ALGORITHM = "lightgbm"
DATA_LABEL = "SYNTHETIC Lending Club-shaped loans (risk/data/lending_club.py synthetic_accepted_loans)"
MODEL_ID_PREFIX = "SYNTHETIC-lc-pd-lightgbm"
BOOSTER_FILE = "lightgbm.txt"
METADATA_FILE = "metadata.json"

# What each paired difference means; for Brier and ECE a negative difference is an improvement.
PAIRED_METRICS: dict[str, tuple[Callable[..., float], str]] = {
    "auc": (roc_auc, "higher is better"),
    "ks": (ks_statistic, "higher is better"),
    "brier": (brier_score, "lower is better"),
    "ece": (ece_metric, "lower is better"),
}

LIMITATIONS = [
    "SYNTHETIC data: the generator draws every issue year from the same distribution (no drift), so this shows the "
    "procedure, not how much a retrain helps on real loans. Real champion/challenger evidence needs the Lending Club "
    "data (a Kaggle key) and the trained credit model.",
    "Finished loans only. In the SYNTHETIC data a loan is unfinished at random; in the real data the finished loans "
    "of 2017-2018 are mostly early payoffs and early charge-offs (right censoring), which would affect both models.",
    "Platt recalibration and early stopping use each model's own last development year, as in risk/credit.py.",
    "One comparison window (2017-2018). A model validator would also look at stability across windows, segments, "
    "and qualitative changes (features, data sources, code) before promoting.",
]


@dataclass(frozen=True)
class TrainingWindow:
    """The issue years one model learns from: fitted on years <= train_last_year, early stopping and Platt on
    validation_year (the same roles as train and validation in risk/credit.py)."""

    train_last_year: int
    validation_year: int

    def split(self, finished: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(training rows, validation rows) of the finished loans."""
        year = finished["issue_year"]
        train = finished.loc[year <= self.train_last_year].reset_index(drop=True)
        validation = finished.loc[year == self.validation_year].reset_index(drop=True)
        return train, validation

    def describe(self) -> str:
        """One line for the results file."""
        return (
            f"fitted on issue years <= {self.train_last_year}; early stopping and Platt recalibration on "
            f"{self.validation_year}"
        )


CHAMPION_WINDOW = TrainingWindow(train_last_year=TRAIN_LAST_YEAR - 1, validation_year=VALIDATION_YEAR - 1)
CHALLENGER_WINDOW = TrainingWindow(train_last_year=TRAIN_LAST_YEAR, validation_year=VALIDATION_YEAR)
OUT_OF_TIME_YEARS: tuple[int, ...] = TEST_YEARS


@dataclass(frozen=True)
class PromotionRule:
    """The rule behind the recommendation (module docstring). Fixed before the comparison is run."""

    min_auc_difference_ci_low: float = 0.0
    max_ece_worsening: float = 0.01

    def describe(self) -> str:
        """The rule in words, with its numbers."""
        return (
            "recommend promotion only if the lower end of the paired bootstrap 95% CI of AUC(challenger) - "
            f"AUC(champion) is above {self.min_auc_difference_ci_low} AND the upper end of the paired bootstrap 95% "
            f"CI of ECE(challenger) - ECE(champion) is at most {self.max_ece_worsening}; otherwise keep the champion. "
            "The recommendation is advice to a model validator; this code never promotes."
        )


def check_windows(champion: TrainingWindow, challenger: TrainingWindow, out_of_time_years: tuple[int, ...]) -> None:
    """ValueError unless the challenger sees newer loans than the champion and neither sees the out-of-time years."""
    for name, window in (("champion", champion), ("challenger", challenger)):
        if window.validation_year <= window.train_last_year:
            raise ValueError(f"the {name} validation year must come after its training years")
    if challenger.validation_year <= champion.validation_year:
        raise ValueError("the challenger must be trained on newer loans than the champion")
    if challenger.train_last_year < champion.train_last_year:
        raise ValueError("the challenger must keep at least the champion's training years")
    if not out_of_time_years or min(out_of_time_years) <= challenger.validation_year:
        raise ValueError("the out-of-time years must come after every year either model saw")


def rows_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Rows, bad loans, bad rate and issue years of a set of finished loans."""
    n_bad = int(frame[TARGET_COLUMN].sum())
    return {
        "n_rows": int(len(frame)),
        "n_bad": n_bad,
        "bad_rate": n_bad / len(frame) if len(frame) else None,
        "issue_years": sorted(int(year) for year in frame["issue_year"].unique()),
    }


def require_both_classes(frame: pd.DataFrame, what: str) -> None:
    """ValueError unless the rows hold good and bad loans (AUC and calibration need both)."""
    labels = set(frame[TARGET_COLUMN].unique().tolist())
    if labels != {0, 1}:
        raise ValueError(f"{what} needs both good and bad loans, found labels {sorted(labels)}")


def fit_window_model(finished: pd.DataFrame, window: TrainingWindow, config: CreditConfig) -> LightGbmModel:
    """LightGBM fitted by risk/credit.py fit_lightgbm on the window's years of finished loans."""
    train, validation = window.split(finished)
    require_both_classes(train, f"training years <= {window.train_last_year}")
    require_both_classes(validation, f"validation year {window.validation_year}")
    return fit_lightgbm(train, validation, config)


def training_summary(model: LightGbmModel, window: TrainingWindow) -> dict[str, Any]:
    """The model's training_info, with the validation year named correctly.

    fit_lightgbm describes its validation set as "2016" because that is the credit model's split; here the champion
    validates on another year, so those two labels are rewritten with the window's actual year.
    """
    info = dict(model.training_info)
    info["early_stopping_metric"] = f"validation AUC ({window.validation_year})"
    recalibration = dict(info.get("recalibration") or {})
    recalibration["method"] = (
        f"Platt scaling on the {window.validation_year} validation set (same set used for early stopping)"
    )
    info["recalibration"] = recalibration
    return info


def out_of_time_rows(finished: pd.DataFrame, years: tuple[int, ...]) -> pd.DataFrame:
    """The finished loans issued in the out-of-time years."""
    return finished.loc[finished["issue_year"].isin(years)].reset_index(drop=True)


def compare_models(
    y_true: np.ndarray, p_champion: np.ndarray, p_challenger: np.ndarray, config: CreditConfig
) -> dict[str, Any]:
    """Each model's metrics with bootstrap CIs and the paired bootstrap differences (challenger - champion)."""
    differences: dict[str, Any] = {}
    for name, (metric, direction) in PAIRED_METRICS.items():
        result = paired_bootstrap_diff(
            metric,
            y_true,
            p_challenger,
            p_champion,
            n_resamples=config.n_resamples,
            seed=config.seed,
            alpha=config.alpha,
        )
        result["definition"] = f"{name}(challenger) - {name}(champion), paired bootstrap on the same rows"
        result["direction"] = direction
        differences[name] = result
    return {
        "champion": binary_metrics(y_true, p_champion, config),
        "challenger": binary_metrics(y_true, p_challenger, config),
        "paired_difference_challenger_minus_champion": differences,
    }


def recommend(differences: Mapping[str, Mapping[str, Any]], rule: PromotionRule) -> dict[str, Any]:
    """Apply the PromotionRule to the paired differences. A NaN bound never meets a condition."""
    auc_low = float(differences["auc"]["ci_low"])
    ece_high = float(differences["ece"]["ci_high"])
    auc_met = auc_low > rule.min_auc_difference_ci_low
    ece_met = ece_high <= rule.max_ece_worsening
    recommendation: Recommendation = "recommend_promotion" if auc_met and ece_met else "keep_champion"
    conditions = [
        {
            "condition": f"AUC difference CI low > {rule.min_auc_difference_ci_low}",
            "value": auc_low,
            "met": auc_met,
        },
        {
            "condition": f"ECE difference CI high <= {rule.max_ece_worsening}",
            "value": ece_high,
            "met": ece_met,
        },
    ]
    unmet = [item["condition"] for item in conditions if not item["met"]]
    reason = "both conditions are met" if not unmet else "not met: " + "; ".join(unmet)
    return {
        "recommendation": recommendation,
        "reason": reason,
        "rule": rule.describe(),
        "conditions": conditions,
        "promoted_by_this_run": False,
        "who_decides": "a model_validator, with scripts/promote_model.py (security/rbac.py action promote_model)",
    }


# ---------------------------------------------------------------------------
# Saving the two models and registering them
# ---------------------------------------------------------------------------


def model_fingerprint(model: LightGbmModel) -> str:
    """SHA-256 of everything that sets the model's predictions: trees, Platt parameters, category codes, features."""
    parts = {
        "booster": model.booster.model_to_string(),
        "calibration_slope": model.calibration_slope,
        "calibration_intercept": model.calibration_intercept,
        "category_codes": model.encoder.codes,
        "features": model.features,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def model_id_for(window: TrainingWindow, fingerprint: str) -> str:
    """A model id that names the last year the model saw and the start of its fingerprint."""
    return f"{MODEL_ID_PREFIX}-to{window.validation_year}-{fingerprint[:10]}"


def save_model_bundle(model: LightGbmModel, model_id: str, window: TrainingWindow, models_dir: Path) -> dict[str, Path]:
    """Write the booster (LightGBM text) and metadata.json (Platt parameters, category codes, window) to
    models_dir/model_id/. Plain text and JSON, never a pickle, so loading cannot run code."""
    folder = Path(models_dir) / model_id
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"booster": folder / BOOSTER_FILE, "metadata": folder / METADATA_FILE}
    model.booster.save_model(str(paths["booster"]))
    metadata = {
        "model_id": model_id,
        "data_label": DATA_LABEL,
        "algorithm": ALGORITHM,
        "fingerprint_sha256": model_fingerprint(model),
        "window": {"train_last_year": window.train_last_year, "validation_year": window.validation_year},
        "features": model.features,
        "category_codes": model.encoder.codes,
        "calibration_slope": model.calibration_slope,
        "calibration_intercept": model.calibration_intercept,
        "training_info": training_summary(model, window),
    }
    text = json.dumps(to_jsonable(metadata), allow_nan=False, indent=1, sort_keys=True) + "\n"
    paths["metadata"].write_text(text, encoding="utf-8")
    return paths


def load_model_bundle(folder: Path) -> LightGbmModel:
    """Rebuild a model written by save_model_bundle."""
    metadata = json.loads((Path(folder) / METADATA_FILE).read_text(encoding="utf-8"))
    return LightGbmModel(
        booster=lgb.Booster(model_file=str(Path(folder) / BOOSTER_FILE)),
        encoder=CategoryEncoder(codes={column: dict(codes) for column, codes in metadata["category_codes"].items()}),
        features=list(metadata["features"]),
        calibration_slope=float(metadata["calibration_slope"]),
        calibration_intercept=float(metadata["calibration_intercept"]),
        training_info=dict(metadata["training_info"]),
    )


def register_model(
    store: ModelStatusStore, model_id: str, role: ModelRole, version: str, reason: str
) -> dict[str, Any]:
    """Register a model with its role, unless it is registered already; then its status is left exactly as it is.

    Raises ValueError (from the store) when the role is champion and the family already has another model in use.
    """
    try:
        entry = store.entry(model_id)
        note = "already registered; status left unchanged"
    except KeyError:
        entry = store.register(
            ModelEntry(model_id=model_id, family=FAMILY, version=version, status=role, data_label=DATA_LABEL),
            reason=reason,
        )
        note = f"registered as {role} by this run"
    return {
        "model_id": model_id,
        "role_in_this_comparison": role,
        "registration": note,
        "status_in_registry": entry.status,
        "history": [change.model_dump(mode="json") for change in entry.history],
    }


# ---------------------------------------------------------------------------
# The whole comparison
# ---------------------------------------------------------------------------


@dataclass
class ChampionChallengerRun:
    """The fitted models, the out-of-time rows and the JSON-ready comparison (no registry entries yet)."""

    champion: LightGbmModel
    challenger: LightGbmModel
    champion_window: TrainingWindow
    challenger_window: TrainingWindow
    out_of_time: pd.DataFrame
    payload: dict[str, Any]


def run_champion_challenger(
    loans: pd.DataFrame,
    config: CreditConfig,
    rule: PromotionRule | None = None,
    champion_window: TrainingWindow = CHAMPION_WINDOW,
    challenger_window: TrainingWindow = CHALLENGER_WINDOW,
    out_of_time_years: tuple[int, ...] = OUT_OF_TIME_YEARS,
) -> ChampionChallengerRun:
    """Fit both models, score the same out-of-time rows and apply the rule. Writes nothing and promotes nothing.

    loans: prepared loans (risk/data/lending_club.py prepare_loans), finished and unfinished.
    """
    rule = rule or PromotionRule()
    check_windows(champion_window, challenger_window, out_of_time_years)
    assert_no_leakage(CREDIT_FEATURES)
    finished = finished_loans(loans)
    champion = fit_window_model(finished, champion_window, config)
    challenger = fit_window_model(finished, challenger_window, config)
    out_of_time = out_of_time_rows(finished, out_of_time_years)
    require_both_classes(out_of_time, "the out-of-time window")

    y_true = out_of_time[TARGET_COLUMN].to_numpy()
    comparison = compare_models(y_true, champion.predict_pd(out_of_time), challenger.predict_pd(out_of_time), config)
    windows: dict[str, Any] = {}
    for role, window in (("champion", champion_window), ("challenger", challenger_window)):
        train, validation = window.split(finished)
        windows[role] = {
            "rule": window.describe(),
            "train": rows_summary(train),
            "validation": rows_summary(validation),
        }
    windows["out_of_time"] = {
        "rule": f"finished loans issued {out_of_time_years[0]}-{out_of_time_years[-1]}; seen by neither model",
        **rows_summary(out_of_time),
    }
    payload = {
        "model": {
            "algorithm": ALGORITHM,
            "code": "risk/credit.py fit_lightgbm (unchanged): class weighting, early stopping on validation AUC, "
            "Platt recalibration on the validation year",
            "features": CREDIT_FEATURES,
            "leakage_check": "passed: assert_no_leakage on the feature list (fit_lightgbm checks booster inputs too)",
        },
        "windows": windows,
        "training": {
            "champion": training_summary(champion, champion_window),
            "challenger": training_summary(challenger, challenger_window),
        },
        "comparison": comparison,
        "recommendation": recommend(comparison["paired_difference_challenger_minus_champion"], rule),
        "bootstrap": {
            "n_resamples": config.n_resamples,
            "seed": config.seed,
            "alpha": config.alpha,
            "method": "percentile; paired differences use the same resampled rows for both models",
        },
        "limitations": LIMITATIONS,
    }
    return ChampionChallengerRun(
        champion=champion,
        challenger=challenger,
        champion_window=champion_window,
        challenger_window=challenger_window,
        out_of_time=out_of_time,
        payload=payload,
    )
