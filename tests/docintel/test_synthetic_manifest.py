"""Tests for generate_packets() and scripts/make_packets.py: files on disk, splits, manifest and eval-set hash."""

import hashlib
import json
from pathlib import Path

import make_packets
from uwcopilot.docintel.synthetic import generate_packets
from uwcopilot.docintel.truth import load_truth, truth_sha256


def test_json_only_set_writes_truths_and_manifest(tmp_path: Path) -> None:
    out = tmp_path / "packets"
    manifest = generate_packets(
        out, n_credit=4, n_insurance=3, seed=11, test_credit=2, test_insurance=1, json_only=True
    )
    assert manifest["data_label"] == "SYNTHETIC" and manifest["json_only"] is True
    entries = manifest["packets"]
    assert [entry["packet_id"] for entry in entries] == [
        "PKT-C-001", "PKT-C-002", "PKT-C-003", "PKT-C-004", "PKT-I-001", "PKT-I-002", "PKT-I-003",
    ]  # fmt: skip
    assert sum(1 for entry in entries if entry["split"] == "test" and entry["line"] == "credit") == 2
    assert sum(1 for entry in entries if entry["split"] == "test" and entry["line"] == "insurance") == 1
    for entry in entries:
        folder = out / entry["packet_id"]
        assert not (folder / "packet.pdf").exists()
        truth = load_truth(folder / "truth.json")
        assert truth_sha256(truth) == entry["truth_sha256"]
        assert truth.split == entry["split"] and entry["n_pages"] == len(truth.pages)
    test_hashes = sorted(entry["truth_sha256"] for entry in entries if entry["split"] == "test")
    assert manifest["test_truth_sha256"] == hashlib.sha256("\n".join(test_hashes).encode()).hexdigest()
    assert json.loads((out / "manifest.json").read_text()) == manifest


def test_drawn_set_writes_pdfs_matching_their_hashes(tmp_path: Path) -> None:
    out = tmp_path / "packets"
    manifest = generate_packets(out, n_credit=1, n_insurance=1, seed=5, test_credit=1, test_insurance=0)
    for entry in manifest["packets"]:
        pdf = (out / entry["packet_id"] / "packet.pdf").read_bytes()
        assert hashlib.sha256(pdf).hexdigest() == entry["pdf_sha256"]


def test_packets_do_not_change_when_the_set_grows(tmp_path: Path) -> None:
    small = generate_packets(tmp_path / "a", 2, 1, seed=3, test_credit=0, test_insurance=0, json_only=True)
    large = generate_packets(tmp_path / "b", 3, 2, seed=3, test_credit=0, test_insurance=0, json_only=True)
    by_id = {entry["packet_id"]: entry["truth_sha256"] for entry in large["packets"]}
    for entry in small["packets"]:
        assert by_id[entry["packet_id"]] == entry["truth_sha256"]


def test_script_json_only_run_leaves_the_eval_manifest_alone(tmp_path: Path, capsys) -> None:
    out = tmp_path / "packets"
    copy = tmp_path / "eval_manifest.json"
    code = make_packets.main(
        ["--out", str(out), "--n-credit", "2", "--n-insurance", "1", "--test-credit", "1", "--test-insurance", "1",
         "--json-only", "--quiet"]
    )  # fmt: skip
    assert code == 0
    assert "SYNTHETIC packets: 3" in capsys.readouterr().out
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["created_with"].startswith("scripts/make_packets.py --out ")
    assert str(tmp_path) not in manifest["created_with"]
    code = make_packets.main(
        ["--out", str(out), "--n-credit", "1", "--n-insurance", "1", "--test-credit", "0", "--test-insurance", "0",
         "--json-only", "--quiet", "--eval-manifest", str(copy)]
    )  # fmt: skip
    assert code == 0 and json.loads(copy.read_text())["counts"]["credit"] == 1


def test_script_rejects_a_test_split_larger_than_the_set(tmp_path: Path) -> None:
    assert make_packets.main(["--out", str(tmp_path), "--n-credit", "1", "--test-credit", "2", "--json-only"]) == 2
