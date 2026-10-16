"""Write results/studio_metro_coverage.json: which factors have data, per metro and business type (Studio S1).

    uv run python scripts/studio_metro_coverage.py          # or: make studio-coverage

For every metro config x business-type profile x required factor (the profile's weighted location factors plus the
data its demand formula reads, plus the metro-level simulator inputs), the report says whether data exists, its
status (DATA, SYNTHETIC, ASSUMPTION or UNKNOWN), its source, its freshness and the share of tracts it covers. Each
metro gets a mode (full, limited or synthetic) from studio.metros.models.metro_mode: a real metro runs in "limited
mode" when it has no snapshot or when the weighted coverage of any business type is below its threshold.

It reads only what is on disk (configs, snapshots written by `make studio-data`, the generated SYNTHETIC city); it
never calls the network. Every number in the file is computed here; nothing is typed in.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.studio import DISCLAIMER
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.data.fabric import (
    DataFabric,
    FactorCoverage,
    SnapshotFabric,
    SyntheticFabric,
    coverage_share,
    latest_snapshot,
    synthetic_city_for,
)
from uwcopilot.studio.data.keys import key_status
from uwcopilot.studio.data.paths import SNAPSHOTS_DIR
from uwcopilot.studio.metros.loader import METROS_DIR, list_metros, load_metro
from uwcopilot.studio.metros.models import MetroConfig, metro_mode
from uwcopilot.studio.profiles.factors import METRO_LEVEL_INPUTS
from uwcopilot.studio.profiles.loader import load_all_profiles
from uwcopilot.studio.profiles.models import Profile

OUTPUT = RESULTS_DIR / "studio_metro_coverage.json"
GENERATED_BY = "scripts/studio_metro_coverage.py"


def build_fabric(metro: MetroConfig, snapshots_dir: Path) -> DataFabric:
    """The fabric for a metro, reading snapshots from snapshots_dir (no live lookups)."""
    if metro.data_label == "SYNTHETIC":
        return SyntheticFabric(metro=metro, city=synthetic_city_for(metro))
    return SnapshotFabric(metro=metro, snapshot=latest_snapshot(metro.id, snapshots_dir, metro.snapshot_date))


def metro_input_rows(fabric: DataFabric, profile: Profile) -> list[dict[str, Any]]:
    """The simulator's metro-level inputs (rent, wages, prime rate) with status and reason."""
    rows = []
    for name, value in fabric.metro_inputs(profile).items():
        rows.append(
            {
                "factor": name,
                "status": value.status,
                "value": value.value,
                "low": value.low,
                "high": value.high,
                "unit": value.unit,
                "source": value.source.source_id if value.source else None,
                "freshness": value.source.vintage if value.source else None,
                "reason": value.reason,
                "assumptions": value.assumptions,
            }
        )
    return rows


def profile_block(fabric: DataFabric, profile: Profile, snapshot_date: Any) -> dict[str, Any]:
    """Coverage of one business type in one metro."""
    rows: list[FactorCoverage] = fabric.coverage(profile)
    share = coverage_share(rows)
    known_share = share if snapshot_date is not None else None
    mode, reason = metro_mode(fabric.metro, known_share, snapshot_date=snapshot_date)
    return {
        "coverage_share": round(share, 4),
        "mode": mode,
        "mode_reason": reason,
        "factors": [row.model_dump() for row in rows],
        "metro_inputs": metro_input_rows(fabric, profile),
    }


def metro_block(metro: MetroConfig, profiles: dict[str, Profile], snapshots_dir: Path) -> dict[str, Any]:
    """Coverage of every business type in one metro, plus the metro's mode."""
    fabric = build_fabric(metro, snapshots_dir)
    snapshot = fabric.snapshot if isinstance(fabric, SnapshotFabric) else None
    snapshot_date = snapshot.snapshot_date if snapshot else None
    by_profile = {profile_id: profile_block(fabric, profile, snapshot_date) for profile_id, profile in profiles.items()}
    shares = [block["coverage_share"] for block in by_profile.values()]
    if metro.data_label == "SYNTHETIC":
        mode, reason = metro_mode(metro, None)
    else:
        mode, reason = metro_mode(metro, min(shares) if snapshot else None, snapshot_date=snapshot_date)
    return {
        "name": metro.name,
        "data_label": metro.data_label,
        "verified_county_list": metro.verified,
        "cbsa_code": metro.cbsa_code,
        "counties": len(metro.counties),
        "tracts": len(fabric.tract_ids()),
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "snapshot_fingerprint": snapshot.fingerprint() if snapshot else None,
        "snapshot_steps": [
            {"name": step["name"], "status": step["status"], "detail": step["detail"]}
            for step in (snapshot.manifest.get("steps", []) if snapshot else [])
        ],
        "coverage_threshold": metro.coverage_threshold,
        "min_coverage_share": round(min(shares), 4) if shares else None,
        "mode": mode,
        "mode_reason": reason,
        "by_business_type": by_profile,
    }


def build_report(metros_dir: Path = METROS_DIR, snapshots_dir: Path = SNAPSHOTS_DIR) -> dict[str, Any]:
    """The report body (without the provenance block)."""
    profiles = load_all_profiles()
    metros = {
        metro_id: metro_block(load_metro(metro_id, metros_dir), profiles, snapshots_dir)
        for metro_id in list_metros(metros_dir)
    }
    return {
        "description": (
            "Data coverage per metro x business type x factor (Studio S1). status: DATA = public dataset value; "
            "SYNTHETIC = generated city; ASSUMPTION = the project author's labeled range; DATA_X_ASSUMPTION = a "
            "public value converted with a labeled assumption (QCEW weekly wage / assumed hours); UNKNOWN = no data. "
            "coverage_share = weighted share of a business type's required factors backed by data (DATA or "
            "SYNTHETIC) times the share of tracts covered; everything else counts as 0. live_per_site = the layer "
            "has no per-tract values but can be looked up live for one site (UNKNOWN for ranking tracts)."
        ),
        "disclaimer": DISCLAIMER,
        "metro_level_inputs": list(METRO_LEVEL_INPUTS),
        "api_keys_present": key_status(),
        "summary": [
            {
                "metro_id": metro_id,
                "data_label": block["data_label"],
                "mode": block["mode"],
                "min_coverage_share": block["min_coverage_share"],
                "snapshot_date": block["snapshot_date"],
                "tracts": block["tracts"],
            }
            for metro_id, block in metros.items()
        ],
        "metros": metros,
    }


def main(argv: list[str] | None = None) -> int:
    """Build the report, add provenance, write it and print the headline."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    report = build_report()
    snapshots = {
        metro_id: block["snapshot_fingerprint"]
        for metro_id, block in report["metros"].items()
        if block["data_label"] == "PUBLIC"
    }
    report["provenance"] = run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        data_snapshots={"studio_snapshots": snapshots},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    for item in report["summary"]:
        print(
            f"{item['metro_id']:<22} {item['data_label']:<9} mode={item['mode']:<9} "
            f"min_coverage={item['min_coverage_share']} tracts={item['tracts']} snapshot={item['snapshot_date']}"
        )
    print(f"Wrote {display_path(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
