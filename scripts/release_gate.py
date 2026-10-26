"""Release gate (Phase 6 D): block a release when a critical metric regresses against the reviewed baseline.

Usage:
    uv run python scripts/release_gate.py                    # judge every row, print the table, exit 1 on FAIL
    uv run python scripts/release_gate.py --write-results    # also write section "release_gate" of
                                                             # results/governance_metrics.json
    uv run python scripts/release_gate.py --update-baseline  # copy the gated results files to results/baseline/

What it does (src/uwcopilot/governance/release_gate.py has the rules):
1. Reads the `gate:` section of config/release_gate.yaml: one row per critical number (file, JSON path of the value,
   JSON path of its CI bound, which of the two is compared, direction, kind, tolerance, whether it is required).
2. Reads each number from the latest results/*.json and from the reviewed baseline results/baseline/*.json, and gives
   each row PASS, FAIL, MISSING or NOT_MEASURED. It recomputes nothing and calls no model or network service. Before a
   measured number is compared, the row's preconditions must hold (`gate.preconditions` per results file, and the
   row's own): a partial or subset run, another evaluation set or another served model makes the row FAIL instead.
3. Checks the baseline copies against results/baseline/baseline_manifest.json (a copy edited by hand blocks).
4. Lists results files whose provenance says they were written from a dirty working tree, as warnings only.
5. Exits 0 when the verdict is PASS, 1 when any row is FAIL or MISSING or the baseline is not trustworthy, and 2 when
   the configuration is invalid.

--update-baseline copies the results files named by the rows (byte for byte) into results/baseline/ and writes the
manifest (SHA-256 of each copy, the commit, and whether each source equals its committed version at HEAD). Make the
baseline from committed results, review the diff, then commit results/baseline/: the committed copies are the reviewed
baseline. It does not run the gate.

The gate reads results of evaluations on SYNTHETIC and public data. It is a demonstration of a release gate, not a
compliance programme.
"""

import argparse
import sys
from pathlib import Path

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.release_gate import (
    DEFAULT_BASELINE_DIR,
    DEFAULT_CONFIG_PATH,
    DEFAULT_RESULTS_DIR,
    MANIFEST_NAME,
    GateReport,
    RowResult,
    evaluate_gate,
    load_gate_settings,
    update_baseline,
)
from uwcopilot.risk.results import update_results

SECTION = "release_gate"
DEFAULT_OUTPUT = RESULTS_DIR / "governance_metrics.json"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Compare results/*.json with the reviewed baseline; exit 1 on FAIL.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="YAML file with a `gate:` section")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write-results",
        action="store_true",
        help=f'also write section "{SECTION}" of --output (do not commit it; results are regenerated at one commit)',
    )
    mode.add_argument(
        "--update-baseline",
        action="store_true",
        help="copy the gated results files into --baseline-dir and write its manifest (then review and commit)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="results file for --write-results")
    return parser.parse_args(argv)


def format_reading(result: RowResult, which: str) -> str:
    """The compared number from the current or baseline file, or its state when it has no value."""
    reading = getattr(result, which)[result.row.gate_on]
    if reading.value is not None:
        return f"{reading.value:.6g}"
    return reading.state


def print_report(report: GateReport) -> None:
    """The gate as a plain-text table, then the warnings, the baseline problems and the verdict."""
    manifest = report.baseline_manifest or {}
    commit = str(manifest.get("git_commit") or "unknown")[:12]
    print(f"Release gate: config/release_gate.yaml (gate), baseline results/baseline (made at commit {commit})")
    print(f"{'STATUS':<13} {'ROW':<38} {'COMPARED':<9} {'CURRENT':>12} {'BASELINE':>12}  REASON")
    for result in report.results:
        print(
            f"{result.status:<13} {result.row.id:<38} {result.row.gate_on:<9} "
            f"{format_reading(result, 'current'):>12} {format_reading(result, 'baseline'):>12}  {result.reason}"
        )
    if report.warnings:
        print("\nWarnings (not failing today: most committed results files were written from a dirty working tree):")
        for warning in report.warnings:
            commit = str(warning["git_commit"] or "no commit recorded")[:12]
            print(f"  - {warning['file']} [{warning['where']}, {commit}]: {warning['message']}")
            print(f"    rows: {', '.join(warning['rows'])}")
    if report.manifest_problem or report.baseline_problems:
        print("\nBaseline problems (blocking):")
        for problem in [report.manifest_problem, *report.baseline_problems]:
            if problem:
                print(f"  - {problem}")
    counts = report.counts()
    print("\nSummary: " + ", ".join(f"{count} {status}" for status, count in counts.items()))
    print(f"Verdict: {report.verdict}" + ("" if report.verdict == "PASS" else " (FAIL and MISSING rows block)"))


def run_update_baseline(args: argparse.Namespace) -> int:
    """Copy the gated results files into the baseline folder and say what changed."""
    settings = load_gate_settings(args.config)
    manifest, removed = update_baseline(settings, args.results_dir, args.baseline_dir)
    for name, entry in manifest["files"].items():
        print(f"copied {name} (sha256 {entry['sha256'][:12]}..., equals HEAD: {entry['source_matches_head_commit']})")
        if entry["source_matches_head_commit"] is not True:
            print(f"  warning: {name} is not the committed version; commit the results before making the baseline")
    for name in manifest["absent_when_made"]:
        print(f"absent {name}: no baseline value (rows reading it are NOT_MEASURED or MISSING)")
    for name in removed:
        print(f"removed the old baseline copy of {name} (the results file is absent now)")
    print(f"wrote {args.baseline_dir / MANIFEST_NAME}. Review the diff, then commit {args.baseline_dir}.")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    """Run the gate (or update the baseline) and return the exit code."""
    args = parse_args(argv)
    try:
        settings = load_gate_settings(args.config)
    except (OSError, ValueError) as error:
        print(f"release gate: invalid configuration {args.config}: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if args.update_baseline:
        return run_update_baseline(args)
    report = evaluate_gate(settings, args.results_dir, args.baseline_dir)
    print_report(report)
    if args.write_results:
        update_results(args.output, SECTION, report.to_payload(args.config))
        print(f"wrote section {SECTION!r} of {args.output}")
    return EXIT_PASS if report.verdict == "PASS" else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
