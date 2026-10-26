"""Tests for the governance packs (src/uwcopilot/governance/packs*.py, Phase 6 E).

The packs are rendered from hand-made SYNTHETIC results with distinctive values (one per producer of
results/governance_metrics.json, with the shapes scripts/monitor.py, scripts/feedback_report.py,
scripts/champion_challenger.py and scripts/release_gate.py write), so no data, model or LLM is needed. The checks:
every number comes from the JSON (ground rule 1), a section that has not been generated says "not measured yet", the
LLM-dependent measures say "not measured", and the regulatory statements are there. One integration test checks the
quoted SR 26-2 and NY DFS wording against the local copies of the primary sources.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from test_render_reports import ci, minimal_results, numbers_in, untraceable_numbers
from uwcopilot.governance import packs
from uwcopilot.governance.packs import (
    AGENT,
    DOCINTEL,
    GOVERNANCE,
    INJECTION,
    INJECTION_DETECTOR,
    MIGRATION_CERTIFICATES,
    RISK,
)
from uwcopilot.governance.packs_bank import render_bank_model_risk, sr_26_2_rows
from uwcopilot.governance.packs_genai import genai_control_rows, third_party_model_rows
from uwcopilot.governance.packs_governance import monitoring_block
from uwcopilot.governance.packs_insurance import naic_rows, render_insurance_ai_program
from uwcopilot.governance.packs_inventory import INVENTORY, TIERING_RULE, inventory_entry, materiality_tier

REPO = Path(__file__).resolve().parents[2]
REGULATORY_DIR = REPO / "data" / "raw" / "regulatory"
GOVERNANCE_SECTIONS = ("monitoring", "outcomes", "champion_challenger", "release_gate", "lineage", "feedback")

# Numbers allowed in the packs' own text (with no results at all): dates and identifiers of the documents cited, the
# OpenML dataset ids, HTTP 403, SHA-256, the CC BY 4.0 license version, the four-fifths ratio and 95% intervals.
# Everything else must come from the JSON.
ALLOWED_PACK_DECIMALS = {"0.8", "4.0", "95%"}
ALLOWED_PACK_LARGE_INTEGERS = {"2011", "2023", "2024", "2026", "41214", "41215", "403", "256"}


def metadata(commit: str) -> dict[str, Any]:
    """update_results metadata of one section."""
    return {"git_commit": commit, "git_worktree_dirty": False, "generated_at_utc": "2026-02-03T04:05:06+00:00"}


def section(results: dict[str, Any], commit: str = "f" * 40) -> dict[str, Any]:
    """One update_results section."""
    return {"results": results, "metadata": metadata(commit)}


def rate(estimate: float, low: float, high: float, numerator: int, denominator: int) -> dict[str, Any]:
    """A rate as monitoring_stream.rate_with_ci writes it."""
    return {"estimate": estimate, "ci_low": low, "ci_high": high, "numerator": numerator, "denominator": denominator}


def reading(value: float | None) -> dict[str, Any]:
    """A release gate reading."""
    return {"state": "measured" if value is not None else "not_measured", "value": value, "detail": ""}


def governance_results() -> dict[str, Any]:
    """results/governance_metrics.json with every Phase 6 section (SYNTHETIC values)."""
    monitoring = {
        "data_label": "SYNTHETIC",
        "status": "measured on a SIMULATED stream for a render test",
        "config": {"path": "config/monitoring.yaml", "sha256": "ab" * 32},
        "monitored_model": {
            "model_id": "SYNTHETIC-monitor-glm",
            "type": "Poisson GLM of the render test",
            "held_out_calibration": {
                "observed_to_predicted": 1.03217,
                "interval": [0.98113, 1.08419],
                "interval_method": "exact Poisson interval of the render test",
            },
        },
        "stream_design": {
            "no_time_field": "SYNTHETIC: the render-test data has no date field.",
            "n_windows": 12,
            "drift_start_window": 6,
            "n_streams_per_scenario": 20,
        },
        "validation": {
            "no_drift": {"drifted": False, "n_streams": 20, "false_alert_rate": rate(0.05, 0.0, 0.15, 1, 20)},
            "claim_rate_up": {
                "drifted": True,
                "n_streams": 20,
                "detection_rate": rate(0.95, 0.85, 1.0, 19, 20),
                "detection_delay_windows": {
                    "mean": ci(1.31579, 0.89474, 1.73684),
                    "n_detected_streams": 19,
                    "streams_by_delay": {"0": 9, "1": 4, "2": 3, "5": 3},
                },
            },
            "covariate_shift": {
                "drifted": True,
                "n_streams": 20,
                "detection_rate": rate(1.0, 1.0, 1.0, 20, 20),
                "detection_delay_windows": {
                    "mean": ci(0.0, 0.0, 0.0),
                    "n_detected_streams": 20,
                    "streams_by_delay": {"0": 20},
                },
            },
            "mild_shift": {
                "drifted": True,
                "n_streams": 20,
                "detection_rate": rate(0.0, 0.0, 0.0, 0, 20),
                "detection_delay_windows": {
                    "status": "not available",
                    "reason": "no stream was detected, so there is no delay to report",
                },
            },
        },
        "demonstration_stream": {"scenario": "claim_rate_up"},
        "alerts": {"log": "data/governance/monitoring_alerts.jsonl", "n_alerts_this_run": 7},
        "model_status": {
            "model_id": "SYNTHETIC-monitor-glm",
            "status_before_run": "champion",
            "status_after_run": "under_review",
        },
        "credit_model": {"status": "not measured", "reason": "SYNTHETIC reason: no credit data in the render test"},
        "indicators": {
            "values": [
                {"name": "low_confidence_fields", "component": "extraction", "value": 0.04321, "level": "ok"},
                {"name": "override_rate", "component": "reviewer", "value": 0.13846, "interval": [0.06154, 0.23077]},
            ]
        },
    }
    outcomes = {
        "data_label": "SYNTHETIC outcomes joined to SIMULATED decisions (render test)",
        "interpretation": "SYNTHETIC interpretation of the render test.",
        "window": {"issue_years": [2017, 2018], "applications": 4321, "outcomes_arrived": 3210},
        "decision_model": {"model_id": "SYNTHETIC-lc-pd-champion"},
        "decided_by": "SIMULATED rule",
        "summary": {
            "join": {"key": "application_id"},
            "default_rate_by_decision": {
                "approve": {
                    "n_decisions": 3001,
                    "n_with_outcome": 2222,
                    "n_pending": 779,
                    "default_rate": {**ci(0.14986, 0.13537, 0.16472), "k": 333, "n": 2222},
                    "mean_recorded_pd_with_outcome": 0.15123,
                },
                "decline_for_review": {
                    "n_decisions": 0,
                    "n_with_outcome": 0,
                    "n_pending": 0,
                    "default_rate": {
                        "status": "not measured",
                        "reason": "no outcomes have arrived for these decisions",
                    },
                    "mean_recorded_pd_with_outcome": None,
                },
            },
            "approved_and_defaulted": {"n_approved_with_outcome": 2222, "n_approved_and_defaulted": 333},
            "recorded_pd_calibration": {
                "ece": ci(0.01234, 0.00987, 0.01543),
                "brier": ci(0.13579, 0.13246, 0.13913),
                "observed_over_expected": 0.99091,
            },
        },
        "caveats": ["SYNTHETIC caveat: selective labels."],
    }
    champion = {
        "data_label": "SYNTHETIC Lending Club-shaped loans (render test)",
        "interpretation": "SYNTHETIC comparison of the render test.",
        "windows": {
            "champion": {"rule": "SYNTHETIC champion window"},
            "challenger": {"rule": "SYNTHETIC challenger window"},
            "out_of_time": {"rule": "SYNTHETIC out-of-time rows", "n_rows": 5432},
        },
        "comparison": {
            "champion": {name: ci(0.70123 + i, 0.69012 + i, 0.71234 + i) for i, name in enumerate(("auc", "ks"))},
            "challenger": {"auc": ci(0.71357, 0.70246, 0.72468)},
            "paired_difference_challenger_minus_champion": {"auc": ci(0.01234, 0.00456, 0.02012)},
        },
        "recommendation": {
            "recommendation": "keep_champion",
            "reason": "SYNTHETIC: not met",
            "promoted_by_this_run": False,
            "who_decides": "a model_validator (render test)",
        },
        "registry": {
            "models": {
                "champion": {"model_id": "SYNTHETIC-champion-model", "status_in_registry": "champion"},
                "challenger": {"model_id": "SYNTHETIC-challenger-model", "status_in_registry": "challenger"},
            }
        },
    }
    gate = {
        "verdict": "FAIL",
        "counts": {"rows": 3, "PASS": 1, "FAIL": 1, "MISSING": 0, "NOT_MEASURED": 1},
        "config": {"file": "config/release_gate.yaml", "section": "gate"},
        "baseline": {"manifest_git_commit": "c" * 40},
        "rows": [
            {
                "id": "credit_auc_ci_low",
                "status": "PASS",
                "reason": "SYNTHETIC: within tolerance",
                "gate_on": "ci_bound",
                "current": {"value": reading(0.72345), "ci_bound": reading(0.71987)},
                "baseline": {"value": reading(0.72222), "ci_bound": reading(0.71864)},
            },
            {
                "id": "agent_required_escalations",
                "status": "FAIL",
                "reason": "SYNTHETIC: below target",
                "gate_on": "value",
                "current": {"value": reading(0.98333)},
                "baseline": {"value": reading(1.0)},
            },
            {
                "id": "llm_citation_accuracy",
                "status": "NOT_MEASURED",
                "reason": "SYNTHETIC: no LLM run",
                "gate_on": "value",
                "current": {"value": reading(None)},
                "baseline": {"value": reading(None)},
            },
        ],
    }
    lineage = {
        "data_label": "SYNTHETIC",
        "status": "measured on the SIMULATED decision log of the render test",
        "interpretation": "SYNTHETIC lineage interpretation.",
        "checks": {"records_read": 65, "records_with_lineage": 64, "records_by_status": {"OK": 61, "STALE": 3}},
    }
    feedback = {
        "data_label": "SYNTHETIC",
        "interpretation": "SYNTHETIC feedback interpretation.",
        "summary": {
            "decisions": 65,
            "overrides": 9,
            "override_rate": {"value": 0.13846, "ci_low": 0.06154, "ci_high": 0.23077, "k": 9, "n": 65},
            "overrides_without_reason": 0,
            "definition": "override = an approve or decline against the reference recommendation (render test)",
            "reasons_on_overrides": {"documentation_insufficient": 4, "other": 5},
        },
    }
    return {
        "monitoring": section(monitoring),
        "outcomes": section(outcomes),
        "champion_challenger": section(champion),
        "release_gate": section(gate),
        "lineage": section(lineage),
        "feedback": section(feedback),
    }


def agent_results() -> dict[str, Any]:
    """results/agent_metrics.json (eval_agent.py layout; SYNTHETIC values)."""
    return {
        "generated_at": "2026-03-04T05:06:07+00:00",
        "status": "measured (deterministic metrics); LLM-dependent metrics not measured",
        "versions": {
            "prompt_version": "memo-prompt-SYNTHETIC",
            "git": {"commit": "a" * 40, "uncommitted_changes": True},
        },
        "configuration": {"llm": "fake:fake-llm (SYNTHETIC render test)", "retrieval_mode": "hybrid"},
        "reference_policy": {
            "recommendation_agreement": {"value": 0.97531, "ci_low": 0.93827, "ci_high": 1.0, "n": 81}
        },
        "injection_detector": {
            "recall": {"value": 0.77778, "ci_low": 0.44444, "ci_high": 1.0, "n": 9},
            "false_positive_rate": {"value": 0.02778, "ci_low": 0.0, "ci_high": 0.06944, "n": 72},
        },
        "deterministic_path": {
            "reached_human_checkpoint": {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 81},
            "decisions_recorded_without_a_human": 0,
            "escalation": {"all_required_escalations": {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 73}},
        },
        "llm_dependent": {
            "citation_accuracy": "not measured: SYNTHETIC reason (no LLM in the render test)",
            "unsupported_claim_rate": "not measured: SYNTHETIC reason (no LLM in the render test)",
            "memo_writer_used_instead": "template memo of the render test (SYNTHETIC)",
        },
    }


def provenance_block(commit: str) -> dict[str, Any]:
    """A governance/provenance.py block."""
    return {"git_sha": commit, "git_dirty": False, "generated_at_utc": "2026-04-05T06:07:08+00:00"}


def docintel_results() -> dict[str, Any]:
    """results/docintel_metrics.json (rules extractor only; SYNTHETIC values)."""
    return {
        "provenance": {
            **provenance_block("d" * 40),
            "data_snapshots": {"tesseract_version": "tesseract SYNTHETIC-version"},
        },
        "run": {"extractors": ["rules"], "provider": None},
        "rules": {
            "fields": {"overall": {"f1": {"value": 0.98642, "ci_low": 0.97531, "ci_high": 0.99753}}},
            "latency": {"ocr_pages_total": 37},
            "page_classification": {"all": {"numerator": 412, "denominator": 412}},
        },
        "comparisons": {"rules_vs_hybrid": "not measured (SYNTHETIC: no provider run)"},
    }


def detector_results() -> dict[str, Any]:
    """results/injection/injection_metrics_detector_only.json (no model run; SYNTHETIC values)."""
    return {
        "provenance": provenance_block("e" * 40),
        "layers": {name: "not measured (no model run for this layer)" for name in ("L0", "L1", "L2")},
        "action_level": {"status": "enforced by tests of the render test; not an empirical rate"},
        "detector": {
            "recall_raw": {"all": {"successes": 13, "n": 61, "rate": 0.21311, "wilson_ci95": [0.12901, 0.33114]}}
        },
    }


def full_sources() -> dict[str, Any]:
    """Every file a pack reads, SYNTHETIC."""
    return {
        RISK: minimal_results(),
        GOVERNANCE: governance_results(),
        AGENT: agent_results(),
        DOCINTEL: docintel_results(),
        INJECTION_DETECTOR: detector_results(),
        MIGRATION_CERTIFICATES: [],
    }


def render_both(sources: dict[str, Any]) -> dict[str, str]:
    """Both packs as text."""
    return {
        "bank_model_risk.md": render_bank_model_risk(sources),
        "insurance_ai_program.md": render_insurance_ai_program(sources),
    }


def template_numbers() -> set[str]:
    """Numbers in the packs rendered with no results at all (the packs' own text)."""
    found: set[str] = set()
    for body in render_both({}).values():
        found |= numbers_in(body)
    return found


# ---------------------------------------------------------------------------
# Ground rule 1: numbers come from the JSON; missing values say so
# ---------------------------------------------------------------------------


def test_no_results_render_not_measured_everywhere() -> None:
    for name, body in render_both({}).items():
        assert "not measured" in body, name
        assert "| 0." not in body and "[0." not in body, name  # no interval or share is printed
    bank = render_bank_model_risk({})
    for name in GOVERNANCE_SECTIONS:
        assert f"has no section `{name}`" in bank, name


def test_pack_text_holds_no_metric() -> None:
    """With no results, the only numbers left are dates, identifiers and named constants of the documents cited."""
    found = template_numbers()
    decimals = {token for token in found if "." in token or "%" in token}
    assert decimals <= ALLOWED_PACK_DECIMALS
    large = {token for token in found if "." not in token and "%" not in token and float(token.replace(",", "")) >= 100}
    assert large <= ALLOWED_PACK_LARGE_INTEGERS


def test_every_pack_number_comes_from_the_json() -> None:
    sources = full_sources()
    assert untraceable_numbers(render_both(sources), sources, template_numbers()) == set()


def test_the_check_catches_a_typed_number() -> None:
    sources = full_sources()
    documents = render_both(sources)
    documents["bank_model_risk.md"] += "\n| Invented | AUC | 0.9876 |"
    assert untraceable_numbers(documents, sources, template_numbers()) == {"0.9876"}


@pytest.mark.skipif(not (REPO / "results" / RISK).exists(), reason="no committed results")
def test_packs_from_the_committed_results_hold_only_json_numbers() -> None:
    sources = packs.load_pack_sources(REPO / "results")
    assert untraceable_numbers(render_both(sources), sources, template_numbers()) == set()


# ---------------------------------------------------------------------------
# The Phase 6 sections: rendered when present, "not measured yet" when absent
# ---------------------------------------------------------------------------


def test_governance_sections_render_when_present() -> None:
    bank = render_bank_model_risk(full_sources())
    assert "`results/governance_metrics.json` has no section" not in bank
    # monitoring
    assert (
        "| `no_drift` | no | false alert rate | 0.0500 [0.0000, 0.1500] (1/20) | not applicable | not applicable |"
        in bank
    )
    assert (
        "| `claim_rate_up` | yes | detection rate | 0.9500 [0.8500, 1.0000] (19/20) | 1.32 [0.89, 1.74] (19 of 20 "
        "streams detected) | 0: 9, 1: 4, 2: 3, 5: 3 |" in bank
    )
    assert "7 alert records this run" in bank and "model status champion -> under_review" in bank
    assert "1.0322 [0.9811, 1.0842]" in bank
    assert "not measured (SYNTHETIC reason: no credit data in the render test)" in bank
    assert "| override_rate | reviewer | 0.1385 [0.0615, 0.2308] | no rule (reported only) |" in bank
    assert "| low_confidence_fields | extraction | 0.0432 | ok |" in bank
    # outcomes
    assert "| `approve` | 3,001 | 2,222 | 779 | 0.1499 [0.1354, 0.1647] (333/2,222) | 0.1512 |" in bank
    assert "| `decline_for_review` | 0 | 0 | 0 | not measured (no outcomes have arrived" in bank
    assert "333 of 2,222 approved decisions" in bank and "ECE 0.0123 [0.0099, 0.0154]" in bank
    # champion/challenger
    assert "| AUC | 0.7012 [0.6901, 0.7123] | 0.7136 [0.7025, 0.7247] | 0.0123 [0.0046, 0.0201] |" in bank
    assert "| ECE (lower is better) | not measured | not measured | not measured |" in bank
    assert "`keep_champion`" in bank and "Promoted by this run: no" in bank
    assert "| challenger | `SYNTHETIC-challenger-model` | challenger |" in bank
    # release gate
    assert "Verdict: **FAIL** (rows: 3, PASS: 1, FAIL: 1, MISSING: 0, NOT_MEASURED: 1)" in bank
    assert "| `credit_auc_ci_low` | PASS | ci_bound | 0.7199 | 0.7186 | SYNTHETIC: within tolerance |" in bank
    assert "| `llm_citation_accuracy` | NOT_MEASURED | value | not measured | not measured |" in bank
    # lineage and feedback
    assert "64 of 65; by status: OK: 61, STALE: 3" in bank
    assert "override rate 0.1385 [0.0615, 0.2308] (9/65)" in bank
    assert "| `documentation_insufficient` | 4 |" in bank
    # every section names its commit
    assert f"section `monitoring`, written at git commit `{'f' * 40}`" in bank


def test_monitoring_block_edge_cases() -> None:
    sources = full_sources()
    monitoring = sources[GOVERNANCE]["monitoring"]["results"]
    monitoring["model_status"]["status_before_run"] = None  # the run registered the model
    monitoring["indicators"]["values"] = [
        {"name": "escalated", "component": "agent", "value": 1.0, "interval": [1.0, 1.0], "level": "ok"},
        {"name": "unsupported_claims", "component": "agent", "value": None, "level": None},
    ]
    block = "\n".join(monitoring_block(sources))
    assert "model status not registered before the run -> under_review" in block
    assert "| escalated | agent | 1.0000 (zero-width interval) | ok |" in block
    assert "| unsupported_claims | agent | not measured | not measured |" in block


def test_missing_indicator_keeps_the_status_and_note_the_monitor_recorded() -> None:
    """P6-PACKS-2: an indicator the monitor could not read says why ('not available' and its note), instead of a
    bare 'not measured' that hides that the feedback section was absent when the monitor ran."""
    sources = full_sources()
    monitoring = sources[GOVERNANCE]["monitoring"]["results"]
    monitoring["indicators"]["values"] = [
        {
            "name": "override_rate",
            "component": "review_feedback",
            "status": "not available",
            "value": None,
            "interval": None,
            "level": None,
            "note": "results/governance_metrics.json has no 'feedback' section (the feedback track has not run)",
        },
        {
            "name": "unsupported_claim_rate",
            "component": "agent",
            "status": "not measured",
            "value": None,
            "level": None,
            "note": "not measured: no LLM provider was used",
        },
    ]
    block = "\n".join(monitoring_block(sources))
    assert (
        "| override_rate | review_feedback | not available (results/governance_metrics.json has no 'feedback' "
        "section (the feedback track has not run)) | not available |"
    ) in block
    assert "| unsupported_claim_rate | agent | not measured: no LLM provider was used | not measured |" in block
    assert "| override_rate | review_feedback | not measured |" not in block
    assert "Indicators the monitor read from the results files when it ran" in block


def test_mean_delay_says_it_covers_detected_streams_only() -> None:
    """P6-STAT-3 / P6-PACKS-11: the mean delay names its population and the window cap, a zero-width interval is not
    printed as an interval, and a scenario where no stream was detected says not applicable, not not measured."""
    block = "\n".join(monitoring_block(full_sources()))
    assert "Mean delay among detected streams, windows [95% CI] (detected of all streams)" in block
    assert "Detected streams by delay (windows: streams)" in block
    assert "20 streams per scenario, 12 windows each, numbered from 0; drift, if any, from window 6" in block
    assert "over the detected streams only" in block and "cannot exceed the number of drifted windows" in block
    assert "| 0.00 (20 of 20 streams detected; zero-width bootstrap interval) | 0: 20 |" in block
    assert "[0.00, 0.00]" not in block
    assert (
        "| `mild_shift` | yes | detection rate | 0.0000 (0/20; zero-width bootstrap interval, exact interval not "
        "recorded) | not applicable (no stream was detected, so there is no delay to report) | not applicable |"
    ) in block
    assert "Mean delay, windows [95% CI]" not in block


def test_absent_governance_sections_say_not_measured_yet_with_their_make_target() -> None:
    sources = {**full_sources(), GOVERNANCE: {}}
    bank = render_bank_model_risk(sources)
    assert "not measured yet: `results/governance_metrics.json` has no section `monitoring`; `make monitor`" in bank
    assert "has no section `outcomes`; `make champion-challenger` writes it" in bank
    assert "has no section `release_gate`; `make release-gate ARGS=--write-results` writes it" in bank
    assert "has no section `feedback`; `make feedback-report` writes it" in bank


def test_map_rows_follow_the_sections() -> None:
    absent = {row[0]: row[2] for row in sr_26_2_rows({})}
    present = {row[0]: row[2] for row in sr_26_2_rows(full_sources())}
    monitoring = "V. Ongoing model monitoring"
    assert absent[monitoring].startswith("not measured yet (`make monitor`)")
    assert present[monitoring].startswith("SIMULATED stream on a separate demonstration freMTPL2 GLM only")
    assert absent.keys() == present.keys()
    risk_row = [row for row in naic_rows({}) if row[0] == "Risk management and internal controls"][0]
    assert "of the extraction and the agent: not measured yet (`make monitor`)" in risk_row[2]
    risk_row = [row for row in naic_rows(full_sources()) if row[0] == "Risk management and internal controls"][0]
    assert "measured (section 3)" in risk_row[2]


def test_packs_say_no_inventory_model_is_monitored() -> None:
    """P6-PACKS-1: the monitor watches its own demonstration GLM with no discrimination metric; the packs must not
    read as if the inventory models (or the frequency and severity models in scope) were monitored."""
    present = {row[0]: row[2] for row in sr_26_2_rows(full_sources())}
    row = present["V. Ongoing model monitoring"]
    assert "none of the inventory models (section 1) is monitored" in row and "no discrimination metric" in row
    bank = render_bank_model_risk(full_sources())
    assert "Phase 6 runs it" not in bank
    assert "it is not run for any inventory model" in bank
    assert "separate demonstration GLM trained by `scripts/monitor.py`, which is not an inventory model" in bank
    program = render_insurance_ai_program(full_sources())
    assert "not the frequency or severity model of section 1 (neither of those is monitored)" in program
    risk_row = [row for row in naic_rows(full_sources()) if row[0] == "Risk management and internal controls"][0]
    assert "the frequency and severity models are not monitored; no discrimination metric" in risk_row[2]


# ---------------------------------------------------------------------------
# The LLM extraction and the agent
# ---------------------------------------------------------------------------


def test_llm_dependent_measures_say_not_measured() -> None:
    bank = render_bank_model_risk(full_sources())
    assert "| Memo citation accuracy (LLM memo) | not measured: SYNTHETIC reason (no LLM in the render test) |" in bank
    assert "| Field extraction, LLM (hybrid) extractor vs rules | not measured (SYNTHETIC: no provider run) |" in bank
    assert "L0, L1, L2: not measured (no model run for this layer)" in bank
    assert "0.2131 [0.1290, 0.3311] (13/61, Wilson 95%)" in bank
    assert "no `results/llm_migration_*.json` certificate exists" in bank
    # A zero-width bootstrap interval is not shown as an interval.
    assert "1.0000 (n=81; zero-width bootstrap interval, exact interval not recorded)" in bank
    assert "[1.0000, 1.0000]" not in bank


def test_bank_pack_states_the_llm_use_recorded_not_that_no_llm_was_available() -> None:
    """P6-PACKS-9: local Ollama runs exist in the repo, so 'No LLM was available' was wrong; the pack says which LLM
    the results files record, and an absent extraction file is not read as 'no LLM'."""
    bank = render_bank_model_risk(full_sources())
    assert "No LLM was available" not in bank
    assert (
        "LLM use recorded for the results below: agent memo writer: fake:fake-llm (SYNTHETIC render test); "
        "extraction LLM provider: none (extractors run: rules)."
    ) in bank
    assert "agent memo writer: not measured; extraction LLM provider: not measured" in render_bank_model_risk({})
    sources = full_sources()
    sources[DOCINTEL]["run"] = {"extractors": ["rules", "hybrid"], "provider": "ollama"}
    assert "extraction LLM provider: ollama" in render_bank_model_risk(sources)


def test_third_party_pretrained_models_are_listed_with_their_recorded_use() -> None:
    """P6-PACKS-4: Tesseract (a pretrained LSTM) read the scanned pages behind the extraction metrics, and the agent's
    retrieval runs pretrained fastembed models; both packs list them instead of saying no third-party AI was used."""
    rows = {row[0]: row[2] for row in third_party_model_rows(full_sources())}
    assert rows["Tesseract OCR (tesseract SYNTHETIC-version)"].startswith(
        "37 of 412 pages read by OCR in the extraction evaluation"
    )
    assert rows["Dense text embedding model (fastembed; `rag/settings.py` DENSE_MODEL_ID)"] == (
        "yes (the agent evaluation recorded retrieval mode `hybrid`)"
    )
    assert rows["Cross-encoder reranker (fastembed; `rag/settings.py` RERANK_MODEL_ID)"] == (
        "no (the agent evaluation recorded retrieval mode `hybrid`)"
    )
    absent = [row[2] for row in third_party_model_rows({})]
    assert absent[0].startswith("not measured (no OCR page count") and absent[1].startswith("not measured")
    program = render_insurance_ai_program(full_sources())
    assert "none produced any number" not in program
    assert "| Tesseract OCR (tesseract SYNTHETIC-version) |" in program
    third_party = [row for row in naic_rows(full_sources()) if row[0] == "Third-party AI systems"][0]
    assert "No third-party AI system produced these results" not in third_party[2]
    assert "pretrained OCR and retrieval models" in third_party[2]
    bank = render_bank_model_risk(full_sources())
    assert "### Third-party pretrained models (not in the inventory)" in bank
    assert "| Tesseract OCR (tesseract SYNTHETIC-version) |" in bank
    vendor = {row[0]: row[2] for row in sr_26_2_rows(full_sources())}["VII. Vendor and other third-party products"]
    assert "No vendor model is used by the inventory models" in vendor and "not validated here" in vendor


def test_measured_injection_layers_and_certificates_are_shown() -> None:
    sources = full_sources()
    sources[INJECTION] = {
        "layers": {
            "L0": {"asr": {"all": {"successes": 21, "n": 60, "rate": 0.35, "wilson_ci95": [0.2414, 0.4767]}}},
            "L2": {"asr": "not measured (no scored answer for this layer)"},
        }
    }
    sources[MIGRATION_CERTIFICATES] = ["llm_migration_a_vs_b.json"]
    rows = {row[0]: row[2] for row in genai_control_rows(sources)}
    layers = rows["Prompt-injection attack success rate by defense layer (frozen payloads)"]
    assert (
        layers == "L0: 0.3500 [0.2414, 0.4767] (21/60, Wilson 95%); L2: not measured (no scored answer for this layer)"
    )
    certificate = (
        "An LLM or prompt change is to be certified by a migration certificate signed by a model_validator "
        "(a process step)"
    )
    assert rows[certificate] == "`results/llm_migration_a_vs_b.json`"


def test_migration_certificate_is_not_listed_as_an_enforced_control() -> None:
    """P6-PACKS-5: nothing in CI or the release gate requires a certificate when the prompt or LLM changes, so the
    'How it is enforced' cell says it is not enforced, and the bank text calls it a manual step."""
    enforcement = {row[0]: row[1] for row in genai_control_rows(full_sources())}
    cell = enforcement[
        "An LLM or prompt change is to be certified by a migration certificate signed by a model_validator "
        "(a process step)"
    ]
    assert cell.startswith("Not enforced: scripts/llm_migration_gate.py is run by hand")
    assert "neither CI nor the release gate checks that a certificate exists" in cell
    bank = render_bank_model_risk(full_sources())
    assert "a manual step: neither CI nor the release gate checks for a certificate" in bank
    assert "needs a migration certificate signed by a model_validator" not in bank


# ---------------------------------------------------------------------------
# Regulatory statements (ground rule 10) and data labels
# ---------------------------------------------------------------------------


def test_both_packs_state_applicability_demonstration_and_data() -> None:
    for name, body in render_both(full_sources()).items():
        assert packs.APPLICABILITY in body, name
        assert "SYNTHETIC / SIMULATED" in body and "public" in body, name
        assert not re.search(r"\bcompliant\b", body, re.IGNORECASE), name
        assert "SR 11-7-style" not in body, name


def test_data_statement_does_not_deny_the_real_records_in_the_public_data() -> None:
    """Lending Club, freMTPL2 and German Credit are de-identified records of real loans and policies, and the
    sanctions lists name real persons: the statement says so instead of 'no real applicant is involved'."""
    statement = packs.DATA_STATEMENT
    assert "No real applicant" not in statement
    assert "de-identified records of real loans, credit applicants and motor policies" in statement
    assert "name real sanctioned persons and entities" in statement
    assert "every decision here is SYNTHETIC or SIMULATED" in statement


def test_bank_pack_cites_sr_26_2_and_governs_genai_separately() -> None:
    bank = render_bank_model_risk({})
    assert "SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026" in bank and "April 17, 2026" in bank
    assert "Aligned to the structure of SR 26-2" in bank
    assert "$30 billion in total assets" in bank
    assert packs.SR_26_2_FOOTNOTE_3 in bank
    assert "## 7. The LLM extraction and the agent: governed outside SR 26-2" in bank
    assert "were not checked against it" in bank  # the OCC and FDIC identifiers


def test_footnote_3_is_quoted_in_full_and_its_sentences_are_named_right() -> None:
    """P6-PACKS-3: the quote keeps the footnote's fourth sentence (non-generative AI stays within the principles),
    and the pack's own controls follow the third sentence, not 'the last sentence'."""
    assert packs.SR_26_2_FOOTNOTE_3.endswith(
        "However, the principles described in this guidance apply to traditional statistical and quantitative models "
        "and non-generative, non-agentic AI models."
    )
    assert len(re.findall(r"\.(?: |$)", packs.SR_26_2_FOOTNOTE_3)) == 4
    bank = render_bank_model_risk({})
    assert "footnote 3 in full" in bank
    assert "Following the footnote's third sentence" in bank
    assert "footnote's last sentence" not in bank
    assert "Still within its principles: traditional statistical and quantitative models and non-generative" in bank


def test_insurance_pack_says_the_naic_text_was_not_checked() -> None:
    program = render_insurance_ai_program({})
    assert "adopted December 4, 2023" in program
    assert "**not checked against a local copy**" in program and "HTTP 403" in program
    assert "No section of the bulletin is quoted here." in program
    assert "## 4. Third-party AI systems and data" in program and "## 5. Fairness testing" in program


@pytest.mark.integration
@pytest.mark.skipif(not (REGULATORY_DIR / "frb_sr_26_2.pdf").exists(), reason="needs make data-regulatory")
def test_quoted_wording_matches_the_local_primary_sources() -> None:
    """The SR 26-2 and NY DFS wording the packs quote or paraphrase is in the downloaded primary sources."""
    from pypdf import PdfReader

    def flat(raw: str) -> str:
        """Straight quotes, words split by a line-end hyphen rejoined ("rule-\\nbased"), single spaces."""
        straight = raw.replace("’", "'").replace("“", '"').replace("”", '"')
        return re.sub(r"\s+", " ", re.sub(r"-[ \t]*\n\s*", "-", straight))

    pdf = flat(" ".join(page.extract_text() or "" for page in PdfReader(REGULATORY_DIR / "frb_sr_26_2.pdf").pages))
    # Footnote 3 is quoted whole: its marker comes right before the quote, and the page number (the end of its page)
    # right after it, so no sentence of the footnote is left out at either end.
    footnote = flat(packs.SR_26_2_FOOTNOTE_3)
    assert footnote in pdf
    start = pdf.index(footnote)
    assert pdf[:start].endswith(" 3 "), pdf[start - 80 : start]
    after = pdf[start + len(footnote) :]
    assert re.match(r" \d+ ", after), after[:80]
    for quoted in (
        packs.SR_26_2_FOOTNOTE_3,
        packs.SR_26_2_NOT_A_MODEL,
        packs.SR_26_2_IMMATERIAL,
        "does not set forth enforceable standards or prescriptive requirements",
        "most relevant to banking organizations with over $30 billion in total assets",
        "Model purpose, together with model exposure, determines model materiality",
        "April 17, 2026",
    ):
        assert flat(quoted) in pdf, quoted
    html = flat(re.sub(r"<[^>]+>", " ", (REGULATORY_DIR / "nydfs_circular_letter_2024_07.html").read_text("utf-8")))
    for phrase in (
        "July 11, 2024",
        "Adverse Impact Ratio",
        "may not rely solely on a third-party's claim of non-discrimination",
        "additional clause drawn from the NAIC's Model Bulletin",
        "only apply to protected classes for which data are available, or may be reasonably imputed",
    ):
        assert phrase in html, phrase


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def test_lookup_and_value_in() -> None:
    sources = full_sources()
    assert packs.value_in(sources, GOVERNANCE, "release_gate.results.rows.1.id") == "agent_required_escalations"
    assert packs.value_in(sources, GOVERNANCE, "release_gate.results.rows.9.id") is None
    assert packs.value_in(sources, "absent.json", "anything") is None
    assert packs.section_results(sources, GOVERNANCE, "feedback")["summary"]["overrides"] == 9


def test_share_formats() -> None:
    assert packs.share({"value": 0.9, "ci_low": 0.8, "ci_high": 0.95, "numerator": 9, "denominator": 10}) == (
        "0.9000 [0.8000, 0.9500] (9/10)"
    )
    assert (
        packs.share({"estimate": 0.25, "ci_low": 0.1, "ci_high": 0.4, "k": 3, "n": 12})
        == "0.2500 [0.1000, 0.4000] (3/12)"
    )
    exact = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "exact_ci_low": 0.99378, "exact_ci_high": 1.0, "n": 590}
    assert packs.share(exact) == "1.0000 exact [0.9938, 1.0000] (n=590)"
    assert packs.share({"value": 1.0, "ci_low": 1.0, "ci_high": 1.0}) == (
        "1.0000 (zero-width bootstrap interval, exact interval not recorded)"
    )
    assert packs.share({"status": "not measured", "reason": "why"}) == "not measured (why)"
    assert packs.share("not measured: no LLM") == "not measured: no LLM"
    assert packs.wilson_share({"rate": 0.2, "wilson_ci95": [0.1, 0.3]}) == "0.2000 [0.1000, 0.3000] (Wilson 95%)"
    assert packs.full_stop("a (b.)") == "a (b.)" and packs.full_stop("a") == "a."


def test_provenance_reads_all_three_layouts() -> None:
    sources = full_sources()
    assert packs.provenance(sources, GOVERNANCE, "lineage").git_commit == "f" * 40
    assert packs.provenance(sources, AGENT).git_commit == "a" * 40
    assert packs.provenance(sources, AGENT).uncommitted_changes is True
    assert packs.provenance(sources, DOCINTEL).git_commit == "d" * 40
    assert packs.provenance(sources, INJECTION) is None
    assert "the file is absent" in packs.source_note(sources, INJECTION)
    rows = packs.sources_table(sources, [(GOVERNANCE, "monitoring", "4"), (INJECTION, None, "7")])
    assert (
        f"| `results/governance_metrics.json` | `monitoring` | 4 | 2026-02-03T04:05:06+00:00 | `{'f' * 40}` | no |"
        in rows
    )
    assert "| `results/injection_metrics.json` | whole file | 7 | absent | not measured | not measured |" in rows


def test_load_pack_sources_reads_only_existing_files(tmp_path: Path) -> None:
    (tmp_path / AGENT).write_text(json.dumps(agent_results()), encoding="utf-8")
    (tmp_path / "llm_migration_a_vs_b.json").write_text("{}", encoding="utf-8")
    sources = packs.load_pack_sources(tmp_path)
    assert set(sources) == {AGENT, MIGRATION_CERTIFICATES}
    assert sources[MIGRATION_CERTIFICATES] == ["llm_migration_a_vs_b.json"]


# ---------------------------------------------------------------------------
# Materiality tiering (a design choice: packs_inventory.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purpose", "exposure", "designed", "now"),
    [
        ("consumer_decision", False, 1, 3),
        ("consumer_decision", True, 1, 1),
        ("financial_risk", False, 2, 3),
        ("financial_risk", True, 2, 2),
        ("method_demonstration", True, 3, 3),
    ],
)
def test_materiality_tier_rule(purpose: str, exposure: bool, designed: int, now: int) -> None:
    tier = materiality_tier(purpose, exposure)
    assert (tier.tier_as_designed, tier.tier_now) == (designed, now)
    assert purpose in tier.reason


def test_tier_3_rule_does_not_claim_monitoring_that_no_code_does() -> None:
    """P6-PACKS-6: has_exposure is a declared field and no inventory model's performance is monitored, so the rule
    quotes what SR 26-2 lists for immaterial models (performance and conditions) and says only the listing is done."""
    rule = " ".join(TIERING_RULE)
    assert "monitored for the conditions under which it would become material" not in rule
    assert f'"{packs.SR_26_2_IMMATERIAL}"' in rule
    assert "monitoring model performance" in packs.SR_26_2_IMMATERIAL
    assert "exposure is declared in `packs_inventory.py`, not monitored" in rule
    assert "no inventory model's performance is monitored" in rule
    for body in render_both({}).values():
        assert "Only the first part is done here" in body
    exposure_row = {row[0]: row[2] for row in sr_26_2_rows({})}[
        "III. Inherent risk, exposure and purpose; materiality = purpose + exposure"
    ]
    assert "declared, not monitored" in exposure_row


def test_unknown_purpose_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown purpose"):
        materiality_tier("pricing", False)


def test_inventory_tiers() -> None:
    designed = {entry.key: entry.tier().tier_as_designed for entry in INVENTORY}
    assert designed == {"credit_pd": 1, "insurance_frequency": 1, "insurance_severity": 1, "fairness_demo": 3}
    assert all(entry.tier().tier_now == 3 for entry in INVENTORY)  # no model has exposure in this repository
    assert inventory_entry("credit_pd").results_section == "credit"
    with pytest.raises(KeyError):
        inventory_entry("nope")
