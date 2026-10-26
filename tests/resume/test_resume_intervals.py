"""Tests for the interval a resume bullet quotes when the bootstrap interval has zero width or is flagged too narrow.

The policy is the README headline table's (governance/readme_headlines.py interval_text): the bootstrap interval,
unless it is zero-width or flagged; then the exact (Clopper-Pearson) interval, else the Wilson interval; with neither,
the value is printed with a note and no brackets.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from resume_fixtures import fake_repo, write_results

from uwcopilot.governance.readme_headlines import interval_text
from uwcopilot.resume.report import INTERVAL_NOTE, build_report
from uwcopilot.resume.results import BOOTSTRAP, EXACT, NARROW_BOOTSTRAP, WILSON, Estimate, read_estimate
from uwcopilot.resume.text import FLAGGED_NARROW_NOTE, ZERO_WIDTH_NOTE, interval

# A zero-width bootstrap interval of a 100% rate, as Phase 2 writes it (evaluation_intervals.add_exact_interval).
ZERO_WIDTH_WITH_EXACT = {
    "value": 1.0,
    "ci_low": 1.0,
    "ci_high": 1.0,
    "n_packets": 60,
    "exact_ci_low": 0.9889,
    "exact_ci_high": 1.0,
    "bootstrap_degenerate": True,
}
# A monitoring rate of 1 of 300 streams: the bootstrap interval is not zero-width but the file flags it as too narrow.
FLAGGED_WITH_EXACT = {
    "estimate": 0.0033,
    "ci_low": 0.0,
    "ci_high": 0.01,
    "numerator": 1,
    "denominator": 300,
    "exact_ci_low": 0.0001,
    "exact_ci_high": 0.0184,
    "bootstrap_interval_note": "1 of 300 streams: the bootstrap interval is too narrow; read the exact interval",
}
# A zero-width bootstrap interval next to a Wilson interval, as studio_handoff_metrics.json writes it.
ZERO_WIDTH_WITH_WILSON = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 71, "k": 71, "wilson_ci95": [0.9487, 1.0]}
# A zero-width bootstrap interval with no other interval, as agent_metrics.json writes reached_human_checkpoint.
ZERO_WIDTH_ALONE = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 65}
# A bootstrap interval flagged as degenerate although its width is not zero, with no other interval.
FLAGGED_ALONE = {"value": 0.9967, "ci_low": 0.99, "ci_high": 1.0, "bootstrap_degenerate": True}
# An informative bootstrap interval next to an exact one: the bootstrap interval is quoted.
INFORMATIVE_WITH_EXACT = {
    "value": 0.9946,
    "ci_low": 0.9906,
    "ci_high": 0.9977,
    "exact_ci_low": 0.9938,
    "exact_ci_high": 0.9954,
    "bootstrap_degenerate": False,
}


def checked(entry: dict[str, Any]) -> Estimate:
    """read_estimate() of entry, which must not be None."""
    estimate = read_estimate(entry)
    assert estimate is not None
    return estimate


def test_zero_width_bootstrap_falls_back_to_the_exact_interval() -> None:
    estimate = checked(ZERO_WIDTH_WITH_EXACT)
    assert (estimate.method, estimate.low, estimate.high, estimate.n) == (EXACT, 0.9889, 1.0, 60)
    assert interval(estimate) == "1.0000 [exact 0.9889, 1.0000]"


def test_flagged_bootstrap_falls_back_to_the_exact_interval() -> None:
    estimate = checked(FLAGGED_WITH_EXACT)
    assert (estimate.method, estimate.low, estimate.high) == (EXACT, 0.0001, 0.0184)
    assert interval(estimate) == "0.0033 [exact 0.0001, 0.0184]"


def test_zero_width_bootstrap_falls_back_to_the_wilson_interval() -> None:
    estimate = checked(ZERO_WIDTH_WITH_WILSON)
    assert (estimate.method, estimate.low, estimate.high) == (WILSON, 0.9487, 1.0)
    assert interval(estimate) == "1.0000 [Wilson 0.9487, 1.0000]"


def test_zero_width_bootstrap_alone_is_printed_with_a_note_not_as_an_interval() -> None:
    estimate = checked(ZERO_WIDTH_ALONE)
    assert estimate.method == NARROW_BOOTSTRAP
    text = interval(estimate)
    assert text == f"1.0000 ({ZERO_WIDTH_NOTE})"
    assert "[" not in text
    assert interval(checked({"mean_difference": 0.0, "ci_low": 0.0, "ci_high": 0.0}), with_sign=True) == (
        f"+0.0000 ({ZERO_WIDTH_NOTE})"
    )


def test_flagged_bootstrap_alone_is_printed_with_a_note_not_as_an_interval() -> None:
    estimate = checked(FLAGGED_ALONE)
    assert estimate.method == NARROW_BOOTSTRAP
    assert interval(estimate) == f"0.9967 ({FLAGGED_NARROW_NOTE})"


def test_informative_bootstrap_interval_is_still_quoted() -> None:
    estimate = checked(INFORMATIVE_WITH_EXACT)
    assert (estimate.method, estimate.low, estimate.high) == (BOOTSTRAP, 0.9906, 0.9977)
    assert interval(estimate) == "0.9946 [0.9906, 0.9977]"


def test_exact_bounds_written_as_none_are_not_an_interval() -> None:
    entry = {**ZERO_WIDTH_ALONE, "exact_ci_low": None, "exact_ci_high": None, "bootstrap_degenerate": True}
    assert checked(entry).method == NARROW_BOOTSTRAP


def test_zero_width_bootstrap_list_falls_back_like_the_pairs() -> None:
    estimate = checked({"value": 0.0, "ci95": [0.0, 0.0], "exact_ci_low": 0.0, "exact_ci_high": 0.0614})
    assert (estimate.method, estimate.high) == (EXACT, 0.0614)


@pytest.mark.parametrize(
    ("entry", "method"),
    [
        (ZERO_WIDTH_WITH_EXACT, EXACT),
        (FLAGGED_WITH_EXACT, EXACT),
        (ZERO_WIDTH_WITH_WILSON, WILSON),
        (ZERO_WIDTH_ALONE, NARROW_BOOTSTRAP),
        (INFORMATIVE_WITH_EXACT, BOOTSTRAP),
    ],
)
def test_interval_choice_matches_the_readme_headline_table(entry: dict[str, Any], method: str) -> None:
    readme = interval_text(entry)
    estimate = checked(entry)
    assert estimate.method == method
    if method == EXACT:
        assert f"exact [{estimate.low:.4f}, {estimate.high:.4f}]" in readme
    elif method == WILSON:
        assert f"[{estimate.low:.4f}, {estimate.high:.4f}]" in readme and "Wilson" in readme
    elif method == NARROW_BOOTSTRAP:
        assert "zero-width bootstrap interval, no other interval recorded" in readme
    else:
        assert f"[{estimate.low:.4f}, {estimate.high:.4f}]" in readme and "exact" not in readme


def set_recall(results: Path, entry: dict[str, Any]) -> None:
    """Replace the contradiction recall of the fixture docintel_metrics.json with entry."""
    path = results / "docintel_metrics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rules"]["contradictions"]["recall"] = entry
    path.write_text(json.dumps(data), encoding="utf-8")


def evidence_text(results: Path, repo: Path) -> str:
    """The evidence_platform bullet built from the results folder."""
    report = build_report(results, repo)
    return next(bullet.text for bullet in report.bullets if bullet.bullet_id == "evidence_platform")


def test_a_bullet_never_prints_a_zero_width_interval(tmp_path: Path) -> None:
    results = write_results(tmp_path / "results")
    repo = fake_repo(tmp_path / "repo")
    set_recall(results, {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "exact_ci_low": 0.9123, "exact_ci_high": 1.0})
    with_exact = evidence_text(results, repo)
    assert "contradiction recall 1.0000 [exact 0.9123, 1.0000]" in with_exact
    assert "[1.0000, 1.0000]" not in with_exact
    set_recall(results, {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0})
    alone = evidence_text(results, repo)
    assert f"contradiction recall 1.0000 ({ZERO_WIDTH_NOTE})" in alone
    assert "[1.0000, 1.0000]" not in alone


def test_interval_note_names_the_exact_interval_and_the_zero_width_case() -> None:
    assert "exact (Clopper-Pearson)" in INTERVAL_NOTE
    assert "zero width" in INTERVAL_NOTE
