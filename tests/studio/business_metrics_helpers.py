"""Shared helpers for the Studio S12 business-metrics tests: hand-made SYNTHETIC results files to cite (offline)."""

import json
from pathlib import Path
from typing import Any

COMMIT = "a" * 40
PROVENANCE = {"generated_by": "scripts/x.py", "generated_at_utc": "2026-10-01T00:00:00+00:00", "git_sha": COMMIT}
RATE = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 2, "k": 2, "wilson_ci95": [0.34, 1.0]}


def write(folder: Path, name: str, report: dict[str, Any]) -> None:
    """Write one hand-made results file."""
    (folder / name).write_text(json.dumps(report), encoding="utf-8")


def handoff_report() -> dict[str, Any]:
    """A minimal S6 handoff results file."""
    return {
        "configuration": {"screening": "designed", "n_paths": 200, "golden_scenarios": 2, "memo_writer": "template"},
        "golden_founder_scenarios": {
            "cases": 2,
            "reached_human_checkpoint": RATE,
            "decisions_recorded_without_a_human": 0,
            "package_numbers_read_back": 1700,
            "untraced_numbers_package_text": 0,
            "untraced_numbers_pdf_text": 0,
        },
        "all_cases": {"cases": 4, "reached_human_checkpoint": RATE, "decisions_recorded_without_a_human": 0},
        "provenance": {**PROVENANCE, "git_dirty": False},
    }
