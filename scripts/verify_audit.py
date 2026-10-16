"""Check the audit log's hash chain and head anchor, and optionally the chain of decision logs (Phase 5 B).

No key is needed: the audit log is plaintext (PII-redacted), and an encrypted decision log keeps its chain fields
in clear (security/encrypted_log.py).

What is detected in the audit log: an edited event, a deleted event, reordered events and a truncated tail (by the
head anchor). Someone who can rewrite both the log and its head file can rebuild a consistent chain, so keep a copy
of the printed head (count and hash) elsewhere and pass it back later with --expect-count and --expect-head. The copy
is checked as a prefix: the log may have grown since it was taken (normal), but the events up to it must be
unchanged. Events appended after the copy are covered only by the stored head, so refresh the copy regularly.

Decision logs (agent.decision_record.DecisionLog and security.encrypted_log.EncryptedDecisionLog) have no head
anchor: their chain detects edits, deletions and reordering, not a truncated tail. A line that is no longer valid
JSON is reported by its line number.

Examples:
    uv run python scripts/verify_audit.py
    uv run python scripts/verify_audit.py --log data/processed/security/audit_events.jsonl \\
        --expect-count 42 --expect-head 3f2a...
    uv run python scripts/verify_audit.py --decision-log data/processed/agent/decision_records.jsonl

Exit code 0 when everything checked is intact, 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.security.audit import AuditHead, AuditLog
from uwcopilot.security.fileio import jsonl_lines
from uwcopilot.security.settings import load_security_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, help="audit log (default UWC_AUDIT_LOG or data/processed/security/...)")
    parser.add_argument("--expect-count", type=int, help="the event count from a head anchor kept elsewhere")
    parser.add_argument("--expect-head", help="the last record_hash from a head anchor kept elsewhere")
    parser.add_argument(
        "--decision-log", type=Path, action="append", default=[], help="also check this decision log's chain"
    )
    parser.add_argument("--skip-audit-log", action="store_true", help="check only the --decision-log files")
    args = parser.parse_args(argv)
    if (args.expect_count is None) != (args.expect_head is None):
        parser.error("--expect-count and --expect-head go together")
    return args


def check_audit_log(args: argparse.Namespace) -> list[str]:
    """Print and return the audit log's problems."""
    path = args.log or load_security_settings().audit_log
    log = AuditLog(path)
    expected = None
    if args.expect_count is not None:
        expected = AuditHead(count=args.expect_count, head_hash=args.expect_head)
    problems = log.verify(expected)
    if problems:
        print(f"Audit log {path}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return problems
    count = len(log.events())
    print(f"Audit log {path}: intact ({count} events)")
    if expected is not None:
        print(
            f"  the anchor kept elsewhere still matches event {expected.count}; "
            f"{count - expected.count} event(s) were appended after it"
        )
    head = log.head()
    if head is not None:
        print(f"  head anchor: count={head.count} head_hash={head.head_hash}")
        print("  Keep a copy of this anchor somewhere the log's writer cannot change.")
    return problems


def unreadable_lines(path: Path) -> list[str]:
    """Lines of a decision log that are not a JSON object (edited or cut off), by line number."""
    problems = []
    for number, line in enumerate(jsonl_lines(path.read_text(encoding="utf-8")), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not valid JSON (edited or cut off)")
            continue
        if not isinstance(value, dict):
            problems.append(f"line {number}: not a JSON object (edited)")
    return problems


def check_decision_log(path: Path) -> list[str]:
    """Print and return one decision log's problems: unreadable lines, else chain problems (no key needed)."""
    if not path.is_file():
        problem = f"no decision log at {path}"
        print(f"Decision log {path}: {problem}")
        return [problem]
    log = DecisionLog(path)
    problems = unreadable_lines(path) or log.verify_chain()
    if problems:
        print(f"Decision log {path}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print(f"Decision log {path}: chain intact ({len(log.records())} records; a truncated tail is not detectable)")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Verify; 0 when intact, 1 when any problem was found."""
    args = parse_args(argv)
    problems: list[str] = []
    if not args.skip_audit_log:
        problems.extend(check_audit_log(args))
    for path in args.decision_log:
        problems.extend(check_decision_log(path))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
