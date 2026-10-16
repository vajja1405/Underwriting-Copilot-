"""Tests for scripts/render_reports.py: documents say "not measured" for missing results, and every number in
them comes from the results JSON (ground rule 1).

The results used here are a small hand-made JSON with distinctive values, plus a credit section produced by
running the credit pipeline on SYNTHETIC data, so no real data, saved model or committed result is needed
(one test also checks the committed results file when it exists, and skips otherwise).
"""

import copy
import difflib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

import render_reports
import train_insurance
from uwcopilot.risk import credit
from uwcopilot.risk.data.lending_club import prepare_loans, synthetic_accepted_loans
from uwcopilot.risk.results import to_jsonable

REPO = Path(render_reports.__file__).resolve().parents[1]
TEMPLATE_PATH = REPO / "docs" / "templates" / "walkthrough_phase1.md"
COMMITTED_RESULTS = REPO / "results" / "risk_metrics.json"
COMMITTED_EVIDENCE = REPO / "results" / "evidence_metrics.json"

URL = re.compile(r"https?://[^\s)\]]+")
NUMBER = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:e-?\d+)?%?")

# Decimals allowed in the document templates themselves (everything else must come from the JSON):
# the champion/challenger minimum AUC gain, the PSI thresholds, the four-fifths ratio, and a CFR citation.
ALLOWED_TEMPLATE_DECIMALS = {
    str(credit.MIN_AUC_GAIN_FOR_LIGHTGBM),
    "0.10",
    "0.25",
    "0.8",
    "1002.9",
    "95%",
}
# Integers of 100 or more allowed in the templates: years, HTTP status codes, citations, the example request.
ALLOWED_TEMPLATE_LARGE_INTEGERS = {
    "2007",
    "2011",
    "2015",
    "2016",
    "2017",
    "2018",
    "2022",
    "2023",
    "2024",
    "2026",
    "256",  # SHA-256
    "422",
    "503",
    "1,000",  # the README metric name "Recall at alerts per 1,000"
    "1200",  # example request
    "8000",  # API port
}


def ci(estimate: float, low: float, high: float) -> dict[str, Any]:
    """A bootstrap result as results.py writes it."""
    return {
        "estimate": estimate,
        "ci_low": low,
        "ci_high": high,
        "n_resamples": 1000,
        "n_valid_resamples": 1000,
        "seed": 42,
        "alpha": 0.05,
        "method": "percentile",
    }


def metadata() -> dict[str, Any]:
    return {
        "git_commit": "0123456789abcdef0123456789abcdef01234567",
        "git_worktree_dirty": False,
        "generated_at_utc": "2026-01-02T03:04:05+00:00",
    }


def decile(index: int, predicted: float, observed: float, low: float, high: float) -> dict[str, Any]:
    return {
        "decile": index,
        "n_policies": 1234 + index,
        "total_weight": 600.5,
        "predicted_total": 41.7,
        "observed_total": 43.0,
        "mean_predicted_rate": predicted,
        "mean_observed_rate": observed,
        "mean_observed_rate_ci_low": low,
        "mean_observed_rate_ci_high": high,
        "observed_to_predicted": observed / predicted,
    }


def minimal_results() -> dict[str, Any]:
    """A small results file: some insurance and fairness numbers, credit not measured, the rest missing."""
    frequency = {
        "status": "measured",
        "dataset": "SYNTHETIC frequency data for a render test",
        "preferred_model": {"name": "lgbm", "rule": "lower validation deviance"},
        "test_metrics": {
            "glm": {
                "mean_poisson_deviance": ci(0.41117, 0.40113, 0.42119),
                "lorenz_gini": ci(0.27771, 0.26173, 0.29177),
                "observed_to_predicted_claims": ci(1.00731, 0.97337, 1.04139),
            },
            "lgbm": {
                "mean_poisson_deviance": ci(0.39991, 0.38993, 0.40997),
                "lorenz_gini": ci(0.33313, 0.31317, 0.35319),
                "observed_to_predicted_claims": ci(0.99871, 0.96673, 1.03077),
                "calibration_by_decile": [
                    decile(1, 0.02713, 0.02917, 0.02519, 0.03311),
                    decile(2, 0.21137, 0.17113, 0.19717, 0.20019),
                ],
            },
            "lgbm_minus_glm": {"mean_poisson_deviance": ci(-0.01126, -0.01327, -0.00921)},
        },
        "risk_bands": {
            "model": "lgbm",
            "method": "quantiles of the model's predictions on its training split",
            "score": "predicted claim frequency",
            "population": "every policy in the training split",
            "n_scores": 4567,
            "quantiles": [0.5, 0.8, 0.95],
            "cutoffs": [0.06113, 0.09587, 0.18853],
            "labels": ["low", "medium", "high", "very high"],
            "rule": "a score equal to a cut-off goes to the higher band",
        },
        "scenario_sanity_checks": {
            "checks": [
                {
                    "feature": "BonusMalus",
                    "step": 10.0,
                    "expected": "increase",
                    "glm": {"n_rows_checked": 2345, "share_violations": 0.0, "share_unchanged": 0.0031},
                    "lgbm": {"n_rows_checked": 2345, "share_violations": 0.0537, "share_unchanged": 0.0012},
                }
            ]
        },
        "stability": {"status": "not measured", "reason": "no date field in this synthetic file"},
        "out_of_time_test": {"status": "not measured", "reason": "no date field in this synthetic file"},
        "bootstrap": {"n_resamples": 1000, "seed": 42, "alpha": 0.05, "method": "percentile"},
        "model_version": {
            "frequency_lgbm": {"registered_model": "insurance_frequency_lgbm", "version": "9"},
            "label": "SYNTHETIC render-test bundle",
        },
        "limitations": ["A SYNTHETIC limitation for the render test."],
    }
    fairness_row = {
        "model": "logistic_regression",
        "comparison": "age_lt25",
        "role": "primary",
        "n_privileged": 241,
        "n_unprivileged": 59,
        "approval_rate_privileged": 0.71369,
        "approval_rate_unprivileged": 0.61017,
        "adverse_impact_ratio": 0.85494,
        "air_ci_low": 0.66850,
        "air_ci_high": 1.05062,
        "air_below_0_8": False,
        "air_ci_contains_0_8": True,
        "equal_opportunity_difference": -0.05821,
        "eod_ci_low": -0.23219,
        "eod_ci_high": 0.10117,
    }
    return {
        "insurance_frequency": {"results": frequency, "metadata": metadata()},
        "fairness_demo": {
            "results": {"summary_table": {"fairness": [fairness_row]}, "data_quality_warning": "A SYNTHETIC warning."},
            "metadata": metadata(),
        },
        "credit": {
            "results": {"status": "not measured", "reason": "SYNTHETIC reason: the credit data was not downloaded"},
            "metadata": metadata(),
        },
    }


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def synthetic_credit_section() -> dict[str, Any]:
    """A measured credit section, produced by the real pipeline on SYNTHETIC loans."""
    params = {**credit.DEFAULT_LGBM_PARAMS, "num_threads": 1, "min_data_in_leaf": 20, "num_leaves": 7}
    config = credit.CreditConfig(
        n_resamples=20,
        shap_sample_size=100,
        min_category_count=5,
        lgbm_params=params,
        max_boost_rounds=200,
        early_stopping_rounds=20,
    )
    loans = prepare_loans(synthetic_accepted_loans(n_rows=2500, seed=11))
    payload, _ = credit.run_credit_pipeline(loans, config, dataset_label="SYNTHETIC test data")
    payload["mlflow"] = {"registered_model": "uwcopilot_credit_pd", "registered_version": "4", "run_id": "synthetic"}
    return {"results": json.loads(json.dumps(to_jsonable(payload))), "metadata": metadata()}


# ---------------------------------------------------------------------------
# Helpers: numbers in a text, and every way a JSON number can be displayed
# ---------------------------------------------------------------------------


def numbers_in(text: str) -> set[str]:
    """Number tokens in a document, ignoring URLs."""
    return set(NUMBER.findall(URL.sub("", text)))


def json_leaves(value: Any) -> tuple[list[float], list[str]]:
    """All numeric and string leaves of a JSON value."""
    numbers: list[float] = []
    strings: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            more_numbers, more_strings = json_leaves(item)
            numbers += more_numbers
            strings += more_strings
    elif isinstance(value, list):
        for item in value:
            more_numbers, more_strings = json_leaves(item)
            numbers += more_numbers
            strings += more_strings
    elif isinstance(value, bool):
        pass
    elif isinstance(value, int | float):
        if math.isfinite(value):
            numbers.append(float(value))
    elif isinstance(value, str):
        strings.append(value)
    return numbers, strings


def displayed_forms(numbers: list[float]) -> set[str]:
    """Every string the renderer can turn a JSON number into (fixed decimals, thousands, percent, scientific)."""
    forms: set[str] = set()
    for value in numbers:
        forms.add(f"{round(value):,}")
        forms.add(f"{value:.1e}")
        for digits in range(5):
            forms.add(f"{value:.{digits}f}")
            forms.add(f"{value:,.{digits}f}")
            forms.add(f"{100 * value:.{digits}f}%")
            forms.add(f"{100 * value:.{digits}f}")
    return forms


def without_json_strings(text: str, strings: list[str]) -> str:
    """Remove long JSON strings (reasons, hashes, timestamps) so digits inside them are not counted."""
    for string in sorted((s for s in strings if len(s) >= 6), key=len, reverse=True):
        text = text.replace(string, " ")
    return text


def untraceable_numbers(documents: dict[str, str], data: dict[str, Any], template_numbers: set[str]) -> set[str]:
    """Numbers in the documents that are neither template constants nor a displayed JSON value."""
    numbers, strings = json_leaves(data)
    allowed = displayed_forms(numbers) | template_numbers
    found: set[str] = set()
    for body in documents.values():
        found |= numbers_in(without_json_strings(body, strings))
    return {token for token in found if token not in allowed}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_results_render_not_measured_everywhere(template: str) -> None:
    documents = render_reports.render_all({}, template)
    readme = documents["README results block"]
    value_column = [line.rsplit("|", 2)[1].strip() for line in readme.splitlines() if line.startswith("| ")][1:]
    assert value_column and all(value.startswith("not measured") for value in value_column)
    for name in ("credit_model_card.md", "insurance_model_card.md", "validation_report.md", "walkthrough_phase1.md"):
        assert "not measured" in documents[name]
    assert "{{" not in documents["walkthrough_phase1.md"]
    assert "template-only" not in documents["walkthrough_phase1.md"]


def test_template_text_holds_no_metric(template: str) -> None:
    """With no results, the only numbers left are section numbers, years, citations and named design constants."""
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    decimals = {token for token in template_numbers if "." in token or "%" in token}
    assert decimals <= ALLOWED_TEMPLATE_DECIMALS
    integers = {token for token in template_numbers if "." not in token and "%" not in token}
    large = {token for token in integers if float(token.replace(",", "")) >= 100}
    assert large <= ALLOWED_TEMPLATE_LARGE_INTEGERS


def test_minimal_results_render_values_and_not_measured(template: str) -> None:
    data = minimal_results()
    documents = render_reports.render_all(data, template)
    readme = documents["README results block"]
    assert "0.3999 [0.3899, 0.4100]" in readme  # LightGBM deviance, 4 decimals
    assert "-0.0113 [-0.0133, -0.0092]" in readme  # paired difference
    assert "not measured (SYNTHETIC reason: the credit data was not downloaded)" in readme
    severity_row = next(line for line in readme.splitlines() if "Insurance severity, LightGBM minus GLM" in line)
    assert severity_row.endswith("| not measured |")
    card = documents["insurance_model_card.md"]
    assert "| low | below the 50th percentile | < 0.0611 |" in card
    assert "not measured (no date field in this synthetic file)" in card
    assert "| BonusMalus + 10 | increase | LightGBM | 2,345 | 5.37% | 0.12% |" in card
    # Decile 2: the prediction lies outside the observed interval, so the report flags it.
    assert "| 2 | 1,236 | 0.2114 | 0.1711 [0.1972, 0.2002] | 0.810 | no |" in card
    report = documents["validation_report.md"]
    assert "Frequency calibration misses in some deciles (LightGBM)" in report
    assert "Credit model not measured" in report
    assert "SR 11-7-style" not in documents["credit_model_card.md"]
    assert "aligned to the structure of sr 26-2" in documents["credit_model_card.md"].lower()


def test_every_number_comes_from_the_json(template: str, synthetic_credit_section: dict[str, Any]) -> None:
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    for data in (minimal_results(), {**minimal_results(), "credit": synthetic_credit_section}):
        documents = render_reports.render_all(data, template)
        assert untraceable_numbers(documents, data, template_numbers) == set()


def test_check_would_catch_a_typed_number(template: str) -> None:
    """The traceability check itself works: a number not in the JSON is reported."""
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    data = minimal_results()
    documents = render_reports.render_all(data, template)
    documents["README results block"] += "\n| Invented | AUC | 0.9876 |"
    assert untraceable_numbers(documents, data, template_numbers) == {"0.9876"}


def test_measured_credit_section_fills_the_credit_card(template: str, synthetic_credit_section: dict[str, Any]) -> None:
    data = {**minimal_results(), "credit": synthetic_credit_section}
    documents = render_reports.render_all(data, template)
    card = documents["credit_model_card.md"]
    auc = synthetic_credit_section["results"]["models"]["logistic_scorecard"]["test"]["auc"]
    assert render_reports.interval(auc) in card
    assert "uwcopilot_credit_pd v4" in card
    assert "| AUC | not measured |" not in card
    chosen = synthetic_credit_section["results"]["model_selection"]["chosen"]
    chosen_auc = synthetic_credit_section["results"]["models"][chosen]["test"]["auc"]
    assert render_reports.interval(chosen_auc) in documents["README results block"]


def test_readme_block_is_replaced_and_the_rest_kept() -> None:
    readme = "intro\n<!-- RESULTS:START -->\nold table\n<!-- RESULTS:END -->\noutro\n"
    updated = render_reports.replace_readme_block(readme, "new table")
    assert updated == "intro\n<!-- RESULTS:START -->\nnew table\n<!-- RESULTS:END -->\noutro\n"
    with pytest.raises(ValueError, match="RESULTS"):
        render_reports.replace_readme_block("no markers", "x")


def test_write_reports_writes_every_file(tmp_path: Path) -> None:
    results = tmp_path / "risk_metrics.json"
    results.write_text(json.dumps(minimal_results()))
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n<!-- RESULTS:START -->\n<!-- RESULTS:END -->\nrest\n")
    written = render_reports.write_reports(
        results,
        tmp_path / "model_cards",
        readme,
        walkthrough_template=TEMPLATE_PATH,
        walkthrough_path=tmp_path / "walkthrough_phase1.md",
        evidence_path=tmp_path / "no_evidence_metrics.json",
        docintel_path=tmp_path / "no_docintel_metrics.json",
    )
    assert {path.name for path in written} == {
        "credit_model_card.md",
        "insurance_model_card.md",
        "validation_report.md",
        "README.md",
        "walkthrough_phase1.md",
    }
    assert readme.read_text().endswith("<!-- RESULTS:END -->\nrest\n")


def test_lookup_and_formats() -> None:
    data = minimal_results()
    assert render_reports.lookup(data, "insurance_frequency.preferred_model.name") == "lgbm"
    assert render_reports.lookup(data, "insurance_frequency.metadata.git_commit").startswith("0123")
    assert render_reports.lookup(data, "insurance_frequency.test_metrics.lgbm.calibration_by_decile.1.decile") == 2
    assert render_reports.lookup(data, "insurance_frequency.no.such.path") is None
    assert render_reports.number(float("nan")) == "not measured"
    assert render_reports.interval({"status": "not measured", "reason": "why"}) == "not measured (why)"
    assert render_reports.percent(0.2676) == "26.8%"
    assert render_reports.amount(34328.3888) == "34,328.39"
    with pytest.raises(ValueError, match="unknown format"):
        render_reports.render_template("{{ credit.status | nope }}", data)


@pytest.mark.skipif(not COMMITTED_RESULTS.exists(), reason="no committed results file")
def test_walkthrough_placeholders_exist_in_the_committed_results(template: str) -> None:
    """Catches typos in template paths: every non-credit placeholder resolves in the real results file."""
    context = render_reports.template_context(json.loads(COMMITTED_RESULTS.read_text()))
    missing = [
        path
        for path in render_reports.placeholder_paths(template)
        if not path.startswith("credit.") and render_reports.lookup(context, path) is None
    ]
    assert missing == []


def _same_text(on_disk: str, rendered: str, label: str) -> None:
    """Assert equality, showing only the first lines of the difference when the file is stale."""
    if on_disk != rendered:
        diff = difflib.unified_diff(on_disk.splitlines(), rendered.splitlines(), "on disk", "fresh render", lineterm="")
        shown = "\n".join(list(diff)[:30])
        pytest.fail(
            f"{label} does not match a fresh render of results/risk_metrics.json (run `make reports`):\n{shown}"
        )


@pytest.mark.skipif(not COMMITTED_RESULTS.exists(), reason="no committed results file")
def test_committed_documents_equal_a_fresh_render(template: str) -> None:
    """A hand-edited or stale number in a published document fails here, not only a number missing from the JSON."""
    data = render_reports.load_report_data(COMMITTED_RESULTS, COMMITTED_EVIDENCE)
    documents = render_reports.render_all(data, template)
    for name in ("credit_model_card.md", "insurance_model_card.md", "validation_report.md"):
        on_disk = (REPO / "docs" / "model_cards" / name).read_text(encoding="utf-8")
        _same_text(on_disk, documents[name], f"docs/model_cards/{name}")
    walkthrough = (REPO / "docs" / "walkthrough_phase1.md").read_text(encoding="utf-8")
    _same_text(walkthrough, documents["walkthrough_phase1.md"], "docs/walkthrough_phase1.md")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    _same_text(readme, render_reports.replace_readme_block(readme, documents["README results block"]), "README.md")
    if "insurance_frequency" in data and "insurance_severity" in data:
        notes = (REPO / "docs" / "insurance_data_notes.md").read_text(encoding="utf-8")
        _same_text(notes, train_insurance.data_notes_text(data), "docs/insurance_data_notes.md")


def test_stale_document_check_catches_an_edited_number() -> None:
    with pytest.raises(pytest.fail.Exception, match="make reports"):
        _same_text("| LightGBM | 0.4493 [0.4395, 0.4583] |", "| LightGBM | 0.3993 [0.3895, 0.4083] |", "README.md")


# ---------------------------------------------------------------------------
# Wording that follows the intervals
# ---------------------------------------------------------------------------


def _fairness_row(model: str, comparison: str, role: str, eod: tuple[float, float, float]) -> dict[str, Any]:
    """A summary-table row whose AIR interval contains 1 and 0.8, with the given equal-opportunity interval."""
    return {
        "model": model,
        "comparison": comparison,
        "role": role,
        "n_privileged": 169,
        "n_unprivileged": 131,
        "approval_rate_privileged": 0.75148,
        "approval_rate_unprivileged": 0.61832,
        "adverse_impact_ratio": 0.82281,
        "air_ci_low": 0.69646,
        "air_ci_high": 1.05609,
        "air_ci_contains_0_8": True,
        "equal_opportunity_difference": eod[0],
        "eod_ci_low": eod[1],
        "eod_ci_high": eod[2],
    }


def test_fairness_interval_excluding_zero_is_a_finding_with_a_multiplicity_caveat() -> None:
    data = minimal_results()
    rows = data["fairness_demo"]["results"]["summary_table"]["fairness"]
    rows.append(_fairness_row("logistic_regression", "age_le30", "sensitivity", (-0.11513, -0.22494, -0.00942)))
    report = render_reports.validation_report(data)
    assert "Fairness demo: some intervals exclude no difference" in report
    assert "age_le30 (sensitivity comparison, 131 unprivileged applicants)" in report
    assert "-0.115 [-0.225, -0.009] excludes 0" in report
    assert "These come from 4 intervals" in report and "one in twenty" in report
    assert "pre-declared sensitivity comparisons" in report
    summary = "\n".join(render_reports.executive_summary(data))
    assert "1 of 2 model-comparison rows" in summary and "signal to investigate" in summary
    assert "the age comparisons do not" in summary  # the sex-coding warning is not used to cover age results
    assert "because of the data-quality and sample-size findings" not in summary
    # A flagged primary comparison is not described as a sensitivity comparison.
    rows[-1]["role"] = "primary"
    assert "pre-declared sensitivity comparisons" not in render_reports.validation_report(data)


def test_no_fairness_exclusion_no_finding() -> None:
    data = minimal_results()
    assert "some intervals exclude no difference" not in render_reports.validation_report(data)
    summary = "\n".join(render_reports.executive_summary(data))
    assert "no adverse impact ratio or equal-opportunity interval excludes" in summary


def test_dirty_worktree_is_a_finding() -> None:
    data = minimal_results()
    assert "Results were produced from uncommitted code" not in render_reports.validation_report(data)
    data["insurance_frequency"]["metadata"]["git_worktree_dirty"] = True
    report = render_reports.validation_report(data)
    assert "Results were produced from uncommitted code" in report
    assert "`insurance_frequency`" in report and "`0123456789abcdef0123456789abcdef01234567`" in report


def test_walkthrough_claims_follow_the_intervals(template: str) -> None:
    data = minimal_results()
    frequency = data["insurance_frequency"]["results"]
    frequency["test_metrics"]["lgbm_minus_glm"]["lorenz_gini"] = ci(0.05542, 0.04513, 0.06571)
    frequency["explainability"] = {"global_importance": [{"feature": "DrivAge", "mean_abs_shap": 0.31234}]}
    walkthrough = render_reports.render_template(template, data)
    assert "LightGBM ranks policies better than the GLM" in walkthrough
    assert "while both are calibrated in total" in walkthrough
    assert "DrivAge has the largest mean absolute SHAP" in walkthrough
    assert "BonusMalus has the largest" not in walkthrough

    changed = copy.deepcopy(data)
    changed_metrics = changed["insurance_frequency"]["results"]["test_metrics"]
    changed_metrics["lgbm_minus_glm"]["lorenz_gini"] = ci(0.00113, -0.00911, 0.01137)
    changed_metrics["glm"]["observed_to_predicted_claims"] = ci(1.10117, 1.05119, 1.15113)
    walkthrough = render_reports.render_template(template, changed)
    assert "LightGBM and the GLM rank policies equally well within sampling error" in walkthrough
    assert "LightGBM ranks policies better" not in walkthrough
    assert "while only LightGBM is calibrated in total" in walkthrough


@pytest.mark.parametrize(
    ("difference", "expected"),
    [
        (ci(0.04, 0.03, 0.05), "LightGBM ranks policies better than the GLM"),
        (ci(-0.04, -0.05, -0.03), "the GLM ranks policies better than LightGBM"),
        (ci(0.0, -0.01, 0.01), "rank policies equally well"),
        (None, "not measured"),
    ],
)
def test_ranking_statement(difference: Any, expected: str) -> None:
    assert expected in render_reports._ranking_statement(difference)


def test_severity_vs_frequency_and_proxy_statements() -> None:
    weak, strong = ci(0.036, 0.003, 0.07), ci(0.32, 0.31, 0.33)
    assert (
        render_reports._severity_vs_frequency(weak, strong)[1]
        == "so the pure-premium ranking comes mostly from frequency"
    )
    assert "more sharply" in render_reports._severity_vs_frequency(strong, weak)[0]
    assert "overlap" in render_reports._severity_vs_frequency(weak, weak)[0]
    assert "does not hide them" in render_reports._proxy_statement(ci(0.81, 0.75, 0.86))
    assert "not predictable" in render_reports._proxy_statement(ci(0.52, 0.45, 0.6))
    assert "not measured" in render_reports._proxy_statement(None)


def test_credit_wording_follows_the_credit_status(template: str, synthetic_credit_section: dict[str, Any]) -> None:
    not_run = render_reports.render_template(template, minimal_results())
    assert "**Credit (Lending Club): not measured.**" in not_run
    assert "(the credit model has not been run)" in not_run
    measured = render_reports.render_template(template, {**minimal_results(), "credit": synthetic_credit_section})
    assert "**Credit (Lending Club): measured**" in measured
    assert "has not been run" not in measured
    assert "gets HTTP 503 today" not in measured


@pytest.mark.skipif(not COMMITTED_RESULTS.exists(), reason="no committed results file")
def test_documents_from_the_committed_results_hold_only_json_numbers(template: str) -> None:
    """The published documents themselves: every number traces back to results/risk_metrics.json."""
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    data = render_reports.load_report_data(COMMITTED_RESULTS, COMMITTED_EVIDENCE)
    documents = render_reports.render_all(data, template)
    assert untraceable_numbers(documents, data, template_numbers) == set()


def evidence_results() -> dict[str, Any]:
    """A small evidence_metrics.json with distinctive values (packets oracle mode and the simulator headline)."""
    share = {"value": 0.98761, "ci_low": 0.97653, "ci_high": 0.99547, "numerator": 1123, "denominator": 1137}
    paired = {"mean_difference": -1.23457, "ci_low": -1.45679, "ci_high": -1.01235, "n": 345}
    return {
        "packets": {"results": {"modes": {"oracle": {"claim_status": {"accuracy": share}}}}},
        "simulator": {"results": {"headline": {"voi_minus_checklist": {"cost_weighted_requests": paired}}}},
    }


def test_evidence_rows_read_the_evidence_results(template: str) -> None:
    data = {**minimal_results(), render_reports.EVIDENCE_KEY: evidence_results()}
    readme = render_reports.render_all(data, template)["README results block"]
    claim_row = next(line for line in readme.splitlines() if "oracle extraction" in line and "Claim-status" in line)
    assert claim_row.endswith("| 0.9876 [0.9765, 0.9955] (1,123/1,137) |")
    assert "same author; not held out" in claim_row
    cost_row = next(line for line in readme.splitlines() if "test cases, documented priors" in line)
    assert cost_row.endswith("| -1.2346 [-1.4568, -1.0124] (n=345) |")
    # Rows without a result say so; the circular metric is not in the table.
    lines = readme.splitlines()
    pipeline_row = next(line for line in lines if "pipeline extraction" in line and "Claim-status" in line)
    assert pipeline_row.endswith("| not measured |")
    assert "top-3" not in readme and "not held out" in readme
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    assert untraceable_numbers({"README": readme}, data, template_numbers) == set()


def test_a_degenerate_bootstrap_share_shows_its_exact_interval() -> None:
    # 590/590: every resample gives 1.0, so the bootstrap interval [1, 1] would overstate the certainty.
    degenerate = {
        "value": 1.0,
        "ci_low": 1.0,
        "ci_high": 1.0,
        "numerator": 590,
        "denominator": 590,
        "exact_ci_low": 0.99378,
        "exact_ci_high": 1.0,
        "bootstrap_degenerate": True,
    }
    assert render_reports.share_interval(degenerate) == "1.0000 exact [0.9938, 1.0000] (590/590)"
    without_exact = {key: value for key, value in degenerate.items() if not key.startswith("exact_")}
    shown = render_reports.share_interval(without_exact)
    assert shown.startswith("1.0000 (bootstrap interval degenerate") and "[1.0000, 1.0000]" not in shown
    regular = {**degenerate, "value": 0.9, "ci_low": 0.8, "ci_high": 0.95, "bootstrap_degenerate": False}
    assert render_reports.share_interval(regular) == "0.9000 [0.8000, 0.9500] (590/590)"


def docintel_results() -> dict[str, Any]:
    """A small docintel_metrics.json with distinctive values (field F1, contradiction recall and precision)."""
    f1 = {"value": 0.97531, "ci_low": 0.96421, "ci_high": 0.98642, "bootstrap_degenerate": False}
    recall = {"value": 0.92346, "ci_low": 0.85432, "ci_high": 0.97654, "numerator": 47, "denominator": 51}
    return {"rules": {"fields": {"overall": {"f1": f1}}, "contradictions": {"recall": recall}}}


def test_document_rows_read_the_docintel_results(template: str) -> None:
    data = {**minimal_results(), render_reports.DOCINTEL_KEY: docintel_results()}
    readme = render_reports.render_all(data, template)["README results block"]
    lines = readme.splitlines()
    assert next(line for line in lines if "Field F1" in line).endswith("| 0.9753 [0.9642, 0.9864] |")
    assert next(line for line in lines if "Recall: injected" in line).endswith("| 0.9235 [0.8543, 0.9765] (47/51) |")
    assert next(line for line in lines if "Precision: contradictions" in line).endswith("| not measured |")
    assert "not built" not in readme
    # Without the file, the rows say why they are not measured.
    empty = render_reports.render_all(minimal_results(), template)["README results block"]
    assert "not measured (results/docintel_metrics.json missing)" in empty
    template_numbers = set()
    for body in render_reports.render_all({}, template).values():
        template_numbers |= numbers_in(body)
    assert untraceable_numbers({"README": readme}, data, template_numbers) == set()


def test_load_report_data_adds_the_docintel_results_when_present(tmp_path: Path) -> None:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps({"fairness_demo": {"results": {}}}), encoding="utf-8")
    missing = tmp_path / "missing.json"
    assert render_reports.DOCINTEL_KEY not in render_reports.load_report_data(risk, missing, missing)
    docintel = tmp_path / "docintel.json"
    docintel.write_text(json.dumps(docintel_results()), encoding="utf-8")
    data = render_reports.load_report_data(risk, missing, docintel)
    assert data[render_reports.DOCINTEL_KEY] == docintel_results()


def test_load_report_data_adds_the_evidence_results_when_present(tmp_path: Path) -> None:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps({"fairness_demo": {"results": {}}}), encoding="utf-8")
    assert render_reports.EVIDENCE_KEY not in render_reports.load_report_data(risk, tmp_path / "missing.json")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(evidence_results()), encoding="utf-8")
    data = render_reports.load_report_data(risk, evidence)
    assert data[render_reports.EVIDENCE_KEY] == evidence_results() and "fairness_demo" in data
