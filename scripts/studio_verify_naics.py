"""Check every business-type profile's NAICS code against the BLS QCEW industry titles file (Studio S1).

    uv run python scripts/studio_verify_naics.py          # or: make studio-naics

What it does:
1. Downloads https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv through the polite, caching client,
   saves it under data/studio/reference/ (gitignored) and logs its SHA-256 in data/studio/checksums.jsonl.
2. Looks every profile code up (studio.profiles.naics_check) and writes results/studio_naics_check.json: per code,
   confirmed / title_differs / older_vintage_only / not_a_naics_title / not_in_list, the published title, and the
   file's URL, retrieval time and SHA-256.

It never edits the profiles. A profile code gets `verified: true` (with a `verification` block copied from this
file's output) only where the status is "confirmed"; tests/studio/test_profiles_naics.py checks the YAMLs against a
recorded excerpt of the same file. Nothing is typed from memory.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from uwcopilot.common.files import append_checksum_log
from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.paths import CHECKSUM_LOG, REFERENCE_DIR
from uwcopilot.studio.profiles.loader import load_all_profiles
from uwcopilot.studio.profiles.naics_check import (
    INDUSTRY_TITLES_URL,
    LIST_NAME,
    PUBLISHER,
    check_profiles,
    parse_industry_titles,
)

OUTPUT = RESULTS_DIR / "studio_naics_check.json"
GENERATED_BY = "scripts/studio_verify_naics.py"


def main(argv: list[str] | None = None) -> int:
    """Download (or reuse) the list, check the codes, write the results file; exit 1 if any code is unconfirmed."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    fetched = PoliteClient().get(INDUSTRY_TITLES_URL)
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    saved = REFERENCE_DIR / INDUSTRY_TITLES_URL.rsplit("/", 1)[1]
    write_atomic(saved, fetched.body)
    append_checksum_log(
        CHECKSUM_LOG,
        "bls_qcew_industry_titles",
        saved,
        INDUSTRY_TITLES_URL,
        "reused_cache" if fetched.from_cache else "downloaded",
    )
    checks = check_profiles(load_all_profiles(), parse_industry_titles(fetched.text()))
    source = {
        "publisher": PUBLISHER,
        "list_name": LIST_NAME,
        "url": INDUSTRY_TITLES_URL,
        "retrieved_at_utc": fetched.retrieved_at.isoformat(timespec="seconds"),
        "sha256": fetched.sha256,
    }
    report = {
        "description": (
            "Each business-type profile's NAICS codes looked up in the BLS QCEW industry titles file. 'confirmed' = "
            "the code has the current 'NAICS ' title prefix and the same title as the profile. It checks that the "
            "code exists with that title, not that it is the right code for the business type (an author's choice)."
        ),
        "source": source,
        "codes": [
            {
                "profile_id": item.profile_id,
                "code": item.code,
                "profile_title": item.profile_title,
                "status": item.status,
                "published_title": item.published_title,
                "detail": item.detail,
            }
            for item in checks
        ],
        "confirmed": sum(item.confirmed for item in checks),
        "total": len(checks),
    }
    report["provenance"] = run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        data_snapshots={"bls_qcew_industry_titles": {"url": INDUSTRY_TITLES_URL, "sha256": fetched.sha256}},
    )
    report["written_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    for item in checks:
        print(f"{item.profile_id:<18} {item.code:<7} {item.status:<20} {item.detail}")
    print(f"{report['confirmed']} of {report['total']} codes confirmed; wrote {display_path(args.output)}")
    return 0 if report["confirmed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
