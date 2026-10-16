"""scripts/eval_evidence.py on a tiny SYNTHETIC packet folder (oracle mode only: no PDF is read, no OCR runs).

The packets are JSON-only generated packets (seed 4040, ids 401-440; evidence_case_helpers), written with a
placeholder packet.pdf whose hash goes into the manifest, so the script's hash checks run for real.
"""

import hashlib
import json
from pathlib import Path

from evidence_case_helpers import generated_truth

import eval_evidence
from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.docintel.truth import truth_sha256

PACKETS = (("PKT-C-401", "credit"), ("PKT-I-401", "insurance"))


def write_packets(folder: Path) -> Path:
    """A packets folder with two generated truths, placeholder PDFs and a manifest that names them as the test
    split."""
    entries = []
    for packet_id, line in PACKETS:
        truth = generated_truth(packet_id, line)
        (folder / packet_id).mkdir(parents=True)
        (folder / packet_id / "truth.json").write_text(truth.model_dump_json(), encoding="utf-8")
        pdf = b"placeholder: oracle mode never opens the PDF"
        (folder / packet_id / "packet.pdf").write_bytes(pdf)
        entries.append(
            {
                "packet_id": packet_id,
                "line": line,
                "split": "test",
                "truth_sha256": truth_sha256(truth),
                "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            }
        )
    hashes = sorted(entry["truth_sha256"] for entry in entries)
    manifest = {
        "generator_version": "synthetic-packets-v2",
        "seed": 4040,
        "test_truth_sha256": hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest(),
        "packets": entries,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def arguments(packets: Path, results: Path, per_packet: Path, *extra: str) -> list[str]:
    """Command-line arguments of an oracle-only run."""
    base = ["--packets-dir", str(packets), "--modes", "oracle", "--workers", "1"]
    return [*base, "--results", str(results), "--per-packet-out", str(per_packet), *extra]


def test_writes_only_its_section_with_provenance_and_per_packet_lines(tmp_path):
    packets = write_packets(tmp_path / "packets")
    results, per_packet = tmp_path / "evidence_metrics.json", tmp_path / "per_packet.jsonl"
    results.write_text(json.dumps({"simulator": {"results": {"kept": True}, "metadata": {}}}), encoding="utf-8")
    assert eval_evidence.main(arguments(packets, results, per_packet)) == 0
    written = json.loads(results.read_text(encoding="utf-8"))
    assert written["simulator"]["results"] == {"kept": True}  # the other section is untouched
    section = written["packets"]["results"]
    assert section["data_label"] == "SYNTHETIC"
    assert section["evaluation_set"]["n_packets"] == 2
    assert set(section["modes"]) == {"oracle"}
    assert section["paired_pipeline_minus_oracle"].startswith("not measured")
    assert section["modes"]["oracle"]["next_best_evidence_agreement_circular"]["label"].startswith("CIRCULAR")
    assert section["provenance"]["generated_by"] == "scripts/eval_evidence.py"
    assert section["provenance"]["data_snapshots"]["bootstrap_seed"] == 42
    lines = per_packet.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["packet_id"] for line in lines] == ["PKT-C-401", "PKT-I-401"]


def test_runs_are_reproducible(tmp_path):
    packets = write_packets(tmp_path / "packets")
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    eval_evidence.main(arguments(packets, first, tmp_path / "first.jsonl"))
    eval_evidence.main(arguments(packets, second, tmp_path / "second.jsonl"))
    modes = [json.loads(path.read_text())["packets"]["results"]["modes"] for path in (first, second)]
    assert modes[0] == modes[1]
    assert (tmp_path / "first.jsonl").read_text() == (tmp_path / "second.jsonl").read_text()


def test_merges_the_simulator_standalone_file_only_when_its_section_is_missing(tmp_path):
    packets = write_packets(tmp_path / "packets")
    results = tmp_path / "evidence_metrics.json"
    standalone = {"simulator": {"results": {"headline": "SYNTHETIC test"}, "metadata": {"git_commit": "x"}}}
    (tmp_path / "evidence_metrics_simulator.json").write_text(json.dumps(standalone), encoding="utf-8")
    assert eval_evidence.main(arguments(packets, results, tmp_path / "p.jsonl")) == 0
    merged = json.loads(results.read_text(encoding="utf-8"))["simulator"]["results"]
    assert merged["headline"] == "SYNTHETIC test"
    assert merged["merged_from"].endswith("evidence_metrics_simulator.json")
    own = {"simulator": {"results": {"headline": "written by the simulator"}, "metadata": {}}}
    results.write_text(json.dumps(own), encoding="utf-8")
    assert eval_evidence.main(arguments(packets, results, tmp_path / "p.jsonl")) == 0
    kept = json.loads(results.read_text(encoding="utf-8"))["simulator"]
    assert kept == own["simulator"]  # never overwritten


def test_refuses_dev_numbers_in_results_and_bad_hashes(tmp_path, capsys):
    packets = write_packets(tmp_path / "packets")
    dev_into_results = arguments(packets, RESULTS_DIR / "x.json", tmp_path / "p.jsonl", "--split", "dev")
    assert eval_evidence.main(dev_into_results) == 1
    assert "must not write into results/" in capsys.readouterr().err
    (packets / "PKT-C-401" / "packet.pdf").write_bytes(b"changed")
    assert eval_evidence.main(arguments(packets, tmp_path / "m.json", tmp_path / "p.jsonl")) == 1
    assert "does not match the manifest hash" in capsys.readouterr().err


def test_records_carry_the_mode_and_integer_counts(tmp_path):
    packets = write_packets(tmp_path / "packets")
    per_packet = tmp_path / "p.jsonl"
    eval_evidence.main(arguments(packets, tmp_path / "m.json", per_packet))
    for line in per_packet.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        assert record["mode"] == "oracle"
        assert record["data_label"] == "SYNTHETIC"
        assert all(isinstance(value, int) for value in record["counts"].values())
