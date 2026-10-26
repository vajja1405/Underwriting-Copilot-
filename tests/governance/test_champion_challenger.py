"""governance/champion_challenger.py and scripts/champion_challenger.py on SYNTHETIC Lending Club-shaped loans.

Nothing here may promote a model: the champion stays champion and the challenger stays challenger until a model
validator acts (tested in test_champion_challenger_promote.py).
"""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import champion_challenger as script
from uwcopilot.governance.champion_challenger import (
    CHALLENGER_WINDOW,
    CHAMPION_WINDOW,
    DATA_LABEL,
    FAMILY,
    OUT_OF_TIME_YEARS,
    ChampionChallengerRun,
    PromotionRule,
    TrainingWindow,
    check_windows,
    compare_models,
    load_model_bundle,
    model_fingerprint,
    model_id_for,
    recommend,
    register_model,
    run_champion_challenger,
    save_model_bundle,
)
from uwcopilot.governance.model_status import ModelEntry, ModelStatusStore
from uwcopilot.risk.credit import DEFAULT_LGBM_PARAMS, TEST_YEARS, TRAIN_LAST_YEAR, VALIDATION_YEAR, CreditConfig
from uwcopilot.risk.data.lending_club import finished_loans
from uwcopilot.risk.metrics import roc_auc
from uwcopilot.security.rbac import Principal

N_RESAMPLES = 30
VALIDATOR = Principal("validator-1", "model_validator", "claimed")


def small_config() -> CreditConfig:
    """Fast settings for a few thousand SYNTHETIC rows (one LightGBM thread, small trees, 30 resamples)."""
    params = {**DEFAULT_LGBM_PARAMS, "num_threads": 1, "min_data_in_leaf": 20, "num_leaves": 7}
    return CreditConfig(
        n_resamples=N_RESAMPLES,
        min_category_count=5,
        lgbm_params=params,
        max_boost_rounds=300,
        early_stopping_rounds=30,
    )


@pytest.fixture(scope="module")
def loans_and_info() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Prepared SYNTHETIC loans with application ids, made by the script's own helper."""
    return script.synthetic_loans(n_rows=3000, seed=11)


@pytest.fixture(scope="module")
def loans(loans_and_info: tuple[pd.DataFrame, dict[str, Any]]) -> pd.DataFrame:
    return loans_and_info[0]


@pytest.fixture(scope="module")
def comparison(loans: pd.DataFrame) -> ChampionChallengerRun:
    return run_champion_challenger(loans, small_config())


@pytest.fixture(scope="module")
def script_run(
    tmp_path_factory: pytest.TempPathFactory, loans_and_info: tuple[pd.DataFrame, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], script.RunPaths]:
    """The script's run() twice on the same data and paths (the second finds both models registered)."""
    tmp_path = tmp_path_factory.mktemp("champion_challenger")
    paths = script.RunPaths(
        results=tmp_path / "governance_metrics.json",
        registry=tmp_path / "model_status.json",
        models_dir=tmp_path / "models",
    )
    loans, info = loans_and_info
    first = script.run(loans, info, small_config(), paths)
    second = script.run(loans, info, small_config(), paths)
    return first, second, paths


# ---------------------------------------------------------------------------
# Windows and the recommendation rule
# ---------------------------------------------------------------------------


def test_the_windows_come_from_the_credit_split() -> None:
    assert (CHALLENGER_WINDOW.train_last_year, CHALLENGER_WINDOW.validation_year) == (TRAIN_LAST_YEAR, VALIDATION_YEAR)
    assert (CHAMPION_WINDOW.train_last_year, CHAMPION_WINDOW.validation_year) == (
        TRAIN_LAST_YEAR - 1,
        VALIDATION_YEAR - 1,
    )
    assert OUT_OF_TIME_YEARS == TEST_YEARS
    check_windows(CHAMPION_WINDOW, CHALLENGER_WINDOW, OUT_OF_TIME_YEARS)


@pytest.mark.parametrize(
    ("champion", "challenger", "years", "message"),
    [
        (TrainingWindow(2014, 2014), CHALLENGER_WINDOW, (2017,), "validation year must come after"),
        (CHALLENGER_WINDOW, CHAMPION_WINDOW, (2017,), "newer loans"),
        (TrainingWindow(2014, 2016), TrainingWindow(2013, 2017), (2018,), "keep at least"),
        (CHAMPION_WINDOW, CHALLENGER_WINDOW, (2016, 2017), "out-of-time years"),
        (CHAMPION_WINDOW, CHALLENGER_WINDOW, (), "out-of-time years"),
    ],
)
def test_bad_windows_are_refused(
    champion: TrainingWindow, challenger: TrainingWindow, years: tuple[int, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        check_windows(champion, challenger, years)


def test_a_window_splits_by_issue_year(loans: pd.DataFrame) -> None:
    train, validation = CHAMPION_WINDOW.split(finished_loans(loans))
    assert train["issue_year"].max() == CHAMPION_WINDOW.train_last_year
    assert set(validation["issue_year"]) == {CHAMPION_WINDOW.validation_year}


def differences(auc_low: float, ece_high: float) -> dict[str, dict[str, float]]:
    """The two paired-difference bounds the rule reads."""
    return {"auc": {"ci_low": auc_low}, "ece": {"ci_high": ece_high}}


@pytest.mark.parametrize(
    ("auc_low", "ece_high", "expected"),
    [
        (0.002, 0.0, "recommend_promotion"),
        (0.002, 0.01, "recommend_promotion"),  # exactly at the ECE margin still passes
        (0.0, 0.0, "keep_champion"),  # the AUC gain must be above 0, not equal to it
        (-0.01, -0.02, "keep_champion"),
        (0.002, 0.0101, "keep_champion"),  # calibration worse by more than the margin
        (math.nan, 0.0, "keep_champion"),  # an undefined bound never meets a condition
        (0.002, math.nan, "keep_champion"),
    ],
)
def test_the_promotion_rule(auc_low: float, ece_high: float, expected: str) -> None:
    result = recommend(differences(auc_low, ece_high), PromotionRule())
    assert result["recommendation"] == expected
    assert result["promoted_by_this_run"] is False
    assert [item["met"] for item in result["conditions"]] == [
        auc_low > 0,
        ece_high <= 0.01,
    ]


def test_paired_differences_are_challenger_minus_champion() -> None:
    rng = np.random.default_rng(3)
    y_true = (rng.random(600) < 0.3).astype(int)
    champion = np.clip(0.3 + rng.normal(0, 0.15, 600), 0.01, 0.99)
    challenger = np.clip(0.3 + 0.25 * (y_true - 0.3) + rng.normal(0, 0.1, 600), 0.01, 0.99)
    config = CreditConfig(n_resamples=N_RESAMPLES)
    result = compare_models(y_true, champion, challenger, config)
    auc = result["paired_difference_challenger_minus_champion"]["auc"]
    assert auc["estimate"] == pytest.approx(roc_auc(y_true, challenger) - roc_auc(y_true, champion))
    assert auc["estimate"] > 0 and auc["n_resamples"] == N_RESAMPLES and auc["seed"] == 42
    assert result["challenger"]["auc"]["estimate"] == pytest.approx(auc["estimate_a"])
    same = compare_models(y_true, champion, champion, config)["paired_difference_challenger_minus_champion"]
    assert all(same[name]["estimate"] == 0 for name in ("auc", "ks", "brier", "ece"))
    assert same["brier"]["direction"] == "lower is better"


# ---------------------------------------------------------------------------
# The comparison on SYNTHETIC loans
# ---------------------------------------------------------------------------


def test_both_models_are_scored_on_the_same_unseen_window(comparison: ChampionChallengerRun) -> None:
    payload = comparison.payload
    windows = payload["windows"]
    assert windows["champion"]["validation"]["issue_years"] == [CHAMPION_WINDOW.validation_year]
    assert windows["challenger"]["validation"]["issue_years"] == [CHALLENGER_WINDOW.validation_year]
    assert max(windows["challenger"]["train"]["issue_years"]) == CHALLENGER_WINDOW.train_last_year
    assert windows["out_of_time"]["issue_years"] == list(OUT_OF_TIME_YEARS)
    last_seen = max(windows["challenger"]["validation"]["issue_years"])
    assert min(windows["out_of_time"]["issue_years"]) > last_seen
    metrics = payload["comparison"]
    assert metrics["champion"]["n_rows"] == metrics["challenger"]["n_rows"] == windows["out_of_time"]["n_rows"]
    assert windows["challenger"]["train"]["n_rows"] > windows["champion"]["train"]["n_rows"]
    assert payload["recommendation"]["recommendation"] in ("recommend_promotion", "keep_champion")
    assert payload["bootstrap"]["n_resamples"] == N_RESAMPLES


def test_training_info_names_each_models_own_validation_year(comparison: ChampionChallengerRun) -> None:
    champion = comparison.payload["training"]["champion"]
    assert champion["early_stopping_metric"] == f"validation AUC ({CHAMPION_WINDOW.validation_year})"
    assert str(CHAMPION_WINDOW.validation_year) in champion["recalibration"]["method"]
    assert "2016" not in champion["recalibration"]["method"]


def test_a_saved_model_reproduces_the_evaluated_pds(tmp_path: Path, comparison: ChampionChallengerRun) -> None:
    fingerprint = model_fingerprint(comparison.champion)
    model_id = model_id_for(CHAMPION_WINDOW, fingerprint)
    assert model_id.startswith("SYNTHETIC-") and model_id.endswith(fingerprint[:10])
    files = save_model_bundle(comparison.champion, model_id, CHAMPION_WINDOW, tmp_path)
    reloaded = load_model_bundle(files["booster"].parent)
    rows = comparison.out_of_time
    np.testing.assert_allclose(reloaded.predict_pd(rows), comparison.champion.predict_pd(rows))
    assert model_fingerprint(reloaded) == fingerprint
    metadata = json.loads(files["metadata"].read_text(encoding="utf-8"))
    assert metadata["data_label"] == DATA_LABEL and "SYNTHETIC" in metadata["data_label"]
    assert model_fingerprint(comparison.challenger) != fingerprint


def test_registration_never_changes_a_registered_status(tmp_path: Path) -> None:
    store = ModelStatusStore(tmp_path / "model_status.json")
    first = register_model(store, "SYNTHETIC-champ", "champion", "v1", reason="SYNTHETIC test")
    register_model(store, "SYNTHETIC-chall", "challenger", "v2", reason="SYNTHETIC test")
    assert first["registration"] == "registered as champion by this run"
    assert (store.status("SYNTHETIC-champ"), store.status("SYNTHETIC-chall")) == ("champion", "challenger")
    store.promote("SYNTHETIC-chall", VALIDATOR, reason="SYNTHETIC test")
    again = register_model(store, "SYNTHETIC-champ", "champion", "v1", reason="SYNTHETIC test")
    assert again["registration"] == "already registered; status left unchanged"
    assert again["status_in_registry"] == "retired" and store.status("SYNTHETIC-chall") == "champion"
    with pytest.raises(ValueError, match="already has a model in use"):
        register_model(store, "SYNTHETIC-other", "champion", "v3", reason="SYNTHETIC test")


# ---------------------------------------------------------------------------
# scripts/champion_challenger.py
# ---------------------------------------------------------------------------


def test_the_script_writes_two_sections_labelled_synthetic(
    script_run: tuple[dict[str, Any], dict[str, Any], script.RunPaths],
) -> None:
    _, _, paths = script_run
    contents = json.loads(paths.results.read_text(encoding="utf-8"))
    assert list(contents) == ["champion_challenger", "outcomes"]
    for section in contents.values():
        assert section["metadata"]["git_commit"]
        assert "SYNTHETIC" in section["results"]["data_label"]
    champion_section = contents["champion_challenger"]["results"]
    assert champion_section["synthetic_data"]["label"] == "SYNTHETIC"
    assert champion_section["synthetic_data"]["prepared_loans_sha256"]
    assert "SIMULATED" in contents["outcomes"]["results"]["data_label"]
    assert "not real loan performance" in contents["outcomes"]["results"]["interpretation"]


def test_the_script_registers_both_models_and_promotes_nothing(
    script_run: tuple[dict[str, Any], dict[str, Any], script.RunPaths],
) -> None:
    first, second, paths = script_run
    store = ModelStatusStore(paths.registry)
    models = first["champion_challenger"]["registry"]["models"]
    assert store.status(models["champion"]["model_id"]) == "champion"
    assert store.status(models["challenger"]["model_id"]) == "challenger"
    assert {item.family for item in store.load().models} == {FAMILY}
    assert all(change.actor == "register" for item in store.load().models for change in item.history)
    assert first["champion_challenger"]["recommendation"]["promoted_by_this_run"] is False
    again = second["champion_challenger"]["registry"]["models"]
    assert again["champion"]["registration"] == "already registered; status left unchanged"
    assert again["challenger"]["model_id"] == models["challenger"]["model_id"]  # the fit is deterministic
    assert (paths.models_dir / models["challenger"]["model_id"] / "lightgbm.txt").is_file()


def test_outcomes_join_the_window_and_agree_with_the_champion(
    script_run: tuple[dict[str, Any], dict[str, Any], script.RunPaths],
    loans: pd.DataFrame,
) -> None:
    first, _, _ = script_run
    outcomes = first["outcomes"]
    window = loans.loc[loans["issue_year"].isin(OUT_OF_TIME_YEARS)]
    join = outcomes["summary"]["join"]
    assert join["n_decisions"] == len(window)
    assert join["n_decisions_with_outcome"] == int(window["is_finished"].sum())
    assert join["n_decisions_pending"] == int((~window["is_finished"]).sum())
    assert join["n_outcomes_without_decision"] == 0
    assert sum(outcomes["summary"]["decisions"].values()) == len(window)
    champion_id = first["champion_challenger"]["registry"]["models"]["champion"]["model_id"]
    assert outcomes["decision_model"]["model_id"] == champion_id
    champion_ece = first["champion_challenger"]["comparison"]["champion"]["ece"]
    recorded_ece = outcomes["summary"]["recorded_pd_calibration"]["ece"]
    assert recorded_ece["estimate"] == pytest.approx(champion_ece["estimate"])
    assert recorded_ece["ci_low"] == pytest.approx(champion_ece["ci_low"])


def test_main_runs_from_the_command_line_and_prints_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--n-rows", "12000", "--n-resamples", "20", "--num-threads", "1"]
    argv += ["--results-path", str(tmp_path / "results.json"), "--registry", str(tmp_path / "model_status.json")]
    assert script.main([*argv, "--models-dir", str(tmp_path / "models")]) == 0
    output = capsys.readouterr().out
    assert "nothing was promoted" in output and "registered as challenger by this run" in output
    assert "approved and defaulted:" in output
    written = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert written["champion_challenger"]["results"]["synthetic_data"]["n_rows_generated"] == 12000
    assert written["champion_challenger"]["results"]["bootstrap"]["n_resamples"] == 20


def test_a_family_with_another_model_in_use_stops_the_script(
    tmp_path: Path, loans_and_info: tuple[pd.DataFrame, dict[str, Any]]
) -> None:
    paths = script.RunPaths(tmp_path / "results.json", tmp_path / "model_status.json", tmp_path / "models")
    ModelStatusStore(paths.registry).register(
        ModelEntry(model_id="SYNTHETIC-older", family=FAMILY, version="0", status="champion")
    )
    loans, info = loans_and_info
    with pytest.raises(SystemExit, match="already holds another model in use"):
        script.run(loans, info, small_config(), paths)
    assert not paths.results.exists()
