"""Generate the SYNTHETIC startup credit example applications into eval/startup_applications/.

Run with `make startup-applications`. Needs no downloaded data: the examples (a clean file and one file per startup
program rule, spread over the six Fundproof business types) are built from the hand-written scenarios in
uwcopilot.agent.synthetic_startup with a fixed seed, so every run writes the same bytes. Owner names use obviously
fictional surnames and are not checked against any sanctions list (no list is read here).

Writes one APP-S-*.json per application and manifest.json with counts and a SHA-256 checksum of every file.
"""

import argparse
import hashlib
import json
from pathlib import Path

from uwcopilot.agent.synthetic_startup import (
    AS_OF_DATE,
    DEFAULT_SEED,
    GENERATOR_VERSION,
    generate_startup_applications,
)
from uwcopilot.common.paths import REPO_ROOT

DEFAULT_OUT_DIR = REPO_ROOT / "eval" / "startup_applications"
RECOMMENDATIONS = ("approve", "refer", "decline_for_review", "request_more_information")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def application_text(application: dict) -> str:
    """The JSON text written for one application (stable formatting)."""
    return json.dumps(application, indent=2, ensure_ascii=False) + "\n"


def write_applications(applications: list[dict], out_dir: Path) -> dict[str, str]:
    """Write every application, remove stale APP-S-*.json files, and return {file name: sha256}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {f"{application['application_id']}.json" for application in applications}
    for stale in out_dir.glob("APP-S-*.json"):
        if stale.name not in wanted:
            stale.unlink()
    hashes = {}
    for application in applications:
        text = application_text(application)
        name = f"{application['application_id']}.json"
        (out_dir / name).write_text(text, encoding="utf-8")
        hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashes


def build_manifest(applications: list[dict], seed: int, file_hashes: dict[str, str]) -> dict:
    """Counts and checksums describing the generated set."""
    truths = [application["ground_truth"] for application in applications]
    combined = hashlib.sha256("".join(f"{name}:{digest}\n" for name, digest in sorted(file_hashes.items())).encode())
    return {
        "data_label": "SYNTHETIC",
        "description": "SYNTHETIC startup term loan applications (line startup_credit, schema startup-application-v1) "
        "with designed ground truth for the SYNTHETIC Northwind startup program "
        "(corpus/programs/northwind_startup_program.md): a clean file and one file per program rule, spread over the "
        "six Fundproof business types. Every amount and assumption is a made-up example input, not a benchmark; no "
        "file carries simulator output.",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of_date": AS_OF_DATE.isoformat(),
        "count": len(applications),
        "business_types": {
            name: sum(1 for application in applications if application["applicant"]["business_type"] == name)
            for name in sorted({application["applicant"]["business_type"] for application in applications})
        },
        "contains_injection": sum(1 for truth in truths if truth["contains_injection"]),
        "expected_recommendations": {
            name: sum(1 for truth in truths if truth["expected_recommendation"] == name) for name in RECOMMENDATIONS
        },
        "files": dict(sorted(file_hashes.items())),
        "applications_sha256": combined.hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    """Generate, write and summarize the startup application set."""
    args = parse_args(argv)
    applications = generate_startup_applications(seed=args.seed)
    hashes = write_applications(applications, args.out_dir)
    manifest = build_manifest(applications, args.seed, hashes)
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(applications)} SYNTHETIC startup applications to {args.out_dir}")
    print(f"Expected recommendations: {manifest['expected_recommendations']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
