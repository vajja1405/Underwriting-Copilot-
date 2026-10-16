"""Evaluate the Evidence-to-Action Engine on the SYNTHETIC test packets (Phase 4 F) -> results/evidence_metrics.json.

What it runs, on the test split of data/processed/packets (60 packets: 30 credit, 30 insurance; `make packets`):
1. oracle mode: the engine on the generator's perfect extraction (truth.oracle_extraction, oracle page text scanned
   for embedded instructions), which isolates the engine from extraction errors;
2. pipeline mode: the engine on the Phase 2 pipeline's output for the packet PDF (rules extractor, Tesseract OCR for
   scanned pages), end to end.
For each packet and mode it scores (src/uwcopilot/evidence/evaluation*.py): claim-status accuracy against the
expected-status table defined in evidence/evaluation.py, benign vs unsupported explanations (restated from the
matched contradictions), next-best-evidence agreement with the truth's resolving request (LABELLED CIRCULAR), the
share of packets where answering the top-ranked request with its latent truth changes the reference recommendation,
financial calculation agreement with truth.application_as_documented, and scenario / decision-stability counts.
Intervals: packet-level percentile bootstrap, 1,000 resamples, seed 42, plus exact (Clopper-Pearson) intervals.

Before anything runs, every packet's truth.json and packet.pdf must match the manifest hashes (eval_docintel's
loader). The expected claim-status table was written and finalized on the DEV split before this script was run on
the test split; its version and sha256 are written into the results. Error analysis belongs on the dev split
(--split dev with --results / --per-packet-out outside results/; the script refuses to write dev numbers there).

Output: section "packets" of results/evidence_metrics.json, written with uwcopilot.risk.results.update_results, which
holds an exclusive lock on results/evidence_metrics.json.lock and replaces ONLY this section, so the innovation S5
simulator (scripts/simulate_requests.py, section "simulator") and this script never overwrite each other. The
simulator also keeps a standalone copy, results/evidence_metrics_simulator.json; only when the shared file has no
"simulator" section is that copy merged in (marked merged_from). One JSON line per packet and mode goes to
results/evidence_per_packet.jsonl. The section carries a
provenance block (uwcopilot.governance.provenance.RunProvenance).

Everything is SYNTHETIC. Not claimable: real-world accuracy, that a VOI ranking is optimal or economic, or that
the circular agreement metric shows usefulness.

Usage: uv run python scripts/eval_evidence.py [--modes oracle pipeline] [--workers N] [--limit N]
       [--split dev --results PATH --per-packet-out PATH]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from eval_docintel import EvalSetError, committed_manifest_matches, load_packets
from uwcopilot.agent.evaluation import BOOTSTRAP_SEED, N_RESAMPLES
from uwcopilot.agent.policy import POLICY_VERSION
from uwcopilot.common.paths import DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.docintel.pipeline import run_packet
from uwcopilot.docintel.truth import load_truth
from uwcopilot.evidence.evaluation import EXPECTED_TABLE_VERSION, expected_table, expected_table_sha256
from uwcopilot.evidence.evaluation_packet import (
    MODES,
    ORACLE,
    PIPELINE,
    evaluate_case,
    oracle_case,
    pipeline_case,
    reference_result,
)
from uwcopilot.evidence.evaluation_summary import paired_ratio_difference, summarize_mode
from uwcopilot.evidence.record import ENGINE_VERSION
from uwcopilot.evidence.requests import catalog_version, templates_version
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.risk.results import read_results, update_results

GENERATED_BY = "scripts/eval_evidence.py"
SECTION = "packets"
SIMULATOR_SECTION = "simulator"
DEFAULT_PACKETS_DIR = DATA_DIR / "processed" / "packets"
DEFAULT_RESULTS = RESULTS_DIR / "evidence_metrics.json"
DEFAULT_PER_PACKET = RESULTS_DIR / "evidence_per_packet.jsonl"
SIMULATOR_SIDE_NAME = "evidence_metrics_simulator.json"
EVAL_MANIFEST = REPO_ROOT / "eval" / "packets" / "manifest.json"
DESCRIPTION = (
    "Evidence-to-Action Engine on SYNTHETIC document packets: claim statuses, benign vs unsupported explanations, "
    "next-best-evidence agreement (circular), top-request reveal, calculation agreement and decision stability, in "
    "oracle-extraction and pipeline-extraction modes. Simulated documents only; no real-world accuracy claim."
)
MODE_NOTES = {
    ORACLE: "engine on the generator's perfect extraction (truth.oracle_extraction) with the oracle page text scanned "
    "for embedded instructions (evidence.simulator_cases.prepare_case): isolates the engine",
    PIPELINE: "engine on the Phase 2 pipeline output of the packet PDF (rules extractor, Tesseract OCR for pages "
    "without a text layer): end to end",
}
DEVELOPMENT_NOTE = (
    "The expected claim-status table (src/uwcopilot/evidence/evaluation.py) and every metric definition were "
    "developed on the dev split (240 packets) and frozen before the first test-split run of this script. An "
    "earlier, interrupted attempt at this evaluation printed the test split's problem-type and resolving-request "
    "counts (truth composition only; the engine was not run on test packets) before the table was written. "
    "TABLE v2: after the first test run (table v1), an independent review found that the ledger showed "
    "calculations over imputed values as VERIFIED and that v1 copied that rule. The ledger was fixed and table v2 "
    "adds dependency-aware expectations written from the guideline formulas (NBC-4.1, NBC-4.4, NBC-2.1; HM-3.3 / "
    "HM-11.1 for a loss run whose summary disagrees with its lines). v2 was checked on the dev split (oracle mode) "
    "before this test-split re-run; the v1 test numbers were seen before v2 was written, so the test split is no "
    "longer untouched for the table."
)
NOT_CLAIMABLE = [
    "real-world accuracy of the engine or of the extraction (SYNTHETIC packets written by the project author)",
    "that the next-best-evidence ranking is useful because it agrees with the generator (the metric is circular)",
    "that the value-of-information score is optimal or an economic model",
    "anything about risk-model estimates: model PD and expected loss are UNKNOWN by design in this phase",
]


class OutputGuardError(RuntimeError):
    """A dev-split run tried to write into results/."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packets-dir", type=Path, default=DEFAULT_PACKETS_DIR)
    parser.add_argument("--split", default="test", choices=("test", "dev"))
    parser.add_argument("--modes", nargs="+", default=list(MODES), choices=MODES)
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N packets (a smoke run)")
    parser.add_argument("--packet-ids", nargs="+", default=None, help="evaluate only these packets of the split")
    parser.add_argument("--workers", type=int, default=2, help="packets evaluated in parallel processes")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--per-packet-out", type=Path, default=DEFAULT_PER_PACKET)
    return parser.parse_args(argv)


def check_outputs(args: argparse.Namespace) -> None:
    """Refuse to write dev-split numbers into results/ (the test split is the only reported measurement)."""
    if args.split == "test":
        return
    for path in (args.results, args.per_packet_out):
        if Path(path).resolve().is_relative_to(RESULTS_DIR.resolve()):
            raise OutputGuardError(f"--split dev must not write into results/ ({display_path(path)}).")


def evaluate_entry(packets_dir: Path, entry: dict[str, Any], modes: list[str]) -> dict[str, dict[str, Any]]:
    """Every requested mode of one packet: {mode: record}."""
    folder = packets_dir / entry["packet_id"]
    truth = load_truth(folder / "truth.json")
    reference = reference_result(truth)
    records = {}
    if ORACLE in modes:
        records[ORACLE] = evaluate_case(oracle_case(truth), ORACLE, reference)
    if PIPELINE in modes:
        result = run_packet(folder / "packet.pdf", entry["packet_id"], entry["line"])
        records[PIPELINE] = evaluate_case(pipeline_case(truth, result), PIPELINE, reference)
        records[PIPELINE]["tesseract_version"] = result.tesseract_version
    return records


def evaluate_all(
    packets_dir: Path, entries: list[dict[str, Any]], modes: list[str], workers: int
) -> dict[str, list[dict[str, Any]]]:
    """Every packet in every mode ({mode: records in packet-id order}), in parallel processes when workers > 1."""
    started = time.perf_counter()
    if workers <= 1:
        results = []
        for number, entry in enumerate(entries, start=1):
            results.append(evaluate_entry(packets_dir, entry, modes))
            print(f"  {number}/{len(entries)} {entry['packet_id']} ({time.perf_counter() - started:.0f} s)")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(evaluate_entry, packets_dir, entry, modes) for entry in entries]
            results = [future.result() for future in futures]
        print(f"  {len(entries)} packets with {workers} workers ({time.perf_counter() - started:.0f} s)")
    return {mode: [result[mode] for result in results] for mode in modes}


def paired_section(records: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | str:
    """Pipeline minus oracle on the same packets, or a note when one mode was not run."""
    if ORACLE not in records or PIPELINE not in records:
        return "not measured (both modes are needed)"
    pipeline, oracle = records[PIPELINE], records[ORACLE]
    return {
        "note": "pipeline minus oracle on the same packets; paired packet-level bootstrap (1,000 resamples, seed 42)",
        "claim_status_accuracy": paired_ratio_difference(pipeline, oracle, "claims.correct", "claims.total"),
        "open_expected_accuracy": paired_ratio_difference(
            pipeline, oracle, "claims.open_expected.correct", "claims.open_expected.total"
        ),
        "calculation_agreement": paired_ratio_difference(pipeline, oracle, "calcs.all.agree", "calcs.all.total"),
    }


def tesseract_versions(records: dict[str, list[dict[str, Any]]]) -> list[str] | str:
    """Distinct Tesseract versions of the pipeline run (or why there are none)."""
    if PIPELINE not in records:
        return "not used (pipeline mode not run)"
    versions = sorted({record["tesseract_version"] for record in records[PIPELINE] if record["tesseract_version"]})
    return versions or "not used (no page needed OCR)"


def build_report(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    entries: list[dict[str, Any]],
    records: dict[str, list[dict[str, Any]]],
    wall_seconds: float,
) -> dict[str, Any]:
    """The "packets" section (without its provenance block)."""
    return {
        "data_label": "SYNTHETIC",
        "description": DESCRIPTION,
        "evaluation_set": {
            "split": args.split,
            "n_packets": len(entries),
            "full_split": args.limit is None and args.packet_ids is None,
            "generator_version": manifest["generator_version"],
            "test_truth_sha256": manifest["test_truth_sha256"],
            "matches_committed_manifest": committed_manifest_matches(manifest),
            "packet_ids": [entry["packet_id"] for entry in entries],
        },
        "protocol": {
            "bootstrap": {
                "resamples": N_RESAMPLES,
                "seed": BOOTSTRAP_SEED,
                "interval": "percentile 95%",
                "unit": "packet",
            },
            "averaging": "micro (counts summed over packets)",
            "modes": {mode: MODE_NOTES[mode] for mode in args.modes},
            "expected_claim_table": {
                "version": EXPECTED_TABLE_VERSION,
                "sha256": expected_table_sha256(),
                "table": expected_table(),
            },
            "development_note": DEVELOPMENT_NOTE,
            "versions": {
                "engine": ENGINE_VERSION,
                "policy": POLICY_VERSION,
                "evidence_request_catalog": catalog_version(),
                "resolution_templates": templates_version(),
                "risk_model": "not evaluated (no Phase 1 model applies to these SYNTHETIC commercial packets)",
            },
            "documentation": "src/uwcopilot/evidence/evaluation.py, evaluation_packet.py, evaluation_requests.py, "
            "evaluation_calcs.py, evaluation_summary.py",
        },
        "modes": {mode: summarize_mode(records[mode]) for mode in args.modes},
        "paired_pipeline_minus_oracle": paired_section(records),
        "not_claimable": NOT_CLAIMABLE,
        "run": {"modes": list(args.modes), "workers": args.workers, "wall_seconds": round(wall_seconds, 1)},
    }


def write_per_packet(path: Path, records: dict[str, list[dict[str, Any]]]) -> None:
    """One JSON line per packet and mode (labelled SYNTHETIC), through a temporary file and a rename. NaN is
    refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for mode_records in records.values():
            for record in mode_records:
                handle.write(json.dumps({"data_label": "SYNTHETIC", **record}, allow_nan=False) + "\n")
    os.replace(temporary, path)


def simulator_payload(content: Any) -> Any:
    """The simulator's results from its standalone file: {"simulator": {"results": ...}}, {"results": ...} or the
    bare results."""
    entry = content.get(SIMULATOR_SECTION, content) if isinstance(content, dict) else content
    return entry.get("results", entry) if isinstance(entry, dict) else entry


def merge_simulator_file(results_path: Path) -> bool:
    """When the results file has no "simulator" section but the simulator's standalone file (next to it) exists,
    copy its results in; True when merged. A section the simulator wrote itself is never touched."""
    side_file = results_path.with_name(SIMULATOR_SIDE_NAME)
    if SIMULATOR_SECTION in read_results(results_path) or not side_file.is_file():
        return False
    payload = simulator_payload(json.loads(side_file.read_text(encoding="utf-8")))
    update_results(results_path, SIMULATOR_SECTION, {"merged_from": display_path(side_file), **payload})
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation; returns 0, or 1 with a message when the inputs are missing or do not match."""
    args = parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    started = time.perf_counter()
    try:
        check_outputs(args)
        manifest, entries = load_packets(args.packets_dir, args.split, args.limit, args.packet_ids)
    except (EvalSetError, OutputGuardError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Evaluating the engine on {len(entries)} SYNTHETIC {args.split} packets, modes: {', '.join(args.modes)}")
    records = evaluate_all(args.packets_dir, entries, list(args.modes), args.workers)
    report = build_report(args, manifest, entries, records, time.perf_counter() - started)
    matches = committed_manifest_matches(manifest)
    report["provenance"] = run.finish(
        prompts={},
        eval_sets=[EVAL_MANIFEST if matches else args.packets_dir / "manifest.json"],
        models=[],
        data_snapshots={
            "generator_version": manifest["generator_version"],
            "test_truth_sha256": manifest["test_truth_sha256"],
            "packet_seed": manifest["seed"],
            "bootstrap_seed": BOOTSTRAP_SEED,
            "expected_claim_table_sha256": expected_table_sha256(),
            "tesseract_version": tesseract_versions(records),
        },
        corpus_checksum="not applicable (no retrieval corpus is used)",
    )
    update_results(args.results, SECTION, report)
    write_per_packet(args.per_packet_out, records)
    merged = merge_simulator_file(args.results) if args.split == "test" else False
    note = " (merged the simulator side file)" if merged else ""
    print(f"Wrote section '{SECTION}' of {display_path(args.results)}{note} and {display_path(args.per_packet_out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
