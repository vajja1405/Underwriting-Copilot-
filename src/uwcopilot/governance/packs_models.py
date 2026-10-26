"""Model facts, validation tables and fairness rows for the governance packs (Phase 6 E).

Everything here reads results/risk_metrics.json (sections credit, insurance_frequency, insurance_severity and
fairness_demo, written by scripts/train_credit.py, scripts/train_insurance.py and scripts/fairness_german.py). A
missing value prints "not measured" (governance/packs.py formats).
"""

from collections.abc import Mapping
from typing import Any

from uwcopilot.governance.packs import (
    NOT_MEASURED,
    RISK,
    full_stop,
    integer,
    interval,
    model_label,
    present,
    table,
    text,
    value_in,
)
from uwcopilot.governance.packs_inventory import InventoryEntry


def _risk(sources: Mapping[str, Any], section: str, path: str) -> Any:
    """Value inside one risk_metrics.json section's results."""
    return value_in(sources, RISK, f"{section}.results.{path}")


def _insurance_version(sources: Mapping[str, Any], target: str) -> str:
    """Which insurance model is preferred and its version (registered LightGBM or a saved, unregistered GLM)."""
    section = f"insurance_{target}"
    preferred = _risk(sources, section, "preferred_model.name")
    if preferred == "lgbm":
        entry = _risk(sources, section, f"model_version.{target}_lgbm")
        if isinstance(entry, Mapping):
            return f"{text(entry.get('registered_model'))} v{text(entry.get('version'))} (MLflow registry)"
        return NOT_MEASURED
    if preferred == "glm":
        run_id = text(_risk(sources, section, f"mlflow.run_ids.{target}"))
        return f"GLM saved in models/insurance/ (not registered), MLflow run `{run_id}`"
    return NOT_MEASURED


def model_facts(sources: Mapping[str, Any], entry: InventoryEntry) -> list[list[str]]:
    """Inventory rows read from results/risk_metrics.json: data, model in use, version and results status."""
    section = entry.results_section
    if entry.key == "credit_pd":
        chosen = _risk(sources, section, "model_selection.chosen")
        rule = text(_risk(sources, section, "model_selection.rule"))
        model = f"{model_label(chosen) if present(chosen) else NOT_MEASURED}: {rule}"
        registered = _risk(sources, section, "mlflow.registered_model")
        version = (
            f"{text(registered)} v{text(_risk(sources, section, 'mlflow.registered_version'))} (MLflow registry, local "
            "to the training machine)"
            if present(registered)
            else NOT_MEASURED
        )
        data = text(_risk(sources, section, "dataset"))
    elif entry.key in ("insurance_frequency", "insurance_severity"):
        target = entry.key.removeprefix("insurance_")
        preferred = _risk(sources, section, "preferred_model.name")
        rule = text(_risk(sources, section, "preferred_model.rule"))
        model = f"{model_label(preferred) if present(preferred) else NOT_MEASURED}: {rule}"
        version = _insurance_version(sources, target)
        data = text(_risk(sources, section, "dataset"))
    else:
        names = _risk(sources, section, "models")
        model = (
            " and ".join(model_label(name) for name in names) + " (both reported; neither is used for a decision)"
            if isinstance(names, Mapping) and names
            else NOT_MEASURED
        )
        version = f"not registered; MLflow run `{text(_risk(sources, section, 'mlflow.run_id'))}`"
        license_text = text(_risk(sources, section, "dataset.license"))
        data = f"{text(_risk(sources, section, 'dataset.name'))} (license {license_text})"
    status = _risk(sources, section, "status")
    if not present(status):
        status = "measured" if value_in(sources, RISK, f"{section}.results") else NOT_MEASURED
    return [
        ["Data", data],
        ["Model in use and how it was chosen", model],
        ["Version", version],
        ["Results", text(status)],
    ]


CREDIT_METRICS = {
    "auc": "AUC",
    "ks": "KS",
    "gini": "Gini",
    "brier": "Brier score (lower is better)",
    "ece": "ECE (lower is better)",
}


def credit_validation(sources: Mapping[str, Any]) -> list[str]:
    """Out-of-time test metrics of both credit models and the paired AUC difference."""
    rows = [
        [
            label,
            interval(_risk(sources, "credit", f"models.logistic_scorecard.test.{key}")),
            interval(_risk(sources, "credit", f"models.lightgbm.test.{key}")),
        ]
        for key, label in CREDIT_METRICS.items()
    ]
    lines = [
        f"Split: {text(_risk(sources, 'credit', 'split.rule'))}. Test rows: "
        f"{integer(_risk(sources, 'credit', 'split.test.n_rows'))}. Metric [95% bootstrap CI]:",
        "",
        *table(["Metric (test, out of time)", "Logistic scorecard", "LightGBM"], rows),
        "",
        f"LightGBM minus scorecard, test AUC (paired bootstrap): "
        f"{interval(_risk(sources, 'credit', 'comparison.test_auc_difference'))}.",
    ]
    return lines


INSURANCE_METRICS = {
    "frequency": (
        ("mean_poisson_deviance", "Mean Poisson deviance (lower is better)"),
        ("lorenz_gini", "Lorenz Gini"),
        ("observed_to_predicted_claims", "Observed / predicted claims"),
    ),
    "severity": (
        ("mean_gamma_deviance", "Mean Gamma deviance (lower is better)"),
        ("lorenz_gini", "Lorenz Gini"),
        ("observed_to_predicted_amount", "Observed / predicted amount"),
    ),
}


def insurance_validation(sources: Mapping[str, Any], target: str) -> list[str]:
    """Test metrics of the GLM and LightGBM for one insurance target, the paired differences and the time test."""
    section = f"insurance_{target}"
    rows = []
    for key, label in INSURANCE_METRICS[target]:
        rows.append(
            [
                label,
                interval(_risk(sources, section, f"test_metrics.glm.{key}")),
                interval(_risk(sources, section, f"test_metrics.lgbm.{key}")),
                interval(_risk(sources, section, f"test_metrics.lgbm_minus_glm.{key}")),
            ]
        )
    return [
        f"Split: {text(_risk(sources, section, 'split.method'))}. Metric [95% bootstrap CI]:",
        "",
        *table(["Metric (test split)", "GLM", "LightGBM", "LightGBM minus GLM (paired)"], rows),
        "",
        full_stop(f"Out-of-time test: {text(_risk(sources, section, 'out_of_time_test'))}"),
    ]


def fairness_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Adverse impact ratio and equal-opportunity rows of the German Credit demonstration."""
    rows = _risk(sources, "fairness_demo", "summary_table.fairness")
    if not isinstance(rows, list) or not rows:
        return [[NOT_MEASURED] * 6]
    result = []
    for row in rows:
        air = {
            "estimate": row.get("adverse_impact_ratio"),
            "ci_low": row.get("air_ci_low"),
            "ci_high": row.get("air_ci_high"),
        }
        eod = {
            "estimate": row.get("equal_opportunity_difference"),
            "ci_low": row.get("eod_ci_low"),
            "ci_high": row.get("eod_ci_high"),
        }
        result.append(
            [
                model_label(row.get("model")),
                f"{text(row.get('comparison'))} ({text(row.get('role'))})",
                f"{integer(row.get('n_privileged'))} / {integer(row.get('n_unprivileged'))}",
                interval(air, 3),
                text(row.get("air_ci_contains_0_8")),
                interval(eod, 3),
            ]
        )
    return result


FAIRNESS_HEADER = [
    "Model",
    "Comparison (role)",
    "n privileged / unprivileged",
    "Adverse impact ratio [95% CI]",
    "AIR CI contains 0.8",
    "Equal-opportunity difference [95% CI]",
]


def fairness_validation(sources: Mapping[str, Any]) -> list[str]:
    """Test AUC of the two demonstration models (the AIR table is in the fairness sections)."""
    rows = [
        [model_label(name), interval(_risk(sources, "fairness_demo", f"models.{name}.test_auc"))]
        for name in ("logistic_regression", "lightgbm")
    ]
    return [
        f"Split: {text(_risk(sources, 'fairness_demo', 'split.method'))}.",
        "",
        *table(["Model", "Test AUC [95% CI]"], rows),
    ]
