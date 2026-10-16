"""generate_packets(): a seeded set of SYNTHETIC packets on disk, plus the manifest that versions the set.

Layout of the output folder:
    <out>/<packet_id>/packet.pdf   (absent in --json-only mode)
    <out>/<packet_id>/truth.json   (PacketTruth)
    <out>/manifest.json

Packet ids are PKT-C-001.. (credit) and PKT-I-001.. (insurance). Each packet's seed is derived from the master
seed and its id (common.packet_seed), so a packet does not change when the set grows. The test split is drawn
with a seeded shuffle within each line. test_truth_sha256 is the SHA-256 of the sorted test-split truth hashes
joined by newlines: the version of the evaluation set.
"""

import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path

from uwcopilot.docintel.synthetic.common import packet_seed, sub_seed
from uwcopilot.docintel.synthetic.packet import PDF_FILE, build_packet
from uwcopilot.docintel.truth import GENERATOR_VERSION, PacketTruth, truth_sha256

TRUTH_FILE = "truth.json"
MANIFEST_FILE = "manifest.json"


def packet_ids(line: str, count: int) -> list[str]:
    """PKT-C-001.. or PKT-I-001.."""
    letter = "C" if line == "credit" else "I"
    return [f"PKT-{letter}-{number:03d}" for number in range(1, count + 1)]


def test_split(ids: list[str], test_count: int, seed: int, line: str) -> set[str]:
    """The ids in the test split: a seeded shuffle within the line, first test_count ids."""
    order = list(ids)
    random.Random(sub_seed(seed, f"split|{line}")).shuffle(order)
    return set(order[:test_count])


def truth_json(truth: PacketTruth) -> str:
    """The truth as stable, indented JSON text."""
    return json.dumps(truth.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n"


def manifest_entry(truth: PacketTruth) -> dict:
    """One packet's manifest row."""
    return {
        "packet_id": truth.packet_id,
        "line": truth.line,
        "split": truth.split,
        "scenario": truth.scenario,
        "pdf_sha256": truth.pdf_sha256,
        "truth_sha256": truth_sha256(truth),
        "n_pages": len(truth.pages),
        "n_scanned_pages": sum(1 for page in truth.pages if page.scanned),
        "problem_types": [problem.problem_type for problem in truth.problems],
    }


def eval_set_hash(entries: list[dict]) -> str:
    """SHA-256 over the sorted truth hashes of the test split."""
    hashes = sorted(entry["truth_sha256"] for entry in entries if entry["split"] == "test")
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def generate_packets(
    out_dir: Path,
    n_credit: int,
    n_insurance: int,
    seed: int,
    test_credit: int,
    test_insurance: int,
    json_only: bool = False,
    created_with: str = "",
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Write every packet and the manifest; returns the manifest dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for line, count, test_count in (("credit", n_credit, test_credit), ("insurance", n_insurance, test_insurance)):
        ids = packet_ids(line, count)
        test_ids = test_split(ids, test_count, seed, line)
        for packet_id in ids:
            split = "test" if packet_id in test_ids else "dev"
            pdf, truth = build_packet(packet_id, line, packet_seed(seed, packet_id), split, draw=not json_only)
            folder = out_dir / packet_id
            folder.mkdir(parents=True, exist_ok=True)
            if pdf is not None:
                (folder / PDF_FILE).write_bytes(pdf)
            (folder / TRUTH_FILE).write_text(truth_json(truth), encoding="utf-8")
            entries.append(manifest_entry(truth))
            if progress is not None:
                progress(f"{packet_id} {truth.scenario}")
    manifest = {
        "data_label": "SYNTHETIC",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "created_with": created_with,
        "json_only": json_only,
        "counts": {
            "credit": n_credit,
            "insurance": n_insurance,
            "test_credit": test_credit,
            "test_insurance": test_insurance,
        },
        "packets": entries,
        "test_truth_sha256": eval_set_hash(entries),
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest
