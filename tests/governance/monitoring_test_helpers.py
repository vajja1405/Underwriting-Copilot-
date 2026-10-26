"""Builders shared by the monitoring tests (tests/governance/test_monitoring*.py). All data is SYNTHETIC.

- synthetic_policies / toy_frequency: cleaned freMTPL2-shaped policies and a stand-in "model" (no training);
- small_config / write_inputs / run_paths: a short stream, input files and output paths for running
  scripts/monitor.py end to end under a temporary folder (offline);
- simulated_feedback_summary: what governance/feedback.py summarize_feedback writes for SIMULATED reviewer records
  (the real producer of the "feedback" section the monitor reads).
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

import monitor
from uwcopilot.governance.feedback import FEEDBACK_VERSION, summarize_feedback
from uwcopilot.governance.monitoring_config import DEFAULT_CONFIG_PATH, MonitoringConfig
from uwcopilot.risk.data.fremtpl2 import prepare_fremtpl2, synthetic_fremtpl2_frames


def review_record(record_id: str, review_action: str, codes: list[str]) -> dict[str, Any]:
    """A minimal SIMULATED agent decision record with a Phase 6 reviewer block."""
    return {
        "record_id": record_id,
        "application_id": f"APP-{record_id}",
        "policy_result": {"line": "credit"},
        "reference_recommendation": "refer",
        "reviewer": {
            "action": "approve" if review_action == "override" else "refer",
            "role": "senior_underwriter",
            "rationale": "SIMULATED review of a SYNTHETIC file.",
            "review_action": review_action,
            "reason_codes": codes,
            "feedback_version": FEEDBACK_VERSION,
        },
        "reviewer_label": "SIMULATED",
    }


def simulated_feedback_summary(override_codes: list[list[str]], accepts: int) -> dict[str, Any]:
    """summarize_feedback output for one override per code list plus `accepts` accepted recommendations."""
    records = [review_record(f"O{number}", "override", codes) for number, codes in enumerate(override_codes)]
    records += [review_record(f"A{number}", "accept", []) for number in range(accepts)]
    return summarize_feedback(records)


def synthetic_policies(n_policies: int, seed: int) -> pd.DataFrame:
    """SYNTHETIC cleaned policies shaped like freMTPL2."""
    frequency_raw, severity_raw = synthetic_fremtpl2_frames(n_policies=n_policies, seed=seed)
    return prepare_fremtpl2(frequency_raw, severity_raw).policies.reset_index(drop=True)


def toy_frequency(frame: pd.DataFrame) -> np.ndarray:
    """A stand-in for a model: claims per year rising with BonusMalus (no training needed)."""
    return 0.08 * np.exp(0.01 * (frame["BonusMalus"].to_numpy(dtype=float) - 50))


def small_config() -> MonitoringConfig:
    """The committed thresholds with a short stream that fits a SYNTHETIC pool of 4,000 held-out policies."""
    document = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    scenarios = document["stream"]["scenarios"]
    young = scenarios["covariate_shift_young_drivers"]
    young["covariate_shift"] = {"subgroup": {"column": "DrivAge", "below": 30}, "target_share": 0.4}
    document["stream"].update(
        window_size=1500,
        n_windows=4,
        drift_start_window=2,
        n_streams=3,
        scenarios={
            "no_drift": scenarios["no_drift"],
            "covariate_shift_young_drivers": young,
            "claim_rate_up_25pct": scenarios["claim_rate_up_25pct"],
        },
    )
    return MonitoringConfig.model_validate(document)


def write_inputs(tmp_path: Path) -> tuple[pd.DataFrame, list[Path], Path]:
    """SYNTHETIC policies, their CSV files (for the cache fingerprint) and a results folder with an agent file.

    The agent file reports 90% of required escalations, so the run has one indicator breach.
    """
    frequency_raw, severity_raw = synthetic_fremtpl2_frames(n_policies=20000, seed=5)
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    files = [data_dir / "freq.csv", data_dir / "sev.csv"]
    frequency_raw.to_csv(files[0], index=False)
    severity_raw.to_csv(files[1], index=False)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    agent = {
        "deterministic_path": {
            "cases": 65,
            "escalation": {"all_required_escalations": {"value": 0.9, "ci_low": 0.8, "ci_high": 1.0, "n": 60}},
        },
        "llm_dependent": {"unsupported_claim_rate": "not measured: no LLM provider was used"},
    }
    (results_dir / "agent_metrics.json").write_text(json.dumps(agent), encoding="utf-8")
    return prepare_fremtpl2(frequency_raw, severity_raw).policies, files, results_dir


def run_paths(tmp_path: Path, results_dir: Path) -> monitor.RunPaths:
    """Every output under tmp_path."""
    governance = tmp_path / "governance"
    return monitor.RunPaths(
        config=DEFAULT_CONFIG_PATH,
        data_dir=tmp_path / "raw",
        results_dir=results_dir,
        results=results_dir / "governance_metrics.json",
        report=results_dir / "monitoring_report.html",
        cache_dir=governance / "monitoring",
        alert_log=governance / "monitoring_alerts.jsonl",
        registry=governance / "model_status.json",
        checksum_log=tmp_path / "checksums.jsonl",
    )
