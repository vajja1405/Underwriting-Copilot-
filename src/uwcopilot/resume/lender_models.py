"""Lender Desk bullets for the risk models and sanctions screening (Prompt 8 templates 2, 3 and 5).

Every number comes from results/risk_metrics.json or results/kyc_metrics.json. Honest changes to the templates:
- credit "stress testing" is not quoted: risk_metrics.json has no credit stress-test result, so the measured
  one-feature scenario sanity checks are named instead;
- insurance "expected-loss stress tests" are not quoted for the same reason; the measured observed/predicted
  expected-loss ratio on the test split is quoted instead.
"""

from pathlib import Path
from typing import Any

from uwcopilot.resume.results import ResultsFolder, dig, estimate_at, missing_reason, number_at
from uwcopilot.resume.text import LENDER, Bullet, BulletDraft, Omission, count, interval, years_text

RISK = "risk_metrics.json"
KYC = "kyc_metrics.json"
CREDIT_MODEL_CARD = Path("docs/model_cards/credit_model_card.md")
SR_26_2_FOOTNOTE = (
    "SR 26-2 = Revised Guidance on Model Risk Management (Federal Reserve SR 26-2, OCC Bulletin 2026-13, "
    "FDIC FIL-15-2026), April 17, 2026. The model card follows its structure only; applicability depends on the "
    "institution and its risk profile, and this is a demonstration, not a compliance claim."
)
MODEL_NAMES: dict[str, str] = {"lgbm": "LightGBM", "glm": "Poisson GLM", "lightgbm": "LightGBM"}

Built = tuple[Bullet | None, list[Omission]]


def out_of_time_text(credit: dict[str, Any] | None) -> str | None:
    """'out-of-time test (train 2007-2015, validation 2016, test 2017-2018)' from the split's issue years."""
    years = [dig(credit, "split", part, "issue_years") for part in ("train", "validation", "test")]
    if not all(isinstance(item, list) and item for item in years):
        return None
    train, validation, test = (years_text(item) for item in years)
    return f"out-of-time test (train {train}, validation {validation}, test {test})"


def credit_bullet(results: ResultsFolder, repo_root: Path) -> Built:
    """Credit default model on Lending Club: AUC vs the logistic baseline, KS, PSI, SHAP, scenario checks, card."""
    draft = BulletDraft("credit_model", LENDER, "Credit default model on Lending Club loans (public Kaggle data)")
    risk = results.load(RISK)
    credit = dig(risk, "credit", "results")
    lightgbm = estimate_at(credit, "models", "lightgbm", "test", "auc")
    logistic = estimate_at(credit, "models", "logistic_scorecard", "test", "auc")
    difference = estimate_at(credit, "comparison", "test_auc_difference")
    if lightgbm is None or logistic is None or difference is None:
        draft.omit("test AUC vs the logistic baseline", missing_reason(risk, RISK, "credit", "results"), core=True)
        return draft.finish()
    split = out_of_time_text(credit)
    if split is None:
        draft.omit("out-of-time split", missing_reason(risk, RISK, "credit", "results", "split"))
    else:
        draft.add(split, RISK)
    draft.add(
        f"LightGBM test AUC {interval(lightgbm)} vs {interval(logistic)} for a logistic scorecard baseline "
        f"(paired bootstrap difference {interval(difference, with_sign=True)})",
        RISK,
    )
    ks = estimate_at(credit, "models", "lightgbm", "test", "ks")
    if ks is None:
        draft.omit("KS", missing_reason(risk, RISK, "credit", "results", "models", "lightgbm", "test", "ks"))
    else:
        draft.add(f"KS {interval(ks)}", RISK)
    add_credit_context(draft, credit, repo_root)
    return draft.finish()


def add_credit_context(draft: BulletDraft, credit: dict[str, Any] | None, repo_root: Path) -> None:
    """PSI, SHAP, scenario checks and the model card: named only when the results (or the card file) exist."""
    if dig(credit, "stability", "score_psi_by_vintage"):
        draft.add_context("PSI drift checks by issue year")
    else:
        draft.omit("PSI drift monitoring", "not measured: no credit.results.stability.score_psi_by_vintage")
    if dig(credit, "explainability", "reason_codes"):
        draft.add_context("SHAP reason codes")
    else:
        draft.omit("SHAP reason codes", "not measured: no credit.results.explainability.reason_codes")
    if dig(credit, "scenario_sanity_checks", "checks"):
        draft.add_context("one-feature scenario sanity checks")
    draft.omit(
        "stress testing",
        f"not measured: results/{RISK} has no credit stress-test result (the scoring API's what-if scenarios are "
        "not evaluated there); the measured one-feature scenario sanity checks are named instead",
    )
    if (repo_root / CREDIT_MODEL_CARD).exists():
        draft.add_context("a model card aligned to the structure of SR 26-2 (April 17, 2026; see footnote)")
    else:
        draft.omit("model card", f"{CREDIT_MODEL_CARD} does not exist")


def insurance_bullet(results: ResultsFolder) -> Built:
    """freMTPL2 frequency-severity models: Poisson deviance and Gini vs the other model, expected-loss ratio."""
    draft = BulletDraft(
        "insurance_models", LENDER, "Insurance frequency-severity models on freMTPL2 (public French motor data)"
    )
    risk = results.load(RISK)
    frequency = dig(risk, "insurance_frequency", "results")
    chosen = dig(frequency, "preferred_model", "name")
    other = "glm" if chosen == "lgbm" else "lgbm"
    deviance = estimate_at(frequency, "test_metrics", chosen or "", "mean_poisson_deviance")
    baseline = estimate_at(frequency, "test_metrics", other, "mean_poisson_deviance")
    if chosen not in MODEL_NAMES or deviance is None or baseline is None:
        reason = missing_reason(risk, RISK, "insurance_frequency", "results", "test_metrics")
        draft.omit("frequency Poisson deviance", reason, core=True)
        return draft.finish()
    draft.add(
        f"{MODEL_NAMES[chosen]} claim-frequency model (chosen on validation) with test mean Poisson deviance "
        f"{interval(deviance)} vs {interval(baseline)} for a {MODEL_NAMES[other]}",
        RISK,
    )
    gini = estimate_at(frequency, "test_metrics", chosen, "lorenz_gini")
    baseline_gini = estimate_at(frequency, "test_metrics", other, "lorenz_gini")
    if gini is None or baseline_gini is None:
        draft.omit("Gini", missing_reason(risk, RISK, "insurance_frequency", "results", "test_metrics"))
    else:
        draft.add(f"Lorenz Gini {interval(gini)} vs {interval(baseline_gini)}", RISK)
    ratio = estimate_at(risk, "insurance_severity", "results", "expected_loss", "served_pair", "observed_to_predicted")
    if ratio is None:
        draft.omit("expected loss", missing_reason(risk, RISK, "insurance_severity", "results", "expected_loss"))
    else:
        draft.add(f"expected loss (frequency x severity) observed/predicted {interval(ratio)} on the test split", RISK)
    draft.omit(
        "expected-loss stress tests under frequency and severity shocks",
        f"not measured: results/{RISK} has no insurance stress-test result",
    )
    return draft.finish()


def kyc_bullet(results: ResultsFolder) -> Built:
    """Sanctions screening: recall of true matches at the selected alert budget, and the clean false-positive rate."""
    draft = BulletDraft(
        "kyc_screening",
        LENDER,
        "Sanctions screening against the OFAC SDN list and OpenSanctions (CC BY-NC 4.0) for SYNTHETIC customers",
    )
    kyc = results.load(KYC)
    selected = dig(kyc, "selected_operating_point")
    budget = number_at(selected, "alert_budget_per_1000")
    method = dig(selected, "method")
    point = dig(kyc, "operating_points", f"{budget:g}" if budget is not None else "", str(method), "test")
    recall = estimate_at(point, "recall")
    alerts = estimate_at(point, "alerts_per_1000")
    if budget is None or not isinstance(method, str) or recall is None or alerts is None:
        draft.omit("recall at alerts per 1,000", missing_reason(kyc, KYC, "selected_operating_point"), core=True)
        return draft.finish()
    matches = number_at(kyc, "data", "counts_by_label_and_split", "true_match", "test")
    matches_text = f", {count(matches)} test true matches" if matches is not None else ""
    draft.add(
        f"recall of true matches {interval(recall)} at {interval(alerts, decimals=1)} alerts per 1,000 screened "
        f"({method.replace('_', '-')} matcher, threshold set on validation for a budget of {count(budget)} alerts "
        f"per 1,000{matches_text})",
        KYC,
    )
    clean = estimate_at(point, "clean_false_positive_rate")
    if clean is None:
        draft.omit("clean false-positive rate", missing_reason(kyc, KYC, "operating_points"))
    else:
        draft.add(f"false-positive rate on clean customers {interval(clean)}", KYC)
    return draft.finish()


def credit_footnotes(bullet: Bullet | None) -> list[str]:
    """The SR 26-2 footnote, when the credit bullet names the model card."""
    if bullet is not None and "SR 26-2" in bullet.text:
        return [SR_26_2_FOOTNOTE]
    return []
