"""Freeze proofs (governance/freeze_proof.py) in a throwaway git repository with fixed commit dates.

The files are SYNTHETIC test content; no project data is used.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from uwcopilot.governance.freeze_proof import (
    committed_before,
    freeze_record,
    iso_date,
    migration_section_sha256,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

MARGINS = "migration:\n  gated_metrics:\n    key_fact_recall: {direction: higher_is_better, margin: 0.05}\n"


def git(repo: Path, *arguments: str, when: str | None = None) -> None:
    """Run git in repo with a fixed identity and (optionally) fixed author and committer dates."""
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *arguments]
    subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True)


def commit_file(repo: Path, name: str, content: str, when: str) -> None:
    """Write a file and commit it at a fixed date."""
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"update {name}", when=when)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repository whose first commit (19:39 UTC) holds the margins file."""
    git(tmp_path, "init", "-q")
    commit_file(tmp_path, "release_gate.yaml", MARGINS, "2026-10-12T19:39:16+00:00")
    return tmp_path


def test_record_points_at_the_first_commit_with_the_content(repo: Path) -> None:
    record = freeze_record(repo / "release_gate.yaml", "margins", "migration_section", repo)
    assert record["first_committed"]["author_date"] == "2026-10-12T19:39:16+00:00"
    assert record["sha256"] == migration_section_sha256(MARGINS.encode("utf-8"))
    assert committed_before(record, "2026-10-13T06:00:00+00:00")[0] is True
    ok, detail = committed_before(record, "2026-10-12T19:00:00+00:00")
    assert ok is False and "AFTER" in detail


def test_amending_the_commit_keeps_the_freeze_date(repo: Path) -> None:
    git(repo, "commit", "--amend", "--no-edit", "-q", when=None)  # a rebase or squash rewrites the committer date
    record = freeze_record(repo / "release_gate.yaml", "margins", "migration_section", repo)
    assert record["first_committed"]["author_date"] == "2026-10-12T19:39:16+00:00"


def test_a_new_section_or_comment_does_not_count_as_a_margins_change(repo: Path) -> None:
    commit_file(
        repo, "release_gate.yaml", "# note\n" + MARGINS + "controls:\n  GC-01: {}\n", "2026-10-15T10:00:00+00:00"
    )
    record = freeze_record(repo / "release_gate.yaml", "margins", "migration_section", repo)
    assert record["first_committed"]["author_date"] == "2026-10-12T19:39:16+00:00"
    whole_file = freeze_record(repo / "release_gate.yaml", "margins", "file", repo)
    assert whole_file["first_committed"]["author_date"] == "2026-10-15T10:00:00+00:00"


def test_a_changed_margin_is_dated_by_its_own_commit(repo: Path) -> None:
    commit_file(repo, "release_gate.yaml", MARGINS.replace("0.05", "0.20"), "2026-10-14T10:00:00+00:00")
    record = freeze_record(repo / "release_gate.yaml", "margins", "migration_section", repo)
    assert record["first_committed"]["author_date"] == "2026-10-14T10:00:00+00:00"
    assert committed_before(record, "2026-10-13T06:00:00+00:00")[0] is False


def test_uncommitted_and_outside_files_have_no_freeze_commit(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    (repo / "release_gate.yaml").write_text(MARGINS.replace("0.05", "0.07"), encoding="utf-8")
    record = freeze_record(repo / "release_gate.yaml", "margins", "migration_section", repo)
    assert record["first_committed"] is None and "not in any commit" in record["note"]
    assert committed_before(record, "2026-10-13T06:00:00+00:00")[0] is False
    outside = tmp_path_factory.mktemp("outside") / "rag_key_facts.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    elsewhere = freeze_record(outside, "key_facts", "file", repo)
    assert elsewhere["first_committed"] is None and "outside the repository" in elsewhere["note"]


def test_git_dates_have_one_spelling_whatever_the_git_version() -> None:
    # git 2.43 prints "+00:00" for UTC and git 2.55 (the CI runner) prints "Z"; both must compare equal.
    assert iso_date("2026-10-12T19:39:16Z") == "2026-10-12T19:39:16+00:00"
    assert iso_date("2026-10-12T19:39:16+00:00") == "2026-10-12T19:39:16+00:00"
    assert iso_date("2026-10-12T21:39:16+02:00") == "2026-10-12T21:39:16+02:00"
