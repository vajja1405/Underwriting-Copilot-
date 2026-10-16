"""End-to-end smoke test of Phase 2: generate 3 SYNTHETIC packets, run the pipeline, compute the metrics.

The three packets (two credit, one insurance; seed docintel_eval_fixtures.TINY_SEED, not the evaluation set) are
generated in a temporary folder. Each one goes through the rules pipeline and the hybrid pipeline; the hybrid's
FakeLLM answers from the oracle extraction (docintel_eval_fixtures.oracle_responder), so no network and no model
is used and the hybrid numbers only show that the plumbing works. The metrics are the ones scripts/eval_docintel.py
writes; the floors below are loose sanity floors for the plumbing, not reported results.
"""

import json
import shutil

import pytest
from docintel_eval_fixtures import oracle_responder, tiny_packet_set

from uwcopilot.docintel.evaluation import evaluate_packet
from uwcopilot.docintel.evaluation_summary import paired_f1_difference, summarize
from uwcopilot.docintel.extract_llm import make_hybrid_extractor
from uwcopilot.docintel.pipeline import run_packet
from uwcopilot.docintel.to_application import to_application
from uwcopilot.docintel.truth import load_truth
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.settings import LLMSettings


def needs_ocr(manifest: dict) -> bool:
    """True when any packet of the set has a scanned page."""
    return any(entry["n_scanned_pages"] for entry in manifest["packets"])


@pytest.fixture(scope="module")
def runs() -> dict[str, list[dict]]:
    """Evaluation records of the rules and hybrid runs on the three packets."""
    folder, manifest = tiny_packet_set()
    if needs_ocr(manifest) and shutil.which("tesseract") is None:
        pytest.skip("tesseract is not installed and the smoke packets have scanned pages")
    records: dict[str, list[dict]] = {"rules": [], "hybrid": []}
    for entry in manifest["packets"]:
        packet_folder = folder / entry["packet_id"]
        truth = load_truth(packet_folder / "truth.json")
        client = get_llm_client("fake", settings=LLMSettings(provider="fake"), fake_responses=oracle_responder(truth))
        options = {"rules": {}, "hybrid": {"extractor": "hybrid", "hybrid_extractor": make_hybrid_extractor(client)}}
        for name, kwargs in options.items():
            result = run_packet(packet_folder / "packet.pdf", entry["packet_id"], entry["line"], **kwargs)
            application, _, _ = to_application(result.extraction, result.checks)
            records[name].append(evaluate_packet(truth, result, application["documents_provided"]))
    return records


def test_rules_pipeline_metrics_on_three_packets(runs: dict[str, list[dict]]) -> None:
    metrics = summarize(runs["rules"])
    assert metrics["n_packets"] == 3 and metrics["packets_by_line"] == {"credit": 2, "insurance": 1}
    assert metrics["fields"]["overall"]["f1"]["value"] >= 0.9
    citations = metrics["citations"]
    assert citations["page_level_on_correct_values"]["value"] >= 0.9
    assert (
        citations["supported_all_values"]["value"] >= 0.9 and citations["line_level_on_correct_values"]["value"] >= 0.9
    )
    assert metrics["page_classification"]["all"]["value"] >= 0.9
    assert metrics["checklist"]["code_accuracy"]["denominator"] == 2 * 11 + 8
    # Outcomes, not only plumbing: 5 scorable problems (one of them an embedded instruction written in a technique
    # the regex patterns do not know), every detected contradiction matches a problem, every checklist is right.
    contradictions = metrics["contradictions"]
    assert contradictions["recall"]["denominator"] == 5 and contradictions["recall"]["numerator"] >= 4
    assert contradictions["precision"]["value"] == 1.0
    assert metrics["checklist"]["packet_exact_match"]["value"] == 1.0
    assert metrics["injections"]["recall"]["denominator"] == 1
    assert metrics["cost"] == {
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "usd_total": 0.0,
        "usd_per_packet_mean": 0.0,
    }
    assert set(metrics["latency"]["per_stage_ms"]) >= {"text_layer", "ocr", "extract", "checks", "total"}


def test_hybrid_pipeline_with_a_fake_llm(runs: dict[str, list[dict]]) -> None:
    metrics = summarize(runs["hybrid"])
    assert metrics["fields"]["overall"]["f1"]["value"] >= 0.9
    assert metrics["cost"]["llm_calls"] > 0 and metrics["cost"]["usd_total"] == 0.0
    assert all(record["extractor"] == "hybrid" for record in runs["hybrid"])
    difference = paired_f1_difference(runs["hybrid"], runs["rules"], "fields.all.all", "fields.all.all")
    assert difference["value"] is not None and difference["n_packets"] == 3


def test_metrics_are_plain_json(runs: dict[str, list[dict]]) -> None:
    for records in runs.values():
        json.dumps(summarize(records), allow_nan=False)
        for record in records:
            json.dumps(record, allow_nan=False)
