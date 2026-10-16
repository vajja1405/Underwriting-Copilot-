"""The provenance block every results JSON carries: which code, prompts, data, eval sets and models made the numbers.

Shape (docs/innovations.md, S3):

    {git_sha, git_dirty, generated_at_utc, generated_by,
     prompts{id: sha256}, corpus_checksum, eval_sets{path: sha256},
     models[{provider, model, digest, license, runtime_version}], data_snapshots,
     packages{name: version}, environment{cpu_count, load_average_start, load_average_end}}

The release gate (S3) recomputes some of these fields at HEAD (prompt hashes, corpus checksum, eval-set hashes,
pinned model digest). A mismatch means the evidence is stale: the numbers were measured on something else.

Typical use in a results script, so the load average is recorded at the start and at the end of the run:

    run = RunProvenance.start("scripts/eval_generation.py")
    ... do the work ...
    report["provenance"] = run.finish(prompts={"rag_answer_v1": "<sha256>"}, eval_sets=[QUESTIONS_FILE], models=[...])

provenance_block() can also be called directly; then load_average_start is None ("not recorded") unless given.
Nothing here is invented: a value that cannot be read (no git, no load average) is "unknown" or None.
"""

import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import REPO_ROOT

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MODEL_KEYS: tuple[str, ...] = ("provider", "model", "digest", "license", "runtime_version")
# Packages whose versions can change a reported number (LLM clients, retrieval, models, validation).
KEY_PACKAGES: tuple[str, ...] = (
    "anthropic",
    "openai",
    "httpx",
    "pydantic",
    "pyyaml",
    "numpy",
    "scikit-learn",
    "lightgbm",
    "fastembed",
    "qdrant-client",
    "langgraph",
    "mcp",
)
UNKNOWN = "unknown"


def git_sha(repo_root: Path = REPO_ROOT) -> str:
    """The current commit hash, or 'unknown' when git or the repository is not available."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return completed.stdout.strip() or UNKNOWN


def git_dirty(repo_root: Path = REPO_ROOT) -> bool | str:
    """True when the working tree has uncommitted changes, so the commit hash alone does not pin the code.

    Untracked files count. Changes under results/ are ignored, because writing results is what the scripts do
    (the same rule as uwcopilot.risk.results). Returns 'unknown' when git is not available.
    """
    command = ["git", "status", "--porcelain", "--", ".", ":(exclude)results"]
    try:
        completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return bool(completed.stdout.strip())


def load_average() -> list[float] | None:
    """The machine's 1/5/15-minute load average (context for latency numbers), or None where unavailable."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return None


def package_versions(packages: Sequence[str] = KEY_PACKAGES) -> dict[str, str]:
    """The Python version and the installed version of each package ('not installed' when missing)."""
    versions = {"python": platform.python_version()}
    for package in packages:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def display_path(path: Path | str, repo_root: Path = REPO_ROOT) -> str:
    """A path relative to the repo root when it is inside the repo; otherwise only the file name.

    Results files never hold absolute machine paths.
    """
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.name


def hash_files(paths: Sequence[Path | str], repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """{display path: sha256} for each file. A missing file raises FileNotFoundError: no hash is guessed."""
    hashes: dict[str, str] = {}
    for path in paths:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = repo_root / file_path
        if not file_path.is_file():
            raise FileNotFoundError(f"Cannot record provenance for a missing file: {display_path(file_path)}")
        hashes[display_path(file_path, repo_root)] = sha256_of_file(file_path)
    return hashes


def current_corpus_checksum() -> str:
    """The retrieval corpus checksum for the files present now, computed by rag.corpus (the existing function).

    It covers the guideline files and whichever public regulatory documents are downloaded, exactly like the
    index build and scripts/eval_rag.py, so the two can be compared.
    """
    from uwcopilot.rag.corpus import load_corpus  # imported here: the corpus loader pulls in PDF/HTML parsers

    return load_corpus().checksum()


def check_sha256(value: Any, what: str) -> str:
    """Return value if it is a 64-character lowercase hex SHA-256, else raise ValueError naming what it was."""
    if not isinstance(value, str) or not SHA256_HEX.match(value):
        raise ValueError(f"{what} must be a lowercase hex SHA-256 (got {value!r}).")
    return value


def prompt_hashes(prompts: Mapping[str, str] | None) -> dict[str, str]:
    """Validate {prompt id: sha256}. Hash prompt text with the prompt registry before passing it here."""
    checked: dict[str, str] = {}
    for prompt_id, digest in (prompts or {}).items():
        checked[str(prompt_id)] = check_sha256(digest, f"prompts[{prompt_id!r}]")
    return checked


def eval_set_hashes(eval_sets: Mapping[str, str] | Sequence[Path | str] | None) -> dict[str, str]:
    """{path: sha256} for the evaluation sets: hash a list of files, or validate an already-hashed mapping."""
    if eval_sets is None:
        return {}
    if isinstance(eval_sets, str | Path):
        return hash_files([eval_sets])
    if isinstance(eval_sets, Mapping):
        checked: dict[str, str] = {}
        for path, digest in eval_sets.items():
            checked[str(path)] = check_sha256(digest, f"eval_sets[{path!r}]")
        return checked
    return hash_files(list(eval_sets))


def model_entries(models: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize model records to exactly MODEL_KEYS; a missing key is None ("not recorded")."""
    entries: list[dict[str, Any]] = []
    for model in models or []:
        unknown = sorted(set(model) - set(MODEL_KEYS))
        if unknown:
            raise ValueError(f"Unknown model provenance keys {unknown}; allowed: {list(MODEL_KEYS)}.")
        if not model.get("provider") or not model.get("model"):
            raise ValueError("Every model provenance record needs at least a provider and a model.")
        entries.append({key: model.get(key) for key in MODEL_KEYS})
    return entries


def default_generated_by() -> str:
    """The running script as a repo-relative path (or its file name when it lives outside the repo)."""
    if not sys.argv or not sys.argv[0]:
        return UNKNOWN
    return display_path(sys.argv[0])


def provenance_block(
    *,
    prompts: Mapping[str, str] | None = None,
    eval_sets: Mapping[str, str] | Sequence[Path | str] | None = None,
    models: Sequence[Mapping[str, Any]] | None = None,
    data_snapshots: Mapping[str, Any] | None = None,
    corpus_checksum: str | None = None,
    generated_by: str | None = None,
    load_average_start: list[float] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Build the provenance block (see the module docstring for the shape).

    prompts: {prompt id: sha256}. eval_sets: files to hash, or {path: sha256}. models: records with the keys
    provider, model, digest, license, runtime_version. data_snapshots: free-form JSON (for example watchlist
    file hashes). corpus_checksum: computed with current_corpus_checksum() when not given.
    load_average_start: from RunProvenance.start(); None means it was not recorded.
    """
    snapshots = dict(data_snapshots or {})
    json.dumps(snapshots)  # fail early if the snapshot description is not plain JSON
    return {
        "git_sha": git_sha(repo_root),
        "git_dirty": git_dirty(repo_root),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": generated_by or default_generated_by(),
        "prompts": prompt_hashes(prompts),
        "corpus_checksum": corpus_checksum if corpus_checksum is not None else current_corpus_checksum(),
        "eval_sets": eval_set_hashes(eval_sets),
        "models": model_entries(models),
        "data_snapshots": snapshots,
        "packages": package_versions(),
        "environment": {
            "cpu_count": os.cpu_count(),
            "load_average_start": load_average_start,
            "load_average_end": load_average(),
        },
    }


class RunProvenance:
    """Records the load average and the git state when a run starts, then builds the provenance block when it ends.

    The commit is the one checked out when the run STARTED: a long run (an LLM evaluation) can outlast a commit, and
    the block must name the code that produced the numbers. If the commit changed or the tree was dirty at either
    end, git_dirty is True, so the hash alone is not trusted to pin the code.
    """

    def __init__(
        self,
        generated_by: str,
        load_average_start: list[float] | None,
        git_sha_start: str | None = None,
        git_dirty_start: bool | str | None = None,
    ) -> None:
        """Use RunProvenance.start(...) rather than calling this directly."""
        self.generated_by = generated_by
        self.load_average_start = load_average_start
        self.git_sha_start = git_sha_start
        self.git_dirty_start = git_dirty_start

    @classmethod
    def start(cls, generated_by: str | None = None, repo_root: Path = REPO_ROOT) -> "RunProvenance":
        """Begin a run now: remember who is running, the current load average and the checked-out commit."""
        return cls(generated_by or default_generated_by(), load_average(), git_sha(repo_root), git_dirty(repo_root))

    def finish(
        self,
        *,
        prompts: Mapping[str, str] | None = None,
        eval_sets: Mapping[str, str] | Sequence[Path | str] | None = None,
        models: Sequence[Mapping[str, Any]] | None = None,
        data_snapshots: Mapping[str, Any] | None = None,
        corpus_checksum: str | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> dict[str, Any]:
        """The provenance block for this run, with the start and end load averages and the start commit."""
        block = provenance_block(
            prompts=prompts,
            eval_sets=eval_sets,
            models=models,
            data_snapshots=data_snapshots,
            corpus_checksum=corpus_checksum,
            generated_by=self.generated_by,
            load_average_start=self.load_average_start,
            repo_root=repo_root,
        )
        if self.git_sha_start is None:
            return block
        changed = self.git_sha_start != block["git_sha"]
        block["git_sha"] = self.git_sha_start
        if changed or self.git_dirty_start is True:
            block["git_dirty"] = True
        return block
