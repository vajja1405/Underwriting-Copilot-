"""scripts/studio_metro_coverage.py: one row per metro x business type x factor, with a mode per metro."""

import json

import studio_metro_coverage as script
from uwcopilot.studio.data.fabric import required_factors
from uwcopilot.studio.profiles import load_all_profiles


def test_report_covers_every_metro_profile_and_factor(tmp_path) -> None:
    report = script.build_report(snapshots_dir=tmp_path)
    profiles = load_all_profiles()
    assert {item["metro_id"] for item in report["summary"]} == set(report["metros"])
    for block in report["metros"].values():
        assert set(block["by_business_type"]) == set(profiles)
        for profile_id, entry in block["by_business_type"].items():
            assert {row["factor"] for row in entry["factors"]} == set(required_factors(profiles[profile_id]))
            assert {row["factor"] for row in entry["metro_inputs"]} == {"commercial_rent", "local_wages", "prime_rate"}
    synthetic = report["metros"]["synthetic_metro"]
    assert synthetic["mode"] == "synthetic" and synthetic["tracts"] > 0
    houston = report["metros"]["houston_tx"]
    assert houston["mode"] == "limited" and houston["mode_reason"] == "no data snapshot"  # empty snapshots dir
    assert "Not financial" in report["disclaimer"]


def test_main_writes_json_with_provenance(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "SNAPSHOTS_DIR", tmp_path / "none")
    output = tmp_path / "coverage.json"
    assert script.main(["--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provenance"]["generated_by"] == "scripts/studio_metro_coverage.py"
    assert "synthetic_metro" in capsys.readouterr().out
