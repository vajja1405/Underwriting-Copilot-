"""Shared helpers for the Studio S12 business-metrics tests, all offline and SYNTHETIC.

Hand-made results files to cite, and a configured set of screening lists built from small SYNTHETIC files (no
data/raw, no sanctions list, no network).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from uwcopilot.kyc import screening
from uwcopilot.kyc.watchlist import Watchlist, snapshot_for, watchlist_from_records

COMMIT = "a" * 40
PROVENANCE = {"generated_by": "scripts/x.py", "generated_at_utc": "2026-10-01T00:00:00+00:00", "git_sha": COMMIT}
RATE = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 2, "k": 2, "wilson_ci95": [0.34, 1.0]}
# The list files of each family UWC_SCREENING_LISTS can name, under their real file names (kyc.watchlist defaults).
LIST_FILE_NAMES: dict[str, tuple[str, ...]] = {"ofac": ("sdn.csv", "alt.csv"), "opensanctions": ("targets.simple.csv",)}
# One SYNTHETIC watchlist entry; the fake list files only have to be hashed, never parsed.
LIST_RECORDS: list[dict[str, Any]] = [
    {
        "list_entry_id": "SYN-LIST-1",
        "name": "Synthetic Listed Person",
        "aliases": [],
        "entity_type": "person",
        "countries": [],
        "programs": [],
        "source": "OFAC",
        "list_name": "SYNTHETIC test list",
    }
]


def write(folder: Path, name: str, report: dict[str, Any]) -> None:
    """Write one hand-made results file."""
    (folder / name).write_text(json.dumps(report), encoding="utf-8")


def handoff_report() -> dict[str, Any]:
    """A minimal S6 handoff results file."""
    return {
        "configuration": {"screening": "designed", "n_paths": 200, "golden_scenarios": 2, "memo_writer": "template"},
        "golden_founder_scenarios": {
            "cases": 2,
            "reached_human_checkpoint": RATE,
            "decisions_recorded_without_a_human": 0,
            "package_numbers_read_back": 1700,
            "untraced_numbers_package_text": 0,
            "untraced_numbers_pdf_text": 0,
        },
        "all_cases": {"cases": 4, "reached_human_checkpoint": RATE, "decisions_recorded_without_a_human": 0},
        "provenance": {**PROVENANCE, "git_dirty": False},
    }


def write_list_files(folder: Path) -> dict[str, tuple[Path, ...]]:
    """One small SYNTHETIC file per list file, each with different text: {family: files}."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for family, names in LIST_FILE_NAMES.items():
        paths = []
        for name in names:
            path = folder / name
            path.write_text(f"SYNTHETIC test list file {name}\n", encoding="utf-8")
            paths.append(path)
        files[family] = tuple(paths)
    return files


def configure_screening_lists(
    monkeypatch: pytest.MonkeyPatch, folder: Path, lists: tuple[str, ...]
) -> dict[str, tuple[Path, ...]]:
    """Set the screening lists to `lists`, offline, and return the SYNTHETIC list files ({family: files}).

    kyc.screening then builds its default matcher the usual way (build_default_matcher -> watchlist_source_arguments),
    except: the configured lists are `lists`, the validated threshold is a test value, and load_watchlist is a fake
    that keeps the real rule (a None argument leaves that family out) and hashes each file it "loads" with the real
    kyc.watchlist.snapshot_for. The shared matcher starts empty and is reset after the test.
    """
    files = write_list_files(folder)
    sdn, alt = files["ofac"]
    (targets,) = files["opensanctions"]
    checksum_log = folder / "checksums.jsonl"

    def fake_load_watchlist(
        ofac_sdn: Path | None = sdn, ofac_alt: Path | None = alt, opensanctions: Path | None = targets
    ) -> Watchlist:
        """The SYNTHETIC entry, with a snapshot of every file of the families kept."""
        snapshots = []
        if ofac_sdn is not None and ofac_alt is not None:
            snapshots.append(snapshot_for(ofac_sdn, "OFAC", checksum_log))
            snapshots.append(snapshot_for(ofac_alt, "OFAC", checksum_log))
        if opensanctions is not None:
            snapshots.append(snapshot_for(opensanctions, "OpenSanctions", checksum_log))
        return watchlist_from_records(LIST_RECORDS, snapshots)

    monkeypatch.setattr(screening, "configured_screening_lists", lambda: lists)
    monkeypatch.setattr(screening, "load_selected_operating_point", lambda _path: {"threshold": 0.9})
    monkeypatch.setattr(screening, "load_watchlist", fake_load_watchlist)
    monkeypatch.setattr(screening, "_default_matcher", None)
    return files
