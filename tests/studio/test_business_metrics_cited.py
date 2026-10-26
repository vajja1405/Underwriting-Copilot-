"""Citing the earlier Studio results (S1 coverage, S5 paths, S6 handoff) from hand-made SYNTHETIC result files."""

import json
from pathlib import Path
from typing import Any

from uwcopilot.studio.business_metrics.cited import (
    COVERAGE_FILE,
    HANDOFF_FILE,
    PATHS_FILE,
    cite_coverage,
    cite_handoff,
    cite_paths,
)

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


def test_the_handoff_block_copies_values_and_the_source_provenance(tmp_path: Path) -> None:
    write(tmp_path, HANDOFF_FILE, handoff_report())
    cited = cite_handoff(tmp_path)
    trace, handoff = cited["traceability"], cited["handoff"]
    assert trace["package_numbers_read_back"] == 1700 and trace["untraced_numbers_pdf_text"] == 0
    assert trace["pdf_text_audit_empty"] is None  # missing key: None, never guessed
    assert handoff["golden_founder_scenarios"]["reached_human_checkpoint"] == RATE
    assert handoff["all_cases"]["cases"] == 4 and handoff["settings"]["n_paths"] == 200
    assert handoff["source_provenance"]["git_sha"] == COMMIT and handoff["source_provenance"]["git_dirty"] is False
    assert handoff["source_file"].endswith(HANDOFF_FILE)


def test_a_missing_file_is_not_measured_and_names_its_make_target(tmp_path: Path) -> None:
    cited = cite_handoff(tmp_path)
    assert cited["handoff"]["status"].startswith("not measured here") and "make studio-handoff" in str(cited)
    assert "make studio-coverage" in cite_coverage(tmp_path)["status"]
    assert "make studio-paths" in cite_paths(tmp_path)["status"]


def test_the_coverage_block_keeps_each_metro_and_business_type(tmp_path: Path) -> None:
    metro = {
        "name": "SYNTHETIC metro",
        "mode": "synthetic",
        "min_coverage_share": 0.5,
        "coverage_threshold": 0.8,
        "by_business_type": {"motel": {"coverage_share": 0.5, "mode": "limited", "factors": []}},
    }
    write(tmp_path, COVERAGE_FILE, {"description": "coverage_share = ...", "metros": {"m1": metro}, "provenance": {}})
    cited = cite_coverage(tmp_path)
    assert cited["metros"]["m1"]["min_coverage_share"] == 0.5
    assert cited["metros"]["m1"]["by_business_type"] == {"motel": {"coverage_share": 0.5, "mode": "limited"}}
    assert cited["definition"] == "coverage_share = ..." and cited["metros"]["m1"]["tracts"] is None


def test_the_paths_block_keeps_the_headline_rate_and_each_metro(tmp_path: Path) -> None:
    rate = {"estimate": 0.25, "ci_low": 0.1, "ci_high": 0.4, "n": 8}
    report = {
        "definitions": {"path_found_rate": "share of plans ..."},
        "headline": {"n_plans": 8, "path_found_rate_full": rate, "status_counts_full": {"paths_found": 2}},
        "by_metro": {"m1": {"n_plans": 8, "path_found_rate_among_plans_needing_a_change": rate}},
        "real_world_accuracy": "not measured",
        "provenance": PROVENANCE,
    }
    write(tmp_path, PATHS_FILE, report)
    cited = cite_paths(tmp_path)
    assert cited["path_found_rate_full"] == rate and cited["by_metro"]["m1"]["n_plans"] == 8
    assert cited["definition"] == "share of plans ..." and cited["real_world_accuracy"] == "not measured"
    assert cited["source_provenance"]["generated_by"] == "scripts/x.py"
