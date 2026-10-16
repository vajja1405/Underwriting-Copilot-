"""Build the real metros' Studio data snapshots while the Docker image builds (Dockerfile stage "studio-snapshots").

    python scripts/build_studio_snapshots.py --secrets-dir /run/secrets --timeout-s 1500

A Hugging Face Docker Space exposes a Space SECRET to the image build only as a file, inside the one RUN step that
mounts it (`RUN --mount=type=secret,id=CENSUS_API_KEY,required=false ...` -> /run/secrets/CENSUS_API_KEY; Hugging
Face docs "Docker Spaces", section "Secrets / Buildtime"). This script runs inside that step:

1. It reads the build secrets the snapshot builder uses (BUILD_SECRETS) from those files. A value goes only into the
   environment of ONE child process, the snapshot builder; it is never printed, logged or written to a file.
2. Without CENSUS_API_KEY it builds nothing and exits 0: no metro gets a snapshot, so every real metro runs in
   limited mode ("no data snapshot") and says so. CENSUS_API_KEY is the gate because without it the snapshot has
   no ACS tract values (residents, households, income), so demand would be UNKNOWN everywhere.
3. With it, it runs `python -m uwcopilot.studio.data.snapshot --metro all` (what `make studio-data METRO=all` runs)
   with a time limit. The builder's own output is NOT shown (it can carry request details); every step's status and
   detail are in each snapshot's manifest.json. Its exit status 1 (some step failed) is expected and does not fail
   the image build.
4. It checks every snapshot folder: manifest.json and tracts.json parse, every layer file the manifest lists still
   matches its SHA-256, and the steps in REQUIRED_STEPS (the tracts and their ACS values) succeeded. A folder that
   fails the check (cut off by the time limit, or built while the Census API refused the key) is deleted, so its
   metro is honestly limited ("no data snapshot") instead of half-filled.
5. It deletes everything under data/studio except snapshots/: the HTTP response cache (data/studio/cache, the
   largest part of a local data/studio) and anything else the run left there (DuckDB's extension download).
6. It looks for every secret value in every file it keeps (secret_patterns: as is, URL-encoded, and base64 in the
   standard and URL-safe alphabets at any byte position), also inside the members of zip files (.npz layers are
   compressed zips). If one is found, every snapshot is deleted and the variable's NAME (never the value) is printed.
7. It prints, per real metro, whether a snapshot is kept (with the time its manifest was written, to tune the time
   limit) and the NAMES of the steps that failed or were not available (no details).

Every line it prints is first checked for the same forms of every secret value; a line that contains one is replaced
by a notice naming the variable (guarded_emit), so "never printed" does not depend on the builder's own redaction.

Exit code 0 in all of these cases: the image must build without the data. 2 only for bad arguments.
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uwcopilot.studio.data.keys import KEY_PURPOSES
from uwcopilot.studio.data.paths import STUDIO_DATA_DIR
from uwcopilot.studio.data.snapshot_files import SnapshotFileError, checked_bytes
from uwcopilot.studio.metros.loader import list_metros, load_metro

# The keys `uwcopilot.studio.data.snapshot` reads: the Census Data API key (ACS tracts and County Business Patterns,
# required) and the FRED key (optional: without it the keyless fredgraph.csv download gives the prime rate). The
# BLS, HUD and OpenRouteService keys are not read by the snapshot builder, so they are not build secrets.
REQUIRED_SECRET = "CENSUS_API_KEY"
OPTIONAL_SECRETS: tuple[str, ...] = ("FRED_API_KEY",)
BUILD_SECRETS: tuple[str, ...] = (REQUIRED_SECRET, *OPTIONAL_SECRETS)
# Real keys are long (a Census key has 40 characters); a shorter value is a mistake and too short to search for.
MIN_SECRET_LENGTH = 8
# DESIGN CHOICE: long enough for the five real metros on the machine this was built on (docs/deployment.md, 5b).
# It bounds the snapshot builder only: the Dockerfile adds 180 s for the rest of this script plus 30 s before a kill,
# and the other build stages come on top, so the deploy job's wait for the whole Space build is set separately
# (deploy_space.py wait --timeout-s in .github/workflows/deploy-space.yml). The Dockerfile passes its own value
# (ARG STUDIO_SNAPSHOT_TIMEOUT_S); this default is for running the script by hand.
DEFAULT_TIMEOUT_S = 1500.0
SNAPSHOTS_FOLDER = "snapshots"
# Steps of uwcopilot.studio.data.snapshot.build_snapshot that a kept snapshot must have finished with status "ok":
# the metro's tracts (Census Gazetteer, keyless) and their ACS values (residents, households, renters, income; needs
# the Census key). With a wrong or not yet active key, or a Census outage during the build, ACS fails while the
# keyless steps succeed; such a snapshot has tracts but UNKNOWN demand for most business types, so the app would
# offer area suggestions and simulations that then answer 409. It is removed instead, like a build without the key.
REQUIRED_STEPS: tuple[str, ...] = ("tract_geography", "acs_tracts")
# How a required step's status is printed (anything else is "not ok"); the step's detail is never printed.
STATUS_WORDS: dict[str | None, str] = {None: "missing", "failed": "failed", "not_available": "not available"}
# An exception class name on the last line of a traceback, e.g. "httpx.ConnectError: ..." -> "httpx.ConnectError".
# Its last part must start with a capital letter, as class names do (a lowercase hex key does not), and it is at most
# MAX_ERROR_TYPE_LENGTH characters. A last line without ": message" counts only when the name ends like an exception
# class (KeyboardInterrupt, SystemExit); any other line gives "unknown".
EXCEPTION_NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*")
BARE_EXCEPTION_SUFFIXES: tuple[str, ...] = ("Error", "Exception", "Exit", "Interrupt")
MAX_ERROR_TYPE_LENGTH = 100
# The base64 characters of a value at byte offset 0, 1 or 2 of an encoded blob start after this many characters of
# its first 4-character group (the characters before mix in the bytes in front of the value).
BASE64_SKIP_BY_OFFSET: tuple[int, ...] = (0, 2, 3)
URL_SAFE_BASE64 = bytes.maketrans(b"+/", b"-_")

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class BuilderOutcome:
    """How the snapshot builder child process ended."""

    timed_out: bool
    exit_code: int | None
    error_type: str | None = None  # the exception class name when it crashed (names only, never the message)


@dataclass(frozen=True)
class MetroSummary:
    """What a real metro has after this step (only names and dates, never step details)."""

    metro_id: str
    snapshot_date: str | None
    written_at: str | None = None  # the manifest's created_at_utc
    failed_steps: tuple[str, ...] = ()
    not_available_steps: tuple[str, ...] = ()


def read_build_secrets(secrets_dir: Path, names: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """({name: value} of the usable secrets, {name: status}) from <secrets_dir>/<name> files.

    The status is "provided", "not provided" or "too short, ignored"; it never contains the value.
    """
    values: dict[str, str] = {}
    statuses: dict[str, str] = {}
    for name in names:
        path = secrets_dir / name
        value = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        if not value:
            statuses[name] = "not provided"
        elif len(value) < MIN_SECRET_LENGTH:
            statuses[name] = "too short, ignored"
        else:
            values[name] = value
            statuses[name] = "provided"
    return values, statuses


def child_environment(base: Mapping[str, str], secrets: Mapping[str, str]) -> dict[str, str]:
    """The snapshot builder's environment: the base environment without any Studio key, plus the build secrets."""
    environment = {name: value for name, value in base.items() if name not in KEY_PURPOSES}
    environment.update(secrets)
    return environment


def builder_command() -> list[str]:
    """`python -m uwcopilot.studio.data.snapshot --metro all` with this interpreter (the image's virtualenv)."""
    return [sys.executable, "-m", "uwcopilot.studio.data.snapshot", "--metro", "all"]


def error_type_from(stderr: bytes | None) -> str | None:
    """The exception class name of a crash (the last line of a traceback), "unknown" when that line does not look
    like one (see EXCEPTION_NAME), or None when there was no traceback. The message after the name is never kept."""
    text = (stderr or b"").decode("utf-8", errors="replace")
    if "Traceback (most recent call last)" not in text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name, separator, _ = lines[-1].partition(":")
    name = name.strip()
    if len(name) > MAX_ERROR_TYPE_LENGTH or not EXCEPTION_NAME.fullmatch(name):
        return "unknown"
    if not separator and not name.endswith(BARE_EXCEPTION_SUFFIXES):
        return "unknown"
    return name


def run_builder(environment: Mapping[str, str], timeout_s: float, run: Runner = subprocess.run) -> BuilderOutcome:
    """Run the snapshot builder with a time limit. Its standard output is discarded; its error output is read only
    for the exception class name of a crash."""
    try:
        completed = run(
            builder_command(),
            env=dict(environment),
            timeout=timeout_s,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BuilderOutcome(timed_out=True, exit_code=None)
    return BuilderOutcome(timed_out=False, exit_code=completed.returncode, error_type=error_type_from(completed.stderr))


def snapshot_problem(folder: Path) -> str | None:
    """Why a snapshot folder cannot be used (a short reason without data), or None when it is complete."""
    manifest_path = folder / "manifest.json"
    tracts_path = folder / "tracts.json"
    for path in (manifest_path, tracts_path):
        if not path.is_file():
            return f"{path.name} is missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tracts = json.loads(tracts_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "manifest.json or tracts.json is not valid JSON"
    if not isinstance(manifest, dict) or not isinstance(tracts, dict) or not isinstance(tracts.get("tracts"), dict):
        return "manifest.json or tracts.json does not have the expected shape"
    for name in sorted(manifest.get("files") or {}):
        try:
            checked_bytes(folder, manifest, name)
        except SnapshotFileError:
            return f"layer file {name} is missing or does not match its SHA-256"
    return required_step_problem(manifest)


def required_step_problem(manifest: dict[str, Any]) -> str | None:
    """Why the manifest's REQUIRED_STEPS did not all succeed (step name and status word only), or None."""
    statuses = {step.get("name"): step.get("status") for step in manifest.get("steps") or [] if isinstance(step, dict)}
    for name in REQUIRED_STEPS:
        status = statuses.get(name)
        if status != "ok":
            return f"required step {name} is {STATUS_WORDS.get(status, 'not ok')}"
    return None


def snapshot_folders(snapshots_dir: Path) -> list[Path]:
    """Every <metro id>/<date> folder under snapshots_dir, sorted."""
    if not snapshots_dir.is_dir():
        return []
    return sorted(path for metro in snapshots_dir.iterdir() if metro.is_dir() for path in metro.iterdir())


def remove_incomplete(snapshots_dir: Path) -> list[str]:
    """Delete every snapshot folder that fails snapshot_problem(); returns '<metro>/<date>: <reason>' lines."""
    removed: list[str] = []
    for folder in snapshot_folders(snapshots_dir):
        problem = snapshot_problem(folder) if folder.is_dir() else "not a folder"
        if problem is None:
            continue
        if folder.is_dir():
            shutil.rmtree(folder)
        else:
            folder.unlink()
        removed.append(f"{folder.parent.name}/{folder.name}: {problem}")
    for metro in list(snapshots_dir.iterdir()) if snapshots_dir.is_dir() else []:
        if metro.is_dir() and not any(metro.iterdir()):
            metro.rmdir()
    return removed


def keep_only_snapshots(studio_dir: Path) -> list[str]:
    """Delete everything directly under studio_dir except the snapshots folder; returns the deleted names."""
    deleted: list[str] = []
    for path in sorted(studio_dir.iterdir()) if studio_dir.is_dir() else []:
        if path.name == SNAPSHOTS_FOLDER and path.is_dir() and not path.is_symlink():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(path.name)
    return deleted


def base64_forms(raw: bytes) -> list[bytes]:
    """The base64 text of raw wherever it sits inside a longer base64-encoded blob.

    Base64 turns every 3 bytes into 4 characters, so raw can start at byte offset 0, 1 or 2 of a group. For each
    offset this keeps only the characters that depend on raw alone: the first group's characters that mix in the
    bytes before raw are skipped (BASE64_SKIP_BY_OFFSET), and so is the last partial group, which mixes in the bytes
    after it. Each form also in the URL-safe alphabet ("-" and "_" instead of "+" and "/").
    """
    forms: list[bytes] = []
    for offset, skip in enumerate(BASE64_SKIP_BY_OFFSET):
        encoded = base64.b64encode(bytes(offset) + raw)
        core = encoded[skip : 4 * ((offset + len(raw)) // 3)]
        forms.extend([core, core.translate(URL_SAFE_BASE64)])
    return forms


def secret_patterns(value: str) -> list[bytes]:
    """The forms a secret value could take in a file or a printed line: as is, URL-encoded, and base64-encoded
    (base64_forms: standard and URL-safe, at any byte position inside a longer encoded blob)."""
    raw = value.encode("utf-8")
    forms = [raw, urllib.parse.quote(value, safe="").encode("ascii"), *base64_forms(raw)]
    return [form for form in dict.fromkeys(forms) if form]


def guarded_emit(emit: Callable[[str], None], secrets: Mapping[str, str]) -> Callable[[str], None]:
    """emit() that never prints a secret value: a line holding any secret_patterns() form of one is replaced by a
    notice that names the variable (never the value)."""
    patterns = {name: secret_patterns(value) for name, value in secrets.items()}

    def safe_emit(line: str) -> None:
        data = line.encode("utf-8", errors="replace")
        leaked = sorted(name for name, forms in patterns.items() if any(form in data for form in forms))
        if leaked:
            emit(f"(a line was withheld because it contained a value of {', '.join(leaked)})")
        else:
            emit(line)

    return safe_emit


def files_under(root: Path) -> Iterable[Path]:
    """Every regular file under root (symlinks are not followed)."""
    for current, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(current) / name
            if path.is_file() and not path.is_symlink():
                yield path


def zip_member_bodies(path: Path) -> list[bytes]:
    """The uncompressed bytes of every member of a zip file (an .npz layer is one), or [] for any other file."""
    if not zipfile.is_zipfile(path):
        return []
    try:
        with zipfile.ZipFile(path) as archive:
            return [archive.read(member) for member in archive.infolist() if not member.is_dir()]
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return []  # not a readable zip after all: its raw bytes are still searched


def secrets_found(root: Path, secrets: Mapping[str, str]) -> list[str]:
    """The NAMES of the secrets whose value appears in a file name, a file, or a zip member under root."""
    patterns = {name: secret_patterns(value) for name, value in secrets.items()}
    found: set[str] = set()
    for path in files_under(root):
        bodies = [path.read_bytes(), str(path.relative_to(root)).encode("utf-8"), *zip_member_bodies(path)]
        for name, forms in patterns.items():
            if any(form in body for body in bodies for form in forms):
                found.add(name)
    return sorted(found)


def step_names(manifest: dict[str, Any], status: str) -> tuple[str, ...]:
    """The names of the manifest's steps with this status."""
    return tuple(str(step.get("name")) for step in manifest.get("steps", []) if step.get("status") == status)


def summarize(snapshots_dir: Path, metro_ids: Sequence[str]) -> list[MetroSummary]:
    """One summary per real metro: its newest kept snapshot (if any) and its failed / not available step names."""
    summaries: list[MetroSummary] = []
    for metro_id in metro_ids:
        folders = sorted(path for path in (snapshots_dir / metro_id).glob("*") if (path / "manifest.json").is_file())
        if not folders:
            summaries.append(MetroSummary(metro_id=metro_id, snapshot_date=None))
            continue
        manifest = json.loads((folders[-1] / "manifest.json").read_text(encoding="utf-8"))
        summaries.append(
            MetroSummary(
                metro_id=metro_id,
                snapshot_date=folders[-1].name,
                written_at=manifest.get("created_at_utc"),
                failed_steps=step_names(manifest, "failed"),
                not_available_steps=step_names(manifest, "not_available"),
            )
        )
    return summaries


def summary_lines(summaries: Sequence[MetroSummary]) -> list[str]:
    """The lines printed at the end of the step (names, dates and times only)."""
    lines: list[str] = []
    for item in summaries:
        if item.snapshot_date is None:
            lines.append(f"  {item.metro_id}: no snapshot (runs in limited mode: no data snapshot)")
            continue
        failed = ", ".join(item.failed_steps) or "none"
        not_available = ", ".join(item.not_available_steps) or "none"
        written = f" (written {item.written_at})" if item.written_at else ""
        lines.append(
            f"  {item.metro_id}: snapshot {item.snapshot_date} kept{written}; failed steps: {failed}; "
            f"not available: {not_available}"
        )
    return lines


def real_metro_ids() -> list[str]:
    """The ids of the metros whose data is PUBLIC (the SYNTHETIC metro has nothing to download)."""
    return [metro_id for metro_id in list_metros() if load_metro(metro_id).data_label == "PUBLIC"]


def build_snapshots(
    secrets_dir: Path,
    studio_dir: Path,
    timeout_s: float,
    environ: Mapping[str, str],
    emit: Callable[[str], None],
    run: Runner = subprocess.run,
    metro_ids: Sequence[str] | None = None,
) -> None:
    """Steps 1 to 7 of the module docstring; emit() receives each line to print, after guarded_emit's check."""
    snapshots_dir = studio_dir / SNAPSHOTS_FOLDER
    snapshots_dir.mkdir(parents=True, exist_ok=True)  # the Dockerfile copies this folder, even when it is empty
    secrets, statuses = read_build_secrets(secrets_dir, BUILD_SECRETS)
    emit = guarded_emit(emit, secrets)
    for name in BUILD_SECRETS:
        emit(f"build secret {name}: {statuses[name]}")
    if REQUIRED_SECRET not in secrets:
        emit(f"Studio snapshots skipped: the build secret {REQUIRED_SECRET} was not provided.")
    else:
        started = datetime.now(UTC).isoformat(timespec="seconds")
        emit(f"Running the Studio snapshot builder for every real metro from {started} (time limit {timeout_s:g} s)")
        clock_start = time.monotonic()
        outcome = run_builder(child_environment(environ, secrets), timeout_s, run)
        elapsed = f"after {time.monotonic() - clock_start:.0f} s"
        if outcome.timed_out:
            emit(f"The snapshot builder reached the time limit of {timeout_s:g} s and was stopped.")
        elif outcome.error_type is not None:
            emit(f"The snapshot builder stopped {elapsed} with {outcome.error_type} (exit code {outcome.exit_code}).")
        else:
            emit(f"The snapshot builder finished {elapsed} (exit code {outcome.exit_code}; 1 means some step failed).")
        for item in remove_incomplete(snapshots_dir):
            emit(f"removed incomplete snapshot {item}")
    deleted = keep_only_snapshots(studio_dir)
    if deleted:
        emit(f"deleted from data/studio: {', '.join(deleted)}")
    leaked = secrets_found(studio_dir, secrets)
    if leaked:
        shutil.rmtree(snapshots_dir)
        snapshots_dir.mkdir()
        emit(f"a value of {', '.join(leaked)} was found in the snapshot files: every snapshot was removed")
    else:
        emit(f"secret values searched for in the kept files: {len(secrets)}; found: none")
    emit("Real metros (a metro without a snapshot runs in limited mode):")
    for line in summary_lines(summarize(snapshots_dir, real_metro_ids() if metro_ids is None else metro_ids)):
        emit(line)


def print_line(line: str) -> None:
    """Print one line at once (a Docker build log shows it while the step is still running)."""
    print(line, flush=True)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Command-line arguments (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--secrets-dir", type=Path, default=Path("/run/secrets"), help="where the secret files are")
    parser.add_argument("--studio-dir", type=Path, default=STUDIO_DATA_DIR, help="data/studio of this checkout")
    parser.add_argument(
        "--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="time limit of the snapshot builder, seconds"
    )
    args = parser.parse_args(argv)
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")
    return args


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the step and print its lines; 0 whatever the data sources did."""
    args = parse_args(argv)
    environment = os.environ if environ is None else environ
    build_snapshots(args.secrets_dir, args.studio_dir, args.timeout_s, environment, print_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
