"""Tests for governance/feedback.py: the fixed override-reason list and the override statistics (offline)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from uwcopilot.governance.feedback import (
    FEEDBACK_VERSION,
    NOT_MEASURED,
    OVERRIDE_REASONS_FILE,
    feedback_from_record,
    load_override_reasons,
    override_rate_interval,
    override_reason_codes,
    review_action_for,
    summarize_feedback,
)

CODES = (
    "documented_compensating_factor",
    "data_error_in_model_input",
    "information_not_available_to_model",
    "policy_exception_approved",
    "referral_resolved_in_writing",
)


def record(
    record_id: str,
    action: str,
    review_action: str | None,
    role: str = "underwriter",
    codes: list[str] | None = None,
    line: str = "credit",
    recommendation: str = "approve",
    **reviewer_extra: object,
) -> dict:
    """A minimal agent decision record. review_action None builds a pre-Phase-6 (legacy) reviewer block."""
    reviewer: dict = {"action": action, "role": role, "rationale": "Reviewed the full file.", **reviewer_extra}
    if review_action is not None:
        reviewer |= {"review_action": review_action, "reason_codes": codes or [], "feedback_version": FEEDBACK_VERSION}
    return {
        "record_id": record_id,
        "application_id": f"APP-{record_id}",
        "policy_result": {"line": line},
        "reference_recommendation": recommendation,
        "reviewer": reviewer,
        "reviewer_label": "SIMULATED",
    }


def test_the_committed_list_holds_the_five_approved_codes() -> None:
    reasons = load_override_reasons()
    assert reasons.version == "override-reasons-v1"
    assert override_reason_codes() == CODES
    assert all(reason.label and len(reason.description) >= 10 for reason in reasons.reasons)
    assert OVERRIDE_REASONS_FILE.name == "override_reasons.yaml"


def test_a_list_with_a_duplicate_or_bad_code_is_refused(tmp_path: Path) -> None:
    entry = "  - code: {code}\n    label: Some label\n    description: A description of the reason.\n"
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("version: v\nreasons:\n" + entry.format(code="same_code") * 2, encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate"):
        load_override_reasons(duplicate)
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: v\nreasons:\n" + entry.format(code="Has Spaces"), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_override_reasons(bad)
    empty = tmp_path / "empty.yaml"
    empty.write_text("version: v\nreasons: []\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_override_reasons(empty)


def test_review_action_follows_the_override_flag() -> None:
    assert review_action_for(True) == "override" and review_action_for(False) == "accept"


def test_new_and_legacy_records_give_the_same_feedback() -> None:
    new = feedback_from_record(record("1", "decline", "override", codes=["data_error_in_model_input"]))
    legacy = feedback_from_record(
        record("1", "decline", None, is_override=True, override_reason="data_error_in_model_input")
    )
    assert new is not None and legacy is not None
    assert legacy.legacy and not new.legacy
    assert (new.review_action, new.reason_codes) == (legacy.review_action, legacy.reason_codes)
    plain_legacy = feedback_from_record(record("2", "approve", None, is_override=False, override_reason=None))
    assert plain_legacy is not None and plain_legacy.review_action == "accept" and plain_legacy.reason_codes == ()
    assert feedback_from_record({"record_id": "evidence-engine-record"}) is None


def test_an_unknown_code_in_an_old_record_is_kept_and_reported() -> None:
    item = feedback_from_record(record("1", "approve", "override", codes=["retired_code"]))
    assert item is not None and item.unknown_codes == ("retired_code",)


def test_summary_counts_reasons_roles_and_actions() -> None:
    records = [
        record("1", "approve", "accept"),
        record("2", "approve", "accept", role="senior_underwriter"),
        record("3", "decline", "override", role="senior_underwriter", codes=list(CODES[:2]), reason_text="Note."),
        record("4", "approve", "override", role="senior_underwriter", codes=[CODES[0]], recommendation="refer"),
        record("5", "decline", None, role="senior_underwriter", is_override=True, override_reason=CODES[3]),
        {"record_id": "no-reviewer"},
    ]
    summary = summarize_feedback(records)
    assert summary["records_read"] == 6 and summary["records_without_reviewer"] == 1
    assert summary["decisions"] == 5 and summary["overrides"] == 3 and summary["accepts"] == 2
    assert summary["reasons_on_overrides"] == {CODES[0]: 2, CODES[1]: 1, CODES[2]: 0, CODES[3]: 1, CODES[4]: 0}
    assert summary["overrides_with_two_or_more_reasons"] == 1 and summary["overrides_with_reason_text"] == 1
    assert summary["by_reviewer_role"]["underwriter"] == {"decisions": 1, "overrides": 0, "override_rate": 0.0}
    assert summary["by_reviewer_role"]["senior_underwriter"] == {"decisions": 4, "overrides": 3, "override_rate": 0.75}
    assert summary["by_action"] == {"approve": 3, "decline": 2}
    assert summary["by_reference_recommendation"]["refer"]["overrides"] == 1
    assert summary["by_reviewer_label"] == {"SIMULATED": 5}
    assert summary["legacy_records_mapped"] == 1 and summary["overrides_without_reason"] == 0
    rate = summary["override_rate"]
    assert rate["value"] == 0.6 and rate["k"] == 3 and rate["n"] == 5 and rate["unit"] == "decision"
    assert rate["n_resamples"] == 1000 and rate["seed"] == 42 and rate["ci_low"] <= 0.6 <= rate["ci_high"]


def test_the_interval_is_reproducible_and_empty_logs_are_not_measured() -> None:
    indicators = [1, 0, 0, 1, 0, 0, 0, 1, 0, 0]
    assert override_rate_interval(indicators) == override_rate_interval(indicators)
    assert override_rate_interval([])["status"] == NOT_MEASURED
    assert summarize_feedback([])["override_rate"]["status"] == NOT_MEASURED
    no_overrides = override_rate_interval([0, 0, 0])
    assert no_overrides["value"] == 0.0 and no_overrides["ci_high"] == 0.0
