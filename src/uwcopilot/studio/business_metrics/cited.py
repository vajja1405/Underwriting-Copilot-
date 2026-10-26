"""Read the Studio results that earlier scripts already measured, and cite them (docs/studio/s12_baseline.md).

S12 does not recompute what S1, S5 and S6 measured. Each cite_* function copies the needed values from one results
file, names the file and the keys, and carries that file's own provenance (which script wrote it, when, at which
commit and whether the tree was dirty), so a reader can see where and when each number was measured:
- cite_handoff: results/studio_handoff_metrics.json (S6): M5 traceability and M8 agent handoff success;
- cite_coverage: results/studio_metro_coverage.json (S1): M6 data-coverage score by metro;
- cite_paths: results/studio_path_metrics.json (S5): M7 path-found rate.
A missing file gives a "not measured here" block that names the make target that writes it; nothing is filled in.
"""

import json
from pathlib import Path
from typing import Any

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import display_path

HANDOFF_FILE = "studio_handoff_metrics.json"
COVERAGE_FILE = "studio_metro_coverage.json"
PATHS_FILE = "studio_path_metrics.json"
MAKE_TARGETS: dict[str, str] = {
    HANDOFF_FILE: "make studio-handoff",
    COVERAGE_FILE: "make studio-coverage",
    PATHS_FILE: "make studio-paths",
}
COMMIT_KEY = "git_sha"
PROVENANCE_KEYS: tuple[str, ...] = ("generated_by", "generated_at_utc", COMMIT_KEY, "git_dirty")
HANDOFF_TRACE_KEYS: tuple[str, ...] = (
    "package_numbers_read_back",
    "untraced_numbers_package_text",
    "untraced_numbers_pdf_text",
    "package_text_audit_empty",
    "pdf_text_audit_empty",
)
HANDOFF_SUCCESS_KEYS: tuple[str, ...] = (
    "cases",
    "reached_human_checkpoint",
    "decisions_recorded_without_a_human",
    "decisions_recorded_without_a_human_rate",
    "memo_check_passed",
    "unsupported_numbers_in_memos",
)
ALL_CASES_KEYS: tuple[str, ...] = (
    "cases",
    "reached_human_checkpoint",
    "decisions_recorded_without_a_human",
    "unsupported_numbers_in_memos",
)
METRO_KEYS: tuple[str, ...] = (
    "name",
    "data_label",
    "mode",
    "mode_reason",
    "coverage_threshold",
    "min_coverage_share",
    "snapshot_date",
    "tracts",
)


def load_results(results_dir: Path, file_name: str) -> dict[str, Any] | None:
    """The parsed results file, or None when it does not exist."""
    path = results_dir / file_name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def missing_block(results_dir: Path, file_name: str) -> dict[str, Any]:
    """What a metric says when its source results file is missing."""
    path = display_path(results_dir / file_name)
    return {"status": f"not measured here: {path} is missing (written by `{MAKE_TARGETS[file_name]}`)"}


def source_provenance(report: dict[str, Any]) -> dict[str, Any]:
    """The cited file's own provenance: script, time, commit and whether its tree was dirty."""
    provenance = report.get("provenance") or {}
    return {key: provenance.get(key) for key in PROVENANCE_KEYS}


def pick(block: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """The listed keys of a block (a missing key is None, never guessed)."""
    return {key: block.get(key) for key in keys}


def cited_header(results_dir: Path, file_name: str, report: dict[str, Any], section: str) -> dict[str, Any]:
    """Where a cited block comes from: file, section and the file's provenance."""
    return {
        "source_file": display_path(results_dir / file_name),
        "source_section": section,
        "source_provenance": source_provenance(report),
    }


def cite_handoff(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    """M5 and M8 from the S6 handoff results (golden founder scenarios and all cases)."""
    report = load_results(results_dir, HANDOFF_FILE)
    if report is None:
        return {
            "traceability": missing_block(results_dir, HANDOFF_FILE),
            "handoff": missing_block(results_dir, HANDOFF_FILE),
        }
    golden = report.get("golden_founder_scenarios") or {}
    configuration = report.get("configuration") or {}
    header = cited_header(results_dir, HANDOFF_FILE, report, "golden_founder_scenarios")
    settings = pick(configuration, ("screening", "n_paths", "golden_scenarios", "memo_writer"))
    return {
        "traceability": {**header, **pick(golden, HANDOFF_TRACE_KEYS)},
        "handoff": {
            **header,
            "settings": settings,
            "golden_founder_scenarios": pick(golden, HANDOFF_SUCCESS_KEYS),
            "all_cases": pick(report.get("all_cases") or {}, ALL_CASES_KEYS),
        },
    }


def cite_coverage(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    """M6 from the S1 coverage results: per metro, the mode, the minimum and each business type's coverage share."""
    report = load_results(results_dir, COVERAGE_FILE)
    if report is None:
        return missing_block(results_dir, COVERAGE_FILE)
    metros = {}
    for metro_id, metro in (report.get("metros") or {}).items():
        by_type = metro.get("by_business_type") or {}
        metros[metro_id] = {
            **pick(metro, METRO_KEYS),
            "by_business_type": {
                profile_id: pick(entry, ("coverage_share", "mode")) for profile_id, entry in sorted(by_type.items())
            },
        }
    return {
        **cited_header(results_dir, COVERAGE_FILE, report, "metros"),
        "definition": report.get("description"),
        "metros": metros,
    }


def cite_paths(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    """M7 from the S5 path results: the headline rates, the counts and the per-metro rates."""
    report = load_results(results_dir, PATHS_FILE)
    if report is None:
        return missing_block(results_dir, PATHS_FILE)
    headline = report.get("headline") or {}
    definitions = report.get("definitions") or {}
    by_metro = {
        metro_id: {
            "n_plans": entry.get("n_plans"),
            "status_counts": entry.get("status_counts"),
            "path_found_rate_among_plans_needing_a_change": entry.get("path_found_rate_among_plans_needing_a_change"),
        }
        for metro_id, entry in sorted((report.get("by_metro") or {}).items())
    }
    return {
        **cited_header(results_dir, PATHS_FILE, report, "headline"),
        "definition": definitions.get("path_found_rate"),
        "scope": report.get("scope"),
        "n_plans": headline.get("n_plans"),
        "status_counts_full": headline.get("status_counts_full"),
        "path_found_rate_full": headline.get("path_found_rate_full"),
        "path_found_and_program_compliant_rate_full": headline.get("path_found_and_program_compliant_rate_full"),
        "by_metro": by_metro,
        "real_world_accuracy": report.get("real_world_accuracy"),
    }
