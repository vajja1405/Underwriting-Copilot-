"""Append-only decision records (JSON Lines), chained by SHA-256 so an edit or a deletion can be detected.

Each record holds everything needed to explain a decision later: the inputs hash, every tool call and result, the
policy result, the memo and its check, model and prompt versions, and the reviewer's action (NBC-10.1, HM-12.1:
decision records are append-only; a correction is a new record that references the old one).

Chaining: every record stores previous_record_sha256 (the hash of the record before it, or 64 zeros for the
first) and its own record_sha256 (the hash of the record without that field). verify_chain() recomputes both.

Tool-call latencies are left out of the record (they are in the traces), so a re-run with the same clock, inputs,
tool results, LLM answers and decision produces the same record and the same hash.

Appending is idempotent: a record_id that is already in the file is not written again. That matters for
crash recovery: if the process dies after writing but before LangGraph saves its checkpoint, the resumed run
re-executes the record node and must not write a second record.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from uwcopilot.agent.applications import canonical_json

GENESIS_HASH = "0" * 64
RECORD_SCHEMA_VERSION = 1


def record_hash(record: dict[str, Any]) -> str:
    """SHA-256 of the record's canonical JSON, without its own record_sha256 field."""
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


class DecisionLog:
    """One JSONL file of decision records. Records are only ever appended."""

    def __init__(self, path: Path) -> None:
        """path is created (with its folder) on the first append."""
        self.path = path

    def records(self) -> list[dict[str, Any]]:
        """Every record in file order (an empty list if the file does not exist yet).

        The file is split on "\n" only: str.splitlines() would also split on U+2028 or U+0085 inside a rationale,
        which canonical_json writes raw, and cut that record in two.
        """
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").split("\n")
        return [json.loads(line) for line in lines if line.strip()]

    def find(self, record_id: str) -> dict[str, Any] | None:
        """The record with this id, or None."""
        for record in self.records():
            if record.get("record_id") == record_id:
                return record
        return None

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Chain and append one record (it must have a record_id); return the stored record.

        If a record with the same record_id is already in the file, that stored record is returned unchanged.
        """
        if "record_id" not in record:
            raise ValueError("a decision record needs a record_id")
        existing = self.records()
        for stored in existing:
            if stored.get("record_id") == record["record_id"]:
                return stored
        chained = dict(record)
        chained["schema_version"] = RECORD_SCHEMA_VERSION
        chained["previous_record_sha256"] = existing[-1]["record_sha256"] if existing else GENESIS_HASH
        chained["record_sha256"] = record_hash(chained)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(chained) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return chained

    def verify_chain(self) -> list[str]:
        """Problems found while re-checking every hash (an empty list means the chain is intact)."""
        problems = []
        previous = GENESIS_HASH
        for index, record in enumerate(self.records()):
            if record.get("previous_record_sha256") != previous:
                problems.append(f"record {index}: previous_record_sha256 does not match the record before it")
            if record.get("record_sha256") != record_hash(record):
                problems.append(f"record {index}: record_sha256 does not match its content")
            previous = record.get("record_sha256", "")
        return problems
