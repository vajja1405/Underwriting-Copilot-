"""Write results/studio_handoff_metrics.json: the startup line end to end (Founder Studio S6, package + handoff).

    uv run python scripts/eval_studio_handoff.py                         # full sanctions lists (make data first)
    uv run python scripts/eval_studio_handoff.py --screening ground_truth  # designed screening, no lists needed

Two SYNTHETIC sets (src/uwcopilot/studio/package/evaluation.py says what is measured on each):
1. the 35 startup applications of eval/startup_applications (designed labels): the existing agent graph to its human
   checkpoint with the template memo; agreement of its reference recommendation and exact rule set with the labels;
2. the 36 golden founder scenarios: package, PDF (audited), application and handoff().
For both: checkpoint reached, decisions recorded without a human (must be 0), memo checks and unsupported numbers.
Rates carry percentile bootstrap 95% intervals over cases (1,000 resamples, seed 42). Every number in the file is
computed by this run; nothing here is a claim about real lending outcomes. The memo writer is the deterministic
template (FAKE LLM - test mode): no language model is called and no money is spent.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from eval_agent import ground_truth_screening_handler
from uwcopilot.agent.applications import load_applications
from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES
from uwcopilot.agent.graph import GRAPH_VERSION
from uwcopilot.agent.memo import PROMPT_VERSION
from uwcopilot.agent.policy_startup import STARTUP_POLICY_VERSION
from uwcopilot.agent.tools import close_tool_handlers, screening_handler
from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import RAW_DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.evidence.startup_requests import STARTUP_REQUESTS_FILE, startup_catalog_version
from uwcopilot.governance.provenance import RunProvenance
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.narrative.founder_ledger import FOUNDER_LEDGER_VERSION
from uwcopilot.studio.narrative.golden import GOLDEN_SEED, build_golden_scenario, golden_specs
from uwcopilot.studio.narrative.prompts import all_prompt_hashes
from uwcopilot.studio.package.constants import PACKAGE_BUILDER_VERSION, PACKAGE_SCHEMA_VERSION
from uwcopilot.studio.package.evaluation import (
    application_row,
    golden_row,
    summarize_all,
    summarize_applications,
    summarize_golden,
)
from uwcopilot.studio.package.handoff import HANDOFF_VERSION
from uwcopilot.studio.package.sba import SBA_RECORD_FILE, sba_record
from uwcopilot.tools.registry import ToolHandler

STARTUP_DIR = REPO_ROOT / "eval" / "startup_applications"
OUTPUT = RESULTS_DIR / "studio_handoff_metrics.json"
SDN_FILE = RAW_DATA_DIR / "ofac" / "sdn.csv"
GENERATED_BY = "scripts/eval_studio_handoff.py"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Measure the startup line: package + handoff (Studio S6).")
    parser.add_argument("--screening", choices=["full", "ground_truth"], default="full")
    parser.add_argument("--limit-applications", type=int, default=None)
    parser.add_argument("--limit-golden", type=int, default=None)
    parser.add_argument("--n-paths", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser.parse_args(argv)


def handlers_for(screening: str, applications: list) -> dict[str, ToolHandler] | None:
    """The screening handler: the full downloaded lists, or the designed outcomes (None when the lists are missing)."""
    if screening == "full":
        if not SDN_FILE.exists():
            return None
        return {"screen_entity": screening_handler()}
    return {"screen_entity": ground_truth_screening_handler(applications, None)}


def screening_description(screening: str) -> str:
    """How screening was done, in words."""
    if screening == "full":
        return "full OFAC SDN + OpenSanctions lists (uwcopilot.kyc.screening default matcher)"
    return "designed ground truth: planted watchlist-derived owners of the eval files match, everyone else is clear"


def main(argv: list[str] | None = None) -> int:
    """Run both sets and write the results file."""
    args = parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    applications = load_applications(STARTUP_DIR)[: args.limit_applications]
    handlers = handlers_for(args.screening, applications)
    if handlers is None:
        print(f"{SDN_FILE} is missing: run `make data` or use --screening ground_truth.", file=sys.stderr)
        return 1
    specs = golden_specs()[: args.limit_golden]
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as log_dir:
            application_rows = []
            for application in applications:
                application_rows.append(application_row(application, handlers, Path(log_dir)))
                print(f"{application.application_id} {application_rows[-1]['reference_recommendation']}", flush=True)
            golden_rows = []
            for metro_id, profile_id in specs:
                scenario = build_golden_scenario(metro_id, profile_id, n_paths=args.n_paths)
                golden_rows.append(golden_row(scenario, handlers, Path(log_dir)))
                row = golden_rows[-1]
                print(
                    f"{row['scenario_id']} pages={row['pdf_pages']} untraced={len(row['pdf_untraced'])} "
                    f"checkpoint={row['reached_checkpoint']} {row['reference_recommendation']}",
                    flush=True,
                )
    finally:
        close_tool_handlers(handlers)
    report: dict[str, Any] = {
        "generated_by": GENERATED_BY,
        "data_label": SYNTHETIC_LABEL,
        "disclaimer": DISCLAIMER,
        "simulation_label": SIMULATION_LABEL,
        "status": "measured (deterministic template memo; no language model was called)",
        "configuration": {
            "screening": screening_description(args.screening),
            "golden_seed": GOLDEN_SEED,
            "n_paths": args.n_paths,
            "golden_scenarios": len(specs),
            "startup_applications": len(applications),
            "memo_writer": "deterministic template memo (FAKE LLM - test mode)",
            "bootstrap": {"resamples": N_RESAMPLES, "seed": BOOTSTRAP_SEED, "confidence": 0.95, "unit": "case"},
            "runtime_s": round(time.perf_counter() - started, 1),
        },
        "versions": {
            "package_schema": PACKAGE_SCHEMA_VERSION,
            "package_builder": PACKAGE_BUILDER_VERSION,
            "handoff": HANDOFF_VERSION,
            "founder_ledger": FOUNDER_LEDGER_VERSION,
            "startup_evidence_requests": startup_catalog_version(),
            "graph": GRAPH_VERSION,
            "policy": STARTUP_POLICY_VERSION,
            "memo_prompt": PROMPT_VERSION,
        },
        "startup_applications": summarize_applications(application_rows),
        "golden_founder_scenarios": summarize_golden(golden_rows),
        "all_cases": summarize_all(application_rows, golden_rows),
        "per_case": {"startup_applications": application_rows, "golden_founder_scenarios": golden_rows},
        "provenance": run.finish(
            prompts=all_prompt_hashes(),
            eval_sets=sorted(STARTUP_DIR.glob("*.json")) + [STARTUP_REQUESTS_FILE, SBA_RECORD_FILE],
            data_snapshots={
                "sba_business_plan_page_sha256": sba_record()["sha256"],
                "ofac_sdn_csv_sha256": sha256_of_file(SDN_FILE) if args.screening == "full" else None,
            },
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    summary = report["all_cases"]
    print(
        f"Wrote {args.output}: {summary['cases']} cases, checkpoint {summary['reached_human_checkpoint']['value']}, "
        f"decisions without a human {summary['decisions_recorded_without_a_human']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
