"""Evaluate the document pipeline on the SYNTHETIC test packets (Phase 2) -> results/docintel_metrics.json.

What it runs, on the test split of data/processed/packets (60 packets: 30 credit, 30 insurance; `make packets`):
1. rules extractor, OCR only for pages without a text layer ("rules" section: every metric of
   uwcopilot.docintel.evaluation_summary.summarize, with packet-level bootstrap 95% intervals);
2. rules extractor with EVERY page sent to Tesseract (--skip-forced-ocr turns this off): compared with run 1 on
   the values printed on clean (text-layer) pages, so both read exactly the same pages;
3. hybrid extractor (an LLM reads the key-value documents) only with --provider; otherwise the rules-vs-hybrid
   comparison is written as "not measured". --provider ollama holds the one-job-at-a-time Ollama lock.

Before anything runs, every test packet's truth.json and packet.pdf must match the manifest hashes, and the
manifest's test_truth_sha256 must be the hash of its test truths (the evaluation-set version). Per-packet records
go to results/docintel_per_packet.jsonl (one line per packet and run). Nothing here is tuned on the test split.

The results carry a provenance block (uwcopilot.governance.provenance): git commit and dirty flag, time, the
eval-set manifest hash, package versions, load average, and in data_snapshots the Tesseract version, generator
version, test_truth_sha256 and seeds. Latency depends on the machine and on --workers; both are recorded, and each
run's latency block carries the machine's load average before and after that run with a caveat. --max-load N
refuses to start when the 1-minute load average is above N (use it to measure latency on a quiet machine).

Error analysis belongs on the DEV split (--split dev, with --out/--per-packet-out pointing outside results/); the
test split is for the final measurement only.

Usage: uv run python scripts/eval_docintel.py [--split dev] [--limit N] [--skip-forced-ocr] [--max-load N]
       [--provider ollama --model M]
"""

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import CONFIG_DIR, DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.docintel.evaluation import evaluate_packet
from uwcopilot.docintel.evaluation_summary import (
    BOOTSTRAP_SEED,
    N_RESAMPLES,
    paired_f1_difference,
    prf_metric,
    ratio_metric,
    summarize,
)
from uwcopilot.docintel.pipeline import HybridExtractor, run_packet
from uwcopilot.docintel.to_application import to_application
from uwcopilot.docintel.truth import load_truth, truth_sha256
from uwcopilot.governance.provenance import RunProvenance, display_path

GENERATED_BY = "scripts/eval_docintel.py"
DEFAULT_PACKETS_DIR = DATA_DIR / "processed" / "packets"
DEFAULT_OUT = RESULTS_DIR / "docintel_metrics.json"
DEFAULT_PER_PACKET = RESULTS_DIR / "docintel_per_packet.jsonl"
EVAL_MANIFEST = REPO_ROOT / "eval" / "packets" / "manifest.json"
GENERATION_CONFIG = CONFIG_DIR / "generation_eval.yaml"
NOT_MEASURED_HYBRID = (
    "not measured (no --provider run has been done: this run used the rules extractor only; "
    "`scripts/eval_docintel.py --provider ollama` would measure it)"
)
LATENCY_CAVEAT = (
    "Wall-clock times on the machine named in provenance.environment. They depend on the machine, on other work "
    "running at the same time (see machine_load: the 1/5/15-minute load average before and after this run) and on "
    "the number of worker processes; they are not a stable product figure."
)
NOT_EXERCISED_NOTE = (
    "check_status_counts shows checks that never pass or fail on these SYNTHETIC packets (always not_evaluated): "
    "their detection is not measured here. Known: tax_return_vs_pnl (NBC-9.1) compares only a P&L for the same "
    "calendar year as a tax return, and every generated P&L is trailing twelve months ending mid-year."
)
COMPOSITION_NOTE = (
    "Composition-confounded: scanned pages hold a different mix of document types than clean pages (see "
    "rules.fields.by_condition.<condition>.by_document_type), so this difference mixes scan degradation with that "
    "mix. text_layer_vs_forced_ocr reads the same pages both ways and isolates the OCR effect."
)
EVAL_SET_NOTE = (
    "Generator synthetic-packets-v2, master seed 20261013. The v1 packets (seed 42) and their test split were "
    "inspected during the Phase 2 review, so v2 was generated with a new seed; error analysis for v2 was done on "
    "the dev split only."
)
NOT_IMPLEMENTED = [
    "NBC-6.2 name and date of birth on the photo ID against the application and the credit report: the application "
    "and credit report schemas carry no date of birth, so this match is not checked",
]


class EvalSetError(RuntimeError):
    """The packets on disk do not match the manifest (missing files or different hashes)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packets-dir", type=Path, default=DEFAULT_PACKETS_DIR)
    parser.add_argument("--split", default="test", choices=("test", "dev"))
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N packets (a smoke run)")
    parser.add_argument("--packet-ids", nargs="+", default=None, help="evaluate only these packets of the split")
    parser.add_argument("--skip-forced-ocr", action="store_true", help="do not run the forced-OCR comparison")
    parser.add_argument("--provider", default=None, help="LLM provider for the hybrid run (e.g. ollama)")
    parser.add_argument("--model", default=None, help="model for the hybrid run (default: the provider's)")
    parser.add_argument("--workers", type=int, default=1, help="parallel packets in the rules run (latency!)")
    parser.add_argument("--ocr-workers", type=int, default=2, help="parallel packets in the forced-OCR run")
    parser.add_argument("--max-load", type=float, default=None, help="refuse to start above this 1-minute load")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--per-packet-out", type=Path, default=DEFAULT_PER_PACKET)
    return parser.parse_args(argv)


def eval_set_hash(entries: list[dict[str, Any]]) -> str:
    """SHA-256 over the sorted truth hashes of the test entries (the generator's definition)."""
    hashes = sorted(entry["truth_sha256"] for entry in entries if entry["split"] == "test")
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def load_packets(
    packets_dir: Path, split: str, limit: int | None, packet_ids: list[str] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The manifest and its entries of one split (sorted by packet id, optionally only packet_ids), after
    checking files and hashes."""
    manifest_path = packets_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EvalSetError(f"Missing {display_path(manifest_path)}. Run `make packets` first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if eval_set_hash(manifest["packets"]) != manifest["test_truth_sha256"]:
        raise EvalSetError("The manifest's test_truth_sha256 does not match its own test entries.")
    entries = sorted(
        (item for item in manifest["packets"] if item["split"] == split), key=lambda item: item["packet_id"]
    )
    if packet_ids is not None:
        entries = [item for item in entries if item["packet_id"] in set(packet_ids)]
        if not entries:
            raise EvalSetError(f"None of {packet_ids} is in the {split} split.")
    for entry in entries[:limit]:
        folder = packets_dir / entry["packet_id"]
        if not (folder / "truth.json").is_file() or not (folder / "packet.pdf").is_file():
            raise EvalSetError(f"Missing files of {entry['packet_id']} in {display_path(folder)}. Run `make packets`.")
        if truth_sha256(load_truth(folder / "truth.json")) != entry["truth_sha256"]:
            raise EvalSetError(f"{entry['packet_id']}: truth.json does not match the manifest hash.")
        if sha256_of_file(folder / "packet.pdf") != entry["pdf_sha256"]:
            raise EvalSetError(f"{entry['packet_id']}: packet.pdf does not match the manifest hash.")
    return manifest, entries[:limit]


def committed_manifest_matches(manifest: dict[str, Any]) -> bool | None:
    """True when eval/packets/manifest.json (the committed copy) names the same test set as the packets on disk,
    False when it names another one, None when the committed copy does not exist."""
    if not EVAL_MANIFEST.is_file():
        return None
    committed = json.loads(EVAL_MANIFEST.read_text(encoding="utf-8"))
    return committed.get("test_truth_sha256") == manifest["test_truth_sha256"]


def evaluate_one(
    packets_dir: Path, entry: dict[str, Any], ocr: str, hybrid: HybridExtractor | None = None
) -> dict[str, Any]:
    """Run the pipeline on one packet and return its evaluation record (plus the Tesseract version used)."""
    folder = packets_dir / entry["packet_id"]
    truth = load_truth(folder / "truth.json")
    extractor = "rules" if hybrid is None else "hybrid"
    result = run_packet(folder / "packet.pdf", entry["packet_id"], entry["line"], extractor, ocr, hybrid)
    application, _provenance, _unknowns = to_application(result.extraction, result.checks)
    record = evaluate_packet(truth, result, list(application.get("documents_provided", [])))
    record["tesseract_version"] = result.tesseract_version
    if hybrid is not None:
        record["llm_usage"] = result.llm_usage
    return record


def load_average() -> list[float] | None:
    """The 1/5/15-minute load average, or None where the platform has none."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return None


def with_machine_load(summary: dict[str, Any], before: list[float] | None, after: list[float] | None) -> dict:
    """The run summary with the load average around the run and the latency caveat added to its latency block."""
    summary["latency"]["machine_load"] = {"load_average_before": before, "load_average_after": after}
    summary["latency"]["caveat"] = LATENCY_CAVEAT
    return summary


def evaluate_all(packets_dir: Path, entries: list[dict[str, Any]], ocr: str, workers: int) -> list[dict[str, Any]]:
    """Evaluate every packet with the rules extractor (in parallel processes when workers > 1), in id order."""
    started = time.perf_counter()
    if workers <= 1:
        records = []
        for number, entry in enumerate(entries, start=1):
            records.append(evaluate_one(packets_dir, entry, ocr))
            print(f"  [{ocr}] {number}/{len(entries)} {entry['packet_id']} ({time.perf_counter() - started:.0f} s)")
        return records
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(evaluate_one, packets_dir, entry, ocr) for entry in entries]
        records = [future.result() for future in futures]
    print(f"  [{ocr}] {len(entries)} packets with {workers} workers ({time.perf_counter() - started:.0f} s)")
    return records


def license_of(model: str) -> str | None:
    """The model's license summary from config/generation_eval.yaml, or None when it is not listed there."""
    if not GENERATION_CONFIG.is_file():
        return None
    document = yaml.safe_load(GENERATION_CONFIG.read_text(encoding="utf-8")) or {}
    return ((document.get("models") or {}).get(model) or {}).get("license")


def model_record(client: Any) -> dict[str, Any]:
    """The provenance record of the hybrid run's model (digest and runtime for Ollama)."""
    current = client
    while current is not None:
        if hasattr(current, "model_record"):
            return current.model_record(license_of(client.model))
        current = getattr(current, "inner", None)
    return {"provider": client.provider, "model": client.model}


def make_client(provider: str, model: str | None) -> Any:
    """The LLM client of the hybrid run (cached and under the run's cost cap)."""
    from uwcopilot.llm.factory import get_llm_client  # imported here: the rules run needs no LLM settings

    return get_llm_client(provider, model=model)


def evaluate_hybrid(
    packets_dir: Path, entries: list[dict[str, Any]], provider: str, model: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """The hybrid run (one packet at a time), its model record and the extractor prompt hash."""
    from uwcopilot.docintel.extract_llm import make_hybrid_extractor, prompt_sha256

    client = make_client(provider, model)
    extractor = make_hybrid_extractor(client)
    records = []
    for number, entry in enumerate(entries, start=1):
        records.append(evaluate_one(packets_dir, entry, "auto", extractor))
        print(f"  [hybrid {provider}] {number}/{len(entries)} {entry['packet_id']}")
    return records, model_record(client), prompt_sha256()


def clean_page_comparison(text_layer: list[dict[str, Any]], forced: list[dict[str, Any]]) -> dict[str, Any]:
    """Text layer vs forced Tesseract OCR on the same clean pages: fields, page types, OCR time per page."""
    forced_summary = summarize(forced)
    return {
        "scope": "values printed on clean (text-layer) pages and the page types of those pages, same packets",
        "text_layer": {
            "fields": prf_metric(text_layer, "fields.clean.all"),
            "page_classification": ratio_metric(text_layer, "pages.clean.correct", "pages.clean.total"),
        },
        "forced_ocr": {
            "fields": prf_metric(forced, "fields.clean.all"),
            "page_classification": ratio_metric(forced, "pages.clean.correct", "pages.clean.total"),
            "ocr_ms_per_page": forced_summary["latency"]["ocr_ms_per_page"],
        },
        "f1_difference_text_layer_minus_forced_ocr": paired_f1_difference(
            text_layer, forced, "fields.clean.all", "fields.clean.all"
        ),
    }


def build_comparisons(
    rules: list[dict[str, Any]], forced: list[dict[str, Any]] | None, hybrid: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Clean vs scanned pages, text layer vs forced OCR, rules vs hybrid (or 'not measured' with the reason)."""
    comparisons: dict[str, Any] = {
        "clean_vs_scanned_pages": {
            "note": COMPOSITION_NOTE,
            "clean": prf_metric(rules, "fields.clean.all"),
            "scanned": prf_metric(rules, "fields.scanned.all"),
            "f1_difference_clean_minus_scanned": paired_f1_difference(
                rules, rules, "fields.clean.all", "fields.scanned.all"
            ),
        }
    }
    if forced is None:
        comparisons["text_layer_vs_forced_ocr"] = "not measured (--skip-forced-ocr)"
    else:
        comparisons["text_layer_vs_forced_ocr"] = clean_page_comparison(rules, forced)
    if hybrid is None:
        comparisons["rules_vs_hybrid"] = NOT_MEASURED_HYBRID
    else:
        comparisons["rules_vs_hybrid"] = {
            "hybrid": summarize(hybrid),
            "f1_difference_hybrid_minus_rules": paired_f1_difference(hybrid, rules, "fields.all.all", "fields.all.all"),
        }
    return comparisons


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON through a temporary file and a rename, so a crash never leaves half a file. NaN is refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_per_packet(path: Path, runs: dict[str, list[dict[str, Any]] | None]) -> None:
    """One JSON line per packet and run ("run": rules_auto, rules_forced_ocr or hybrid_auto)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for name, records in runs.items():
            for record in records or []:
                handle.write(json.dumps({"run": name, **record}, allow_nan=False) + "\n")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation; returns 0, or 1 with a message when the inputs are missing or do not match."""
    args = parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    started = time.perf_counter()
    try:
        manifest, entries = load_packets(args.packets_dir, args.split, args.limit, args.packet_ids)
    except EvalSetError as error:
        print(error, file=sys.stderr)
        return 1
    current_load = load_average()
    if args.max_load is not None and current_load is not None and current_load[0] > args.max_load:
        print(f"The 1-minute load average {current_load[0]} is above --max-load {args.max_load}.", file=sys.stderr)
        return 1
    matches_committed = committed_manifest_matches(manifest)
    if matches_committed is False:
        print(
            "WARNING: the packets on disk are not the test set of eval/packets/manifest.json "
            "(different test_truth_sha256); the provenance records the on-disk manifest instead.",
            file=sys.stderr,
        )
    print(f"Evaluating {len(entries)} SYNTHETIC {args.split} packets from {display_path(args.packets_dir)}")
    rules_load = load_average()
    rules = evaluate_all(args.packets_dir, entries, "auto", args.workers)
    rules_load_after = load_average()
    forced = None if args.skip_forced_ocr else evaluate_all(args.packets_dir, entries, "force", args.ocr_workers)
    forced_load_after = load_average()
    hybrid, models, prompts = None, [], {}
    if args.provider:
        from uwcopilot.llm.ollama_queue import ollama_queue_lock

        if args.provider == "ollama":
            with ollama_queue_lock(f"{GENERATED_BY} --provider ollama"):
                hybrid, record, digest = evaluate_hybrid(args.packets_dir, entries, args.provider, args.model)
        else:
            hybrid, record, digest = evaluate_hybrid(args.packets_dir, entries, args.provider, args.model)
        models, prompts = [record], {"docintel_extract_v1": digest}
    report = {
        "data_label": "SYNTHETIC",
        "description": "Document pipeline on SYNTHETIC packets: fields, citations, pages, checklist, "
        "contradictions, injections, latency and cost. Simulated documents only; no real-world accuracy claim.",
        "evaluation_set": {
            "note": EVAL_SET_NOTE,
            "split": args.split,
            "n_packets": len(entries),
            "full_split": args.limit is None and args.packet_ids is None,
            "generator_version": manifest["generator_version"],
            "test_truth_sha256": manifest["test_truth_sha256"],
            "matches_committed_manifest": matches_committed,
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
            "documentation": "src/uwcopilot/docintel/evaluation.py, evaluation_problems.py, evaluation_summary.py",
        },
        "run": {
            "extractors": ["rules"] + (["hybrid"] if hybrid is not None else []),
            "provider": args.provider,
            "model": None if hybrid is None else models[0].get("model"),
            "workers_rules_run": args.workers,
            "workers_forced_ocr_run": None if forced is None else args.ocr_workers,
            "wall_seconds": round(time.perf_counter() - started, 1),
        },
        "rules": with_machine_load(summarize(rules), rules_load, rules_load_after),
        "comparisons": build_comparisons(rules, forced, hybrid),
        "coverage_notes": {"checks_not_exercised": NOT_EXERCISED_NOTE, "not_implemented": NOT_IMPLEMENTED},
    }
    if forced is not None:
        report["comparisons"]["text_layer_vs_forced_ocr"]["forced_ocr"]["machine_load"] = {
            "load_average_before": rules_load_after,
            "load_average_after": forced_load_after,
        }
    versions = sorted({record["tesseract_version"] for record in rules + (forced or []) if record["tesseract_version"]})
    report["provenance"] = run.finish(
        prompts=prompts,
        eval_sets=[EVAL_MANIFEST if matches_committed else args.packets_dir / "manifest.json"],
        models=models,
        data_snapshots={
            "tesseract_version": versions[0] if len(versions) == 1 else (versions or "not used (no page needed OCR)"),
            "generator_version": manifest["generator_version"],
            "test_truth_sha256": manifest["test_truth_sha256"],
            "packet_seed": manifest["seed"],
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        corpus_checksum="not applicable (no retrieval corpus is used)",
    )
    write_json(args.out, report)
    write_per_packet(args.per_packet_out, {"rules_auto": rules, "rules_forced_ocr": forced, "hybrid_auto": hybrid})
    print(f"Wrote {display_path(args.out)} and {display_path(args.per_packet_out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
