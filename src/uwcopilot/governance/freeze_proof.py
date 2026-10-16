"""Freeze proofs: was an evaluation input committed to git BEFORE the answers it scores were generated?

docs/innovations.md (S1) asks for two freezes: the key facts (and the question files and frozen hits) must be
committed before R1, and the migration margins in config/release_gate.yaml before R2. "The commit timestamp is the
freeze proof." This module turns that sentence into a check that survives normal git work.

How it works:
1. At the START of every evaluation run, scripts/eval_generation.py calls freeze_record() for each frozen input.
   A record holds the input's content fingerprint (sha256) and the FIRST commit whose copy of the file has that same
   fingerprint, with the commit's author date. The record is stored in the run's results block.
2. The migration gate later compares each record with the earliest moment an answer of that run was generated
   (committed_before()). It also checks that the inputs on disk still have the recorded fingerprints.

Design choices:
- Content, not "last commit touching the file": for config/release_gate.yaml only the parsed `migration:` section is
  fingerprinted (canonical JSON), so adding a Phase 6 `controls:` section or editing a comment does not count as a
  change of the margins.
- Author date, not committer date: `git commit --amend`, rebase and cherry-pick keep the author date and rewrite the
  committer date, so squashing the WIP checkpoints would otherwise make the margins look newer than the runs.
- Recorded at run time: a later squash-merge (new commits, new dates) cannot invalidate a proof already recorded.

Limits (stated, not hidden): git dates can be set by hand (`--date`), and the results JSON can be edited. This is a
process control that catches honest mistakes and casual tuning, not a tamper-proof seal.
"""

import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

FingerprintKind = Literal["file", "migration_section"]
FINGERPRINT_KINDS: tuple[str, ...] = ("file", "migration_section")


@dataclass(frozen=True)
class CommitInfo:
    """One commit that touched a file."""

    sha: str
    author_date: str
    committer_date: str


def run_git(arguments: list[str], repo_root: Path) -> bytes | None:
    """stdout of a git command as bytes, or None when git fails (no git, not a repository, unknown path)."""
    try:
        completed = subprocess.run(["git", *arguments], cwd=repo_root, capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout


def repo_relative(path: Path, repo_root: Path) -> str | None:
    """The path relative to the repository root (POSIX style), or None when it is outside the repository."""
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def iso_date(git_date: str) -> str:
    """One spelling for a git ISO 8601 date: newer git prints UTC as "Z", older git as "+00:00"."""
    return datetime.fromisoformat(git_date).isoformat()


def commits_touching(relative_path: str, repo_root: Path) -> list[CommitInfo]:
    """Every commit that changed the file, oldest first ([] when git is unavailable)."""
    output = run_git(["log", "--reverse", "--format=%H%x09%aI%x09%cI", "--", relative_path], repo_root)
    commits: list[CommitInfo] = []
    if output is None:
        return commits
    for line in output.decode("utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            commits.append(CommitInfo(sha=parts[0], author_date=iso_date(parts[1]), committer_date=iso_date(parts[2])))
    return commits


def committed_bytes(commit_sha: str, relative_path: str, repo_root: Path) -> bytes | None:
    """The file's content in a commit, or None when the file does not exist there."""
    return run_git(["show", f"{commit_sha}:{relative_path}"], repo_root)


def canonical_sha256(value: Any) -> str:
    """SHA-256 of the canonical JSON of a value (sorted keys, no extra whitespace, UTF-8)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def migration_section_sha256(data: bytes) -> str | None:
    """Fingerprint of the parsed `migration:` section of a release gate YAML file (None if it has none)."""
    try:
        document = yaml.safe_load(data.decode("utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError):
        return None
    if not isinstance(document, Mapping) or "migration" not in document:
        return None
    return canonical_sha256(document["migration"])


def fingerprint(data: bytes, kind: FingerprintKind) -> str | None:
    """The content fingerprint of a file's bytes: the whole file, or only its migration section."""
    if kind == "file":
        return hashlib.sha256(data).hexdigest()
    if kind == "migration_section":
        return migration_section_sha256(data)
    raise ValueError(f"unknown fingerprint kind {kind!r}; known: {FINGERPRINT_KINDS}")


def first_commit_with(relative_path: str, kind: FingerprintKind, wanted: str, repo_root: Path) -> CommitInfo | None:
    """The oldest commit whose copy of the file has the wanted fingerprint, or None."""
    for commit in commits_touching(relative_path, repo_root):
        data = committed_bytes(commit.sha, relative_path, repo_root)
        if data is not None and fingerprint(data, kind) == wanted:
            return commit
    return None


def freeze_record(path: Path, role: str, kind: FingerprintKind, repo_root: Path) -> dict[str, Any]:
    """The freeze proof of one input as it is NOW (call this when a run starts).

    {role, path, kind, sha256, first_committed: {sha, author_date, committer_date} | None, note}
    """
    relative = repo_relative(path, repo_root)
    record: dict[str, Any] = {
        "role": role,
        "path": relative if relative is not None else path.name,
        "kind": kind,
        "sha256": fingerprint(path.read_bytes(), kind),
        "first_committed": None,
        "note": "",
    }
    if relative is None:
        record["note"] = "outside the repository, so it cannot have been committed"
        return record
    if record["sha256"] is None:
        record["note"] = "no `migration:` section to fingerprint"
        return record
    commit = first_commit_with(relative, kind, record["sha256"], repo_root)
    if commit is None:
        record["note"] = "this content is not in any commit (uncommitted change, or git is unavailable)"
        return record
    record["first_committed"] = asdict(commit)
    record["note"] = "first commit holding this exact content (author date)"
    return record


def committed_before(record: Mapping[str, Any], moment_utc: str | None) -> tuple[bool, str]:
    """(True, why) when the record's content was committed at or before moment_utc (ISO 8601), else (False, why)."""
    commit = record.get("first_committed")
    if not commit:
        return False, f"{record.get('path')}: not committed ({record.get('note') or 'no commit recorded'})"
    if moment_utc is None:
        return False, f"{record.get('path')}: the run does not record when its answers were generated"
    committed = f"{record.get('path')}: committed {commit['author_date']} ({commit['sha'][:12]})"
    if datetime.fromisoformat(commit["author_date"]) <= datetime.fromisoformat(moment_utc):
        return True, f"{committed}, before {moment_utc}"
    return False, f"{committed}, AFTER {moment_utc}"
