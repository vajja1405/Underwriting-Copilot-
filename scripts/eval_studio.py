"""Write results/studio_business_metrics.json: Founder Studio S12 business metrics on the golden SYNTHETIC scenarios.

    uv run python scripts/eval_studio.py                              # the 36 golden scenarios (make data first)
    uv run python scripts/eval_studio.py --limit 6 --skip-handoff     # a quick development run

docs/studio/s12_baseline.md defines the baseline B0 (a plan made without SiteIQ, the simulation and the ledger) and
every metric; it was committed before this script existed. Each golden scenario runs end to end with the existing
S2-S6 code (src/uwcopilot/studio/business_metrics/scenario_run.py): scenario, package, PDF, audit, and the handoff to
the lender copilot as a consistency check. Measured here: time to a complete package (machine-dependent wall clock,
not a user time), evidence coverage of the package and of B0 (rules S and E, the founder figures). Cited from the
earlier results files with their provenance, not recomputed: printed-number traceability and agent handoff success
(S6), data-coverage score by metro (S1), path-found rate (S5). LLM cost is 0 by construction (template narrative and
template memo, checked per package); compute cost and the usability test are "not measured".
Everything is SYNTHETIC; nothing here is a claim about real founders, lenders, loan outcomes or savings.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES
from uwcopilot.agent.tools import close_tool_handlers, screening_handler
from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import RAW_DATA_DIR, RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL
from uwcopilot.studio.business_metrics.cited import (
    COVERAGE_FILE,
    HANDOFF_FILE,
    PATHS_FILE,
    cite_coverage,
    cite_handoff,
    cite_paths,
)
from uwcopilot.studio.business_metrics.report import metric_blocks
from uwcopilot.studio.business_metrics.scenario_run import run_scenario
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.narrative.golden import GOLDEN_SEED, MONTHS, golden_specs
from uwcopilot.studio.narrative.prompts import all_prompt_hashes
from uwcopilot.studio.package.constants import PACKAGE_BUILDER_VERSION, PACKAGE_SCHEMA_VERSION
from uwcopilot.studio.package.handoff import HANDOFF_VERSION
from uwcopilot.tools.registry import ToolHandler

OUTPUT = RESULTS_DIR / "studio_business_metrics.json"
SDN_FILE = RAW_DATA_DIR / "ofac" / "sdn.csv"
DEFINITIONS_FILE = "docs/studio/s12_baseline.md"
GENERATED_BY = "scripts/eval_studio.py"
BASELINE_SUMMARY = (
    "B0: a plan made without SiteIQ, the simulation and the ledger, built from the same SYNTHETIC inputs and "
    "documents: the founder's typed figures, the untyped inputs at their most likely value, and years 1-3 revenue, "
    "NOI, debt service and DSCR of the one-forecast run. A figure carries a source only when it is a founder figure "
    "every document about it agrees with. A construction, not a sample of real plans (docs/studio/s12_baseline.md)."
)
SCOPE = (
    "The golden SYNTHETIC founder scenarios of S4/S6: 6 business types x (5 real metros + the SYNTHETIC metro). The "
    "S12 prompt's 30 scenarios are the real-metro subset, reported separately where a metric is measured here."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Founder Studio S12 business metrics on the golden scenarios.")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N golden scenarios")
    parser.add_argument("--n-paths", type=int, default=1000, help="simulated paths per scenario (golden default)")
    parser.add_argument("--skip-handoff", action="store_true", help="do not re-run the handoff consistency check")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where the cited results files are")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args(argv)


def handlers_for(skip_handoff: bool) -> dict[str, ToolHandler] | None:
    """The full-list screening handler of the handoff (as S6 measured it), or None when the handoff is skipped."""
    if skip_handoff:
        return None
    return {"screen_entity": screening_handler()}


def cited_hashes(results_dir: Path) -> dict[str, str]:
    """{path: sha256} of the cited results files that exist (pins exactly which versions were read)."""
    hashes = {}
    for name in (HANDOFF_FILE, COVERAGE_FILE, PATHS_FILE):
        path = results_dir / name
        if path.is_file():
            hashes[display_path(path)] = sha256_of_file(path)
    return hashes


def run_all(
    specs: list[tuple[str, str]], n_paths: int, handlers: dict[str, ToolHandler] | None
) -> list[dict[str, Any]]:
    """Every scenario end to end, printing one progress line each."""
    rows = []
    with tempfile.TemporaryDirectory() as log_dir:
        for metro_id, profile_id in specs:
            row = run_scenario(metro_id, profile_id, n_paths=n_paths, handlers=handlers, log_dir=Path(log_dir))
            rows.append(row)
            print(
                f"{row['scenario_id']} package={row['seconds']['package_total']}s pages={row['pdf_pages']} "
                f"complete={row['complete_package']} studio={row['studio']['sourced']}/{row['studio']['figures']} "
                f"b0={row['baseline']['sourced']}/{row['baseline']['figures']}",
                flush=True,
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    """Run the golden scenarios, cite the earlier results and write the results file."""
    args = parse_args(argv)
    if not args.skip_handoff and not SDN_FILE.exists():
        print(f"{SDN_FILE} is missing: run `make data` or pass --skip-handoff.", file=sys.stderr)
        return 1
    specs = golden_specs()[: args.limit]
    if not specs:
        print("no golden scenario selected (--limit must be at least 1)", file=sys.stderr)
        return 2
    run = RunProvenance.start(GENERATED_BY)
    handlers = handlers_for(args.skip_handoff)
    started = time.perf_counter()
    try:
        rows = run_all(specs, args.n_paths, handlers)
    finally:
        if handlers is not None:
            close_tool_handlers(handlers)
    runtime_s = round(time.perf_counter() - started, 1)
    cited = {
        "handoff": cite_handoff(args.results_dir),
        "coverage": cite_coverage(args.results_dir),
        "paths": cite_paths(args.results_dir),
    }
    report: dict[str, Any] = {
        "title": "Founder Studio S12: business metrics of the golden SYNTHETIC founder scenarios",
        "generated_by": GENERATED_BY,
        "data_label": SYNTHETIC_LABEL,
        "simulation_label": SIMULATION_LABEL,
        "disclaimer": DISCLAIMER,
        "definitions": f"{DEFINITIONS_FILE} (committed before the first run)",
        "scope": SCOPE,
        "baseline": BASELINE_SUMMARY,
        "configuration": {
            "golden_seed": GOLDEN_SEED,
            "scenarios": len(specs),
            "real_metro_scenarios": sum(1 for metro_id, _ in specs if metro_id != "synthetic_metro"),
            "n_paths": args.n_paths,
            "months": MONTHS,
            "handoff_consistency_check": "skipped" if args.skip_handoff else "full OFAC SDN + OpenSanctions lists",
            "narrative_and_memo": "template narrative and deterministic template memo (FAKE LLM - test mode)",
            "bootstrap": {"resamples": N_RESAMPLES, "seed": BOOTSTRAP_SEED, "confidence": 0.95, "unit": "scenario"},
            "runtime_s": runtime_s,
            "versions": {
                "package_schema": PACKAGE_SCHEMA_VERSION,
                "package_builder": PACKAGE_BUILDER_VERSION,
                "handoff": HANDOFF_VERSION,
            },
        },
        **metric_blocks(rows, cited),
        "per_scenario": rows,
        "provenance": run.finish(
            prompts=all_prompt_hashes(),
            eval_sets=[DEFINITIONS_FILE],
            data_snapshots={
                "cited_results_sha256": cited_hashes(args.results_dir),
                "ofac_sdn_csv_sha256": None if args.skip_handoff else sha256_of_file(SDN_FILE),
            },
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    headline = report["headline"]
    print(
        f"Wrote {args.output}: {len(rows)} scenarios in {runtime_s}s; median package "
        f"{headline['time_to_complete_package_median_s']['value']}s; rule S studio "
        f"{headline['evidence_coverage_rule_s']['studio']['value']} vs B0 "
        f"{headline['evidence_coverage_rule_s']['baseline_b0']['value']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
