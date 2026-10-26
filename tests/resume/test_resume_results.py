"""Tests for uwcopilot.resume.results: reading values and intervals, and the reasons for a missing value."""

import json
from pathlib import Path

from uwcopilot.resume.results import ResultsFolder, dig, estimate_at, missing_reason, number_at, read_estimate


def test_read_estimate_understands_each_results_shape() -> None:
    docintel = read_estimate({"value": 0.9412, "ci_low": 0.8723, "ci_high": 1.0, "n_packets": 60})
    assert docintel is not None
    assert (docintel.value, docintel.low, docintel.high, docintel.n) == (0.9412, 0.8723, 1.0, 60)
    risk = read_estimate({"estimate": 0.7151, "ci_low": 0.7123, "ci_high": 0.7177})
    assert risk is not None and risk.method == "bootstrap"
    kyc = read_estimate({"value": 0.6889, "ci95_low": 0.5476, "ci95_high": 0.8182})
    assert kyc is not None and kyc.low == 0.5476
    wilson = read_estimate({"successes": 16, "n": 60, "rate": 0.2667, "wilson_ci95": [0.1713, 0.3901]})
    assert wilson is not None and wilson.method == "wilson" and wilson.n == 60
    difference = read_estimate({"mean_difference": -0.6865, "ci_low": -0.7784, "ci_high": -0.5946})
    assert difference is not None and difference.value == -0.6865


def test_read_estimate_returns_none_without_a_value_or_an_interval() -> None:
    assert read_estimate("not measured (no LLM key)") is None
    assert read_estimate({"value": None, "ci_low": 0.1, "ci_high": 0.2}) is None
    assert read_estimate({"value": 0.5}) is None
    assert read_estimate({"rate": None, "wilson_ci95": None}) is None
    assert read_estimate({"value": True, "ci_low": 0.1, "ci_high": 0.2}) is None


def test_dig_and_number_at_stop_at_missing_keys() -> None:
    data = {"a": {"b": {"c": 3}}, "flag": True}
    assert dig(data, "a", "b", "c") == 3
    assert dig(data, "a", "x", "c") is None
    assert dig(None, "a") is None
    assert number_at(data, "a", "b", "c") == 3
    assert number_at(data, "flag") is None
    assert estimate_at(data, "a", "b") is None


def test_results_folder_loads_files_and_returns_none_when_absent(tmp_path: Path) -> None:
    (tmp_path / "kyc_metrics.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    results = ResultsFolder(tmp_path)
    assert results.load("kyc_metrics.json") == {"x": 1}
    assert results.load("absent.json") is None


def test_missing_reason_names_the_file_or_quotes_its_reason() -> None:
    assert missing_reason(None, "kyc_metrics.json", "x") == "not measured: results/kyc_metrics.json does not exist"
    quoted = missing_reason({"rag": "not measured (no LLM key)"}, "rag_metrics.json", "rag")
    assert quoted == "results/rag_metrics.json says: not measured (no LLM key)"
    status = missing_reason({"s": {"status": "not measured", "reason": "no data"}}, "risk_metrics.json", "s")
    assert status == "results/risk_metrics.json says: not measured (no data)"
    absent_key = missing_reason({}, "agent_metrics.json", "a", "b")
    assert absent_key == "not measured: results/agent_metrics.json has no usable a.b"
