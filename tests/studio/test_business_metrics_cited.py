"""Citing the earlier Studio results (S1 coverage, S5 paths, S6 handoff) from hand-made SYNTHETIC result files."""

from pathlib import Path

from business_metrics_helpers import COMMIT, PROVENANCE, RATE, handoff_report, write

from uwcopilot.studio.business_metrics.cited import (
    COVERAGE_FILE,
    HANDOFF_FILE,
    PATHS_FILE,
    cite_coverage,
    cite_handoff,
    cite_paths,
)


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
