"""Tests for scripts/eval_docintel.py and scripts/run_docintel.py on the tiny SYNTHETIC packet set.

The packets come from docintel_eval_fixtures.tiny_packet_set() (seed 21, not the evaluation set). Runs use the
insurance test packet only, to stay fast; the forced-OCR comparison is either skipped or fed canned records, and
the hybrid run uses a FakeLLM that answers from the oracle (no network, no model).
"""

import json
import shutil
from pathlib import Path

import pytest
from docintel_eval_fixtures import oracle_responder, tiny_packet_set

import eval_docintel
import run_docintel
from uwcopilot.docintel.pipeline import PacketResult
from uwcopilot.docintel.truth import load_truth
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.settings import LLMSettings


def insurance_test_packet() -> tuple[Path, dict]:
    """(packets folder, manifest entry) of the insurance test packet; skipped if it needs OCR without tesseract."""
    folder, manifest = tiny_packet_set()
    entry = next(item for item in manifest["packets"] if item["line"] == "insurance")
    assert entry["split"] == "test"
    if entry["n_scanned_pages"] and shutil.which("tesseract") is None:
        pytest.skip("tesseract is not installed and the packet has scanned pages")
    return folder, entry


def out_args(tmp_path: Path) -> list[str]:
    """Output paths inside the test's temporary folder (never results/)."""
    return ["--out", str(tmp_path / "metrics.json"), "--per-packet-out", str(tmp_path / "per_packet.jsonl")]


def test_missing_packets_return_1(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert eval_docintel.main(["--packets-dir", str(tmp_path / "none"), *out_args(tmp_path)]) == 1
    assert "make packets" in capsys.readouterr().err
    assert not (tmp_path / "metrics.json").exists()


def test_a_changed_truth_file_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    folder, entry = insurance_test_packet()
    copy = tmp_path / "packets"
    shutil.copytree(folder, copy)
    truth_path = copy / entry["packet_id"] / "truth.json"
    data = json.loads(truth_path.read_text(encoding="utf-8"))
    data["scenario"] = data["scenario"] + " (edited)"
    truth_path.write_text(json.dumps(data), encoding="utf-8")
    assert (
        eval_docintel.main(["--packets-dir", str(copy), "--packet-ids", entry["packet_id"], *out_args(tmp_path)]) == 1
    )
    assert "does not match the manifest hash" in capsys.readouterr().err


def test_rules_run_writes_metrics_with_provenance(tmp_path: Path) -> None:
    folder, entry = insurance_test_packet()
    argv = ["--packets-dir", str(folder), "--packet-ids", entry["packet_id"], "--skip-forced-ocr", *out_args(tmp_path)]
    assert eval_docintel.main(argv) == 0
    report = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert report["data_label"] == "SYNTHETIC"
    assert report["evaluation_set"]["packet_ids"] == [entry["packet_id"]]
    assert report["evaluation_set"]["full_split"] is False
    # The tiny test set is not the committed evaluation set, so the on-disk manifest is what provenance hashes.
    assert report["evaluation_set"]["matches_committed_manifest"] is False
    assert "eval/packets/manifest.json" not in report["provenance"]["eval_sets"]
    assert report["rules"]["n_packets"] == 1
    assert report["comparisons"]["text_layer_vs_forced_ocr"] == "not measured (--skip-forced-ocr)"
    assert report["comparisons"]["rules_vs_hybrid"].startswith("not measured")
    provenance = report["provenance"]
    assert provenance["generated_by"] == "scripts/eval_docintel.py"
    assert provenance["data_snapshots"]["test_truth_sha256"] == tiny_packet_set()[1]["test_truth_sha256"]
    assert provenance["data_snapshots"]["bootstrap_seed"] == 42 and provenance["models"] == []
    lines = (tmp_path / "per_packet.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run"] for line in lines] == ["rules_auto"]


def test_forced_ocr_and_hybrid_comparisons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, entry = insurance_test_packet()
    truth = load_truth(folder / entry["packet_id"] / "truth.json")
    real_evaluate_all = eval_docintel.evaluate_all

    def evaluate_all(packets_dir: Path, entries: list[dict], ocr: str, workers: int) -> list[dict]:
        """The real rules run; the forced-OCR run reuses it (canned), so the test needs no full-page OCR."""
        records = real_evaluate_all(packets_dir, entries, "auto", 1)
        return [{**record, "ocr_mode": ocr} for record in records]

    def make_client(provider: str, model: str | None) -> object:
        """A FakeLLM that answers from the oracle (the --provider fake run)."""
        return get_llm_client("fake", settings=LLMSettings(provider="fake"), fake_responses=oracle_responder(truth))

    monkeypatch.setattr(eval_docintel, "evaluate_all", evaluate_all)
    monkeypatch.setattr(eval_docintel, "make_client", make_client)
    argv = ["--packets-dir", str(folder), "--packet-ids", entry["packet_id"], "--provider", "fake", *out_args(tmp_path)]
    assert eval_docintel.main(argv) == 0
    report = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    forced = report["comparisons"]["text_layer_vs_forced_ocr"]
    assert forced["f1_difference_text_layer_minus_forced_ocr"]["value"] == 0.0
    hybrid = report["comparisons"]["rules_vs_hybrid"]
    assert hybrid["hybrid"]["cost"]["llm_calls"] > 0
    assert report["run"]["extractors"] == ["rules", "hybrid"] and report["run"]["provider"] == "fake"
    assert report["provenance"]["models"][0]["provider"] == "fake"
    assert list(report["provenance"]["prompts"]) == ["docintel_extract_v1"]
    runs = [
        json.loads(line)["run"] for line in (tmp_path / "per_packet.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert runs == ["rules_auto", "rules_forced_ocr", "hybrid_auto"]


def test_run_docintel_prints_the_report_and_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    folder, entry = insurance_test_packet()
    out = tmp_path / "result.json"
    assert run_docintel.main(["--packets-dir", str(folder), "--packet", entry["packet_id"], "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"Packet {entry['packet_id']} (insurance, SYNTHETIC)" in printed
    for heading in ("Documents:", "Extracted values per section:", "Checks:", "Contradictions:"):
        assert heading in printed
    result = PacketResult.model_validate_json(out.read_text(encoding="utf-8"))
    assert result.packet_id == entry["packet_id"] and result.extractor == "rules"


@pytest.mark.parametrize(
    "argv",
    [["--packet", "PKT-X-001"], ["--pdf", "missing.pdf"], ["--packet", "PKT-C-999", "--packets-dir", "/nonexistent"]],
)
def test_run_docintel_bad_input_returns_1(argv: list[str], capsys: pytest.CaptureFixture) -> None:
    assert run_docintel.main(argv) == 1
    assert capsys.readouterr().err
