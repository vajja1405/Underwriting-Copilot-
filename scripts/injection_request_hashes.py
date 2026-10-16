"""Write the sha256 of every request the full injection run (R4) will send, without calling a model (S2).

Usage:
    uv run python scripts/injection_request_hashes.py                   # -> results/injection/planned_requests.json
    uv run python scripts/injection_request_hashes.py --output <file>   # for example from another checkout

For each of the 240 planned calls (60 payloads x L0, L1, L2, then 30 benign questions x L1, L2) it builds the layer's
prompt exactly as scripts/eval_injection.py does and hashes it with rag.prompts.request_sha256, the hash every R4 row
records as request_sha256. tests/security/test_injection_eval.py rebuilds the hashes and compares them with the
committed file, so a change to a prompt, payload, question, pairing or normalization step shows up before R4 runs.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from eval_injection import CONTEXT_K, INJECTION_RESULTS_DIR, EvalSetup, build_jobs, load_setup
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.rag.frozen_hits import FROZEN_HITS_FILE, FROZEN_MANIFEST_FILE, KEY_FACTS_FILE
from uwcopilot.rag.prompts import prompt_sha256, prompt_template_sha256, request_sha256
from uwcopilot.security.layers import LAYER_NAMES, LAYERS, build_layer_prompt
from uwcopilot.security.payloads import PAYLOADS_FILE, PAYLOADS_MANIFEST_FILE

PLANNED_REQUESTS_FILE = INJECTION_RESULTS_DIR / "planned_requests.json"


def planned_request_hashes(setup: EvalSetup) -> dict[str, str]:
    """{job id: request sha256} for every planned R4 call, in run order."""
    hashes: dict[str, str] = {}
    for job in build_jobs(setup, LAYER_NAMES, include_utility=True):
        hits = job.record.context_hits(CONTEXT_K)
        system, messages, _flags = build_layer_prompt(job.layer, job.record.question, hits, job.applicant_text)
        hashes[job.job_id] = request_sha256(system, messages)
    return hashes


def combined_sha256(hashes: dict[str, str]) -> str:
    """One sha256 over the '<job id> <request sha256>' lines, in run order."""
    lines = "\n".join(f"{job_id} {digest}" for job_id, digest in hashes.items())
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def build_document(setup: EvalSetup) -> dict[str, Any]:
    """The planned-requests document (without the provenance block)."""
    hashes = planned_request_hashes(setup)
    layers = {
        layer: {
            "prompt_id": LAYERS[layer].prompt_id,
            "prompt_sha256": prompt_sha256(LAYERS[layer].prompt_id),
            "prompt_template_sha256": prompt_template_sha256(LAYERS[layer].prompt_id),
        }
        for layer in LAYER_NAMES
    }
    return {
        "generated_by": "scripts/injection_request_hashes.py",
        "what": "sha256 of the exact system prompt and messages of every planned R4 call; no model was called",
        "n_requests": len(hashes),
        "combined_sha256": combined_sha256(hashes),
        "combined_rule": "sha256 of the '<job id> <request sha256>' lines in run order",
        "layers": layers,
        "frozen_inputs": setup.inputs.hashes,
        "requests": hashes,
    }


def main(argv: list[str] | None = None) -> int:
    """Build the planned requests and write them. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=PLANNED_REQUESTS_FILE)
    args = parser.parse_args(argv)
    run = RunProvenance.start("scripts/injection_request_hashes.py")
    setup = load_setup(PAYLOADS_FILE, PAYLOADS_MANIFEST_FILE, FROZEN_HITS_FILE, FROZEN_MANIFEST_FILE, KEY_FACTS_FILE)
    document = build_document(setup)
    prompts = {layer["prompt_id"]: layer["prompt_sha256"] for layer in document["layers"].values()}
    document["provenance"] = run.finish(prompts=prompts, eval_sets=[PAYLOADS_FILE, FROZEN_HITS_FILE, KEY_FACTS_FILE])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {display_path(args.output)}: {document['n_requests']} requests, {document['combined_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
