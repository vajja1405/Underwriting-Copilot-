"""The HTML monitoring report: static, strict CSP, every value escaped, small inline charts (SYNTHETIC run)."""

import base64
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from monitoring_test_helpers import run_paths, small_config, write_inputs

import monitor
from uwcopilot.governance.monitoring_config import BootstrapSettings, MonitoringConfig
from uwcopilot.governance.monitoring_report import (
    CSP,
    escape,
    fmt,
    fmt_delay,
    fmt_detail,
    fmt_interval,
    fmt_rate,
    render_monitoring_report,
    table,
)
from uwcopilot.governance.monitoring_stream import delay_summary, rate_with_ci

BOOTSTRAP = BootstrapSettings(resamples=1000, seed=42, confidence=0.95)

HOSTILE = '<script>alert("x")</script>'


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, dict[str, Any]]:
    """A report from a SYNTHETIC run whose scenario description and model id carry HTML."""
    tmp_path: Path = tmp_path_factory.mktemp("report")
    policies, files, results_dir = write_inputs(tmp_path)
    document = small_config().model_dump(mode="json")
    document["stream"]["scenarios"]["no_drift"]["description"] = f"SYNTHETIC {HOSTILE}"
    document["monitored_model"]["model_id"] = f"model-{HOSTILE}"
    paths = run_paths(tmp_path, results_dir)
    payload = monitor.run(MonitoringConfig.model_validate(document), policies, files, paths)
    return paths.report.read_text(encoding="utf-8"), payload


def test_the_report_is_static_with_a_strict_csp(report: tuple[str, dict[str, Any]]) -> None:
    page, _ = report
    assert f'<meta http-equiv="Content-Security-Policy" content="{CSP}">' in page
    assert "default-src 'none'" in CSP and "script-src" not in CSP
    assert "<script" not in page.lower() and "javascript:" not in page.lower()
    assert "SYNTHETIC" in page and "SIMULATED" in page


def test_every_value_is_escaped(report: tuple[str, dict[str, Any]]) -> None:
    page, _ = report
    assert HOSTILE not in page
    assert escape(HOSTILE) in page


def test_charts_are_small_inline_pngs(report: tuple[str, dict[str, Any]]) -> None:
    page, _ = report
    images = re.findall(r'<img alt="[^"]*" src="data:image/png;base64,([^"]+)">', page)
    assert len(images) == 3
    for encoded in images:
        png = base64.b64decode(encoded)
        assert png.startswith(b"\x89PNG") and len(png) < 60_000
    assert re.findall(r"<img[^>]+src=\"(?!data:image/png;base64,)", page) == []


def test_the_report_shows_the_payload_numbers(report: tuple[str, dict[str, Any]]) -> None:
    page, payload = report
    calibration = payload["monitored_model"]["held_out_calibration"]
    assert fmt(calibration["observed_to_predicted"]) in page
    assert payload["alerts"]["run_id"] in page
    assert "Lending Club credit model" in page and "not measured" in page


def test_formatting_helpers() -> None:
    assert fmt(None) == "not available" and fmt(0.123456) == "0.1235" and fmt(True) == "yes"
    assert fmt_interval(None) == "not available" and fmt_interval([0.1, 0.2], 2) == "[0.10, 0.20]"
    assert "&lt;b&gt;" in table(["<b>"], [["x"]])


def test_rendering_twice_gives_the_same_page(report: tuple[str, dict[str, Any]]) -> None:
    _, payload = report
    at = datetime(2026, 10, 20, tzinfo=UTC)
    assert render_monitoring_report(payload, at) == render_monitoring_report(payload, at)


def test_the_page_says_the_delay_covers_detected_streams_and_is_capped(report: tuple[str, dict[str, Any]]) -> None:
    """P6-STAT-3 on the small run (4 windows, drift from window 2, so a delay is at most 1)."""
    page, payload = report
    assert "mean delay among detected streams, windows [95% CI] (n detected; capped at 1)" in page
    assert "the delay is at most 1" in page and "cover the detected streams only" in page
    assert "delay &lt;= 0 [95% CI] (streams)" in page and "delay &lt;= 1 [95% CI] (streams)" in page
    delay = payload["validation"]["claim_rate_up_25pct"]["detection_delay_windows"]
    if delay.get("mean"):
        assert escape(fmt_delay(delay)) in page


def test_a_rate_shows_the_exact_interval_when_the_bootstrap_one_misleads() -> None:
    """P6-STAT-4: 1 of 300 and 300 of 300 show the exact interval; 150 of 300 does not need it."""
    one = fmt_rate(rate_with_ci([True] + [False] * 299, BOOTSTRAP))
    assert one == "0.0033 [0.0000, 0.0100] (1/300); exact [0.0001, 0.0184]"
    every = fmt_rate(rate_with_ci([True] * 300, BOOTSTRAP))
    assert every == "1.0000 [1.0000, 1.0000] (300/300); exact [0.9878, 1.0000]"
    assert "exact" not in fmt_rate(rate_with_ci([True, False] * 150, BOOTSTRAP))


def test_the_delay_cell_names_n_the_cap_and_a_missing_delay() -> None:
    capped = delay_summary([0, 5, 5, None, 2], max_delay=5, bootstrap=BOOTSTRAP)
    text = fmt_delay(capped)
    assert text.startswith("3.00 [") and "(4 of 5 streams detected; 2 at the maximum of 5)" in text
    assert "zero-width" not in text
    assert "zero-width bootstrap interval" in fmt_delay(delay_summary([0, 0, None], max_delay=5, bootstrap=BOOTSTRAP))
    nothing = delay_summary([None, None], max_delay=5, bootstrap=BOOTSTRAP)
    assert fmt_delay(nothing) == "not applicable (no stream detected)"


def test_indicator_details_show_the_override_reasons() -> None:
    detail = {"override_reasons": {"data_error_in_model_input": 2, "policy_exception_approved": 0}, "decisions": 65}
    expected = "override_reasons: data_error_in_model_input 2, policy_exception_approved 0; decisions: 65"
    assert fmt_detail(detail) == expected
    assert fmt_detail({}) == "-"
