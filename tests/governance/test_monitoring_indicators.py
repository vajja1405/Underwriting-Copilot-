"""Indicators read from results files: values, 'not measured', 'not available', and no guessing (SYNTHETIC)."""

import json
from pathlib import Path
from typing import Any

import pytest
from monitoring_test_helpers import simulated_feedback_summary

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.monitoring_config import LevelRule, load_monitoring_config
from uwcopilot.governance.monitoring_indicators import (
    NO_REASONS,
    agent_indicators,
    as_number,
    extraction_indicators,
    find_key,
    override_indicator_problems,
    override_indicators,
    read_indicators,
)

NOT_MEASURED_TEXT = "not measured: no LLM provider was used"
COMMITTED_GOVERNANCE = RESULTS_DIR / "governance_metrics.json"
REASONS = [
    ["referral_resolved_in_writing"],
    ["data_error_in_model_input", "documented_compensating_factor"],
]


@pytest.fixture(scope="module")
def rules() -> dict[str, LevelRule]:
    """The committed indicator rules."""
    return load_monitoring_config().thresholds.indicators


def docintel(supported: float = 0.9984, ocr_pages: int = 159, pages: int = 949) -> dict[str, Any]:
    """The parts of a docintel_metrics.json the monitor reads (SYNTHETIC values)."""
    return {
        "rules": {
            "citations": {
                "supported_all_values": {
                    "value": supported,
                    "ci_low": supported - 0.001,
                    "ci_high": min(1.0, supported + 0.001),
                    "numerator": 36230,
                    "denominator": 36288,
                }
            },
            "latency": {"ocr_pages_total": ocr_pages},
            "page_classification": {"all": {"denominator": pages}},
        }
    }


def agent(escalated: float = 1.0) -> dict[str, Any]:
    """The parts of an agent_metrics.json the monitor reads (SYNTHETIC values)."""
    return {
        "deterministic_path": {
            "cases": 65,
            "escalation": {"all_required_escalations": {"value": escalated, "ci_low": 1.0, "ci_high": 1.0, "n": 60}},
        },
        "llm_dependent": {"unsupported_claim_rate": NOT_MEASURED_TEXT},
    }


def by_name(indicators: list[Any]) -> dict[str, Any]:
    """Indicators keyed by name."""
    return {indicator.name: indicator for indicator in indicators}


def test_extraction_indicators_read_citation_support_and_ocr_share(rules: dict[str, LevelRule]) -> None:
    found = by_name(extraction_indicators(docintel(), rules))
    citation = found["citation_support_rate"]
    assert (citation.status, citation.value, citation.level) == ("measured", 0.9984, "ok")
    assert citation.interval == pytest.approx((0.9974, 0.9994))
    assert citation.detail["citation_failures"] == 58
    ocr = found["ocr_page_share"]
    assert ocr.value == pytest.approx(159 / 949) and ocr.detail == {"ocr_pages": 159, "pages": 949}
    assert found["low_confidence_field_share"].status == "not available"


def test_a_low_citation_support_and_a_high_ocr_share_alert(rules: dict[str, LevelRule]) -> None:
    found = by_name(extraction_indicators(docintel(supported=0.95, ocr_pages=500, pages=949), rules))
    assert found["citation_support_rate"].level == "alert"
    assert found["ocr_page_share"].level == "alert"
    check = found["citation_support_rate"].as_check()
    assert (check.check, check.subject, check.level) == ("indicator", "citation_support_rate", "alert")


def test_missing_files_make_every_indicator_not_available(rules: dict[str, LevelRule]) -> None:
    indicators = extraction_indicators(None, rules) + agent_indicators(None, rules)
    assert {indicator.status for indicator in indicators} == {"not available"}
    assert all("not present" in indicator.note for indicator in indicators)
    with pytest.raises(ValueError):
        indicators[0].as_check()


def test_agent_indicators_keep_not_measured_as_not_measured(rules: dict[str, LevelRule]) -> None:
    found = by_name(agent_indicators(agent(), rules))
    assert found["required_escalations_escalated"].level == "ok"
    share = found["escalation_share"]
    assert share.value == pytest.approx(60 / 65) and share.level is None and share.rule is None
    claims = found["unsupported_claim_rate"]
    assert claims.status == "not measured" and claims.value is None and claims.note == NOT_MEASURED_TEXT
    assert by_name(agent_indicators(agent(escalated=0.98), rules))["required_escalations_escalated"].level == "alert"


def test_override_rate_is_not_available_without_a_feedback_section(rules: dict[str, LevelRule]) -> None:
    for governance in (None, {}, {"monitoring": {"results": {}}}):
        (indicator,) = override_indicators(governance, rules)
        assert indicator.status == "not available" and indicator.value is None


def test_override_rate_and_reasons_are_read_wherever_the_feedback_section_puts_them(
    rules: dict[str, LevelRule],
) -> None:
    governance = {
        "feedback": {
            "results": {
                "data_label": "SIMULATED",
                "overrides": {
                    "override_rate": {"estimate": 0.24, "ci_low": 0.18, "ci_high": 0.30},
                    "override_reasons": {"missing_document": 7, "policy_exception": 3},
                },
            },
            "metadata": {"git_commit": "abc123"},
        }
    }
    (indicator,) = override_indicators(governance, rules)
    assert (indicator.value, indicator.level, indicator.interval) == (0.24, "alert", (0.18, 0.30))
    assert indicator.detail["override_reasons"] == {"missing_document": 7, "policy_exception": 3}
    assert indicator.detail["feedback_section_git_commit"] == "abc123"


def test_override_rate_without_a_number_is_never_guessed(rules: dict[str, LevelRule]) -> None:
    text_only = {"feedback": {"results": {"override_rate": "high"}}}
    assert override_indicators(text_only, rules)[0].status == "not available"
    placeholder = {"feedback": {"results": {"override_rate": {"status": "not measured", "reason": "no reviews"}}}}
    (indicator,) = override_indicators(placeholder, rules)
    assert (indicator.status, indicator.note) == ("not measured", "no reviews")


def test_helpers_reject_non_numbers() -> None:
    assert as_number(True) is None and as_number(float("nan")) is None and as_number("0.5") is None
    assert as_number({"value": 0.5}) == 0.5 and as_number(3) == 3.0
    assert find_key({"a": [{"b": {"override_rate": 0.1}}]}, "override_rate") == 0.1
    assert find_key({"a": 1}, "override_rate") is None


def test_read_indicators_hashes_each_source_file(tmp_path: Path, rules: dict[str, LevelRule]) -> None:
    (tmp_path / "docintel_metrics.json").write_text(json.dumps(docintel()), encoding="utf-8")
    indicators, sources = read_indicators(tmp_path, rules)
    assert len(sources["results/docintel_metrics.json"]["sha256"]) == 64
    assert sources["results/agent_metrics.json"]["sha256"] is None
    statuses = {indicator.name: indicator.status for indicator in indicators}
    assert statuses["citation_support_rate"] == "measured" and statuses["override_rate"] == "not available"


# --- The feedback section as scripts/feedback_report.py writes it (P6-STAT-1, P6-STAT-2) ----------------------------


def feedback_section(summary: dict[str, Any], written_at: str = "2026-10-26T16:45:04+00:00") -> dict[str, Any]:
    """A "feedback" results section shaped like scripts/feedback_report.py writes it (SIMULATED decisions)."""
    return {
        "results": {"data_label": "SYNTHETIC", "status": "measured on a SIMULATED reviewer log", "summary": summary},
        "metadata": {"git_commit": "b" * 40, "generated_at_utc": written_at},
    }


def monitoring_section(governance_read: dict[str, Any] | None, rules: dict[str, LevelRule]) -> dict[str, Any]:
    """The indicators of a monitoring section, as scripts/monitor.py writes them after reading `governance_read`."""
    indicators = override_indicators(governance_read, rules)
    file_hash = None if governance_read is None else "c" * 64
    return {
        "results": {
            "indicators": {
                "sources": {"results/governance_metrics.json": {"sha256": file_hash}},
                "values": [indicator.to_dict() for indicator in indicators],
            }
        }
    }


def test_the_rate_and_reasons_summarize_feedback_writes_reach_the_indicator(rules: dict[str, LevelRule]) -> None:
    summary = simulated_feedback_summary(REASONS, accepts=14)  # 2 overrides in 16 decisions: 0.125, at warn level
    (indicator,) = override_indicators({"feedback": feedback_section(summary)}, rules)
    assert (indicator.status, indicator.value, indicator.level) == ("measured", 0.125, "warn")
    assert indicator.interval == (summary["override_rate"]["ci_low"], summary["override_rate"]["ci_high"])
    reasons = indicator.detail["override_reasons"]
    assert reasons == summary["reasons_on_overrides"]
    assert reasons["referral_resolved_in_writing"] == 1 and reasons["data_error_in_model_input"] == 1
    assert (indicator.detail["overrides"], indicator.detail["decisions"]) == (2, 16)
    assert indicator.detail["feedback_section_generated_at_utc"] == "2026-10-26T16:45:04+00:00"
    assert NO_REASONS not in indicator.note


def test_a_feedback_section_without_reason_counts_says_so(rules: dict[str, LevelRule]) -> None:
    summary = simulated_feedback_summary(REASONS, accepts=14)
    del summary["reasons_on_overrides"]
    (indicator,) = override_indicators({"feedback": feedback_section(summary)}, rules)
    assert indicator.value == 0.125 and indicator.detail["override_reasons"] == NO_REASONS
    assert NO_REASONS in indicator.note


def test_a_monitoring_section_written_before_the_feedback_section_is_flagged(rules: dict[str, LevelRule]) -> None:
    """The committed state of P6-STAT-1: the monitor ran before results/governance_metrics.json existed."""
    feedback = feedback_section(simulated_feedback_summary(REASONS, accepts=14))
    problems = override_indicator_problems({"monitoring": monitoring_section(None, rules), "feedback": feedback})
    assert len(problems) == 2
    assert "did not exist yet" in problems[0]
    assert "read no feedback section" in problems[1] and "'not available'" in problems[1]


def test_a_monitoring_section_that_read_the_current_feedback_section_is_consistent(
    rules: dict[str, LevelRule],
) -> None:
    governance: dict[str, Any] = {"feedback": feedback_section(simulated_feedback_summary(REASONS, accepts=14))}
    governance["monitoring"] = monitoring_section(governance, rules)
    assert override_indicator_problems(json.loads(json.dumps(governance))) == []  # as read back from the file


def test_a_feedback_section_rewritten_after_the_monitor_ran_is_flagged(rules: dict[str, LevelRule]) -> None:
    earlier = feedback_section(simulated_feedback_summary(REASONS, accepts=14), "2026-10-26T16:00:00+00:00")
    later = feedback_section(simulated_feedback_summary(REASONS[:1], accepts=14), "2026-10-26T17:00:00+00:00")
    governance = {"monitoring": monitoring_section({"feedback": earlier}, rules), "feedback": later}
    (problem,) = override_indicator_problems(governance)
    assert "written at 2026-10-26T16:00:00+00:00" in problem and "2026-10-26T17:00:00+00:00" in problem


def test_nothing_is_compared_without_both_sections(rules: dict[str, LevelRule]) -> None:
    feedback = feedback_section(simulated_feedback_summary(REASONS, accepts=14))
    assert override_indicator_problems(None) == []
    assert override_indicator_problems({"feedback": feedback}) == []
    assert override_indicator_problems({"monitoring": monitoring_section(None, rules)}) == []


@pytest.mark.skipif(not COMMITTED_GOVERNANCE.exists(), reason="no committed governance results")
def test_the_committed_monitoring_section_read_the_committed_feedback_section() -> None:
    """When this fails, regenerate with `make governance-results` (it runs feedback-report before monitor)."""
    governance = json.loads(COMMITTED_GOVERNANCE.read_text(encoding="utf-8"))
    assert override_indicator_problems(governance) == []
