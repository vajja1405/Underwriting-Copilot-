"""Tests for scripts/build_studio_snapshots.py and the Dockerfile stage that runs it.

The snapshot builder is never run: a fake `run` writes snapshot folders the way it would, so there is no network,
no Docker and no real key. The fake key values below are made up for these tests.
"""

import base64
import hashlib
import inspect
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

import build_studio_snapshots
from build_studio_snapshots import (
    BUILD_SECRETS,
    REQUIRED_SECRET,
    REQUIRED_STEPS,
    build_snapshots,
    child_environment,
    error_type_from,
    guarded_emit,
    read_build_secrets,
    real_metro_ids,
    snapshot_problem,
)
from uwcopilot.studio.data.snapshot import build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
CENSUS_VALUE = "census-test-value-0123456789abcdef"
FRED_VALUE = "fred-test-value-0123456789"
METROS = ("alpha_metro", "beta_metro")


def write_secrets(folder: Path, values: dict[str, str]) -> Path:
    """Secret files as BuildKit mounts them: one file per secret, named after it (with a trailing newline)."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        (folder / name).write_text(value + "\n", encoding="utf-8")
    return folder


def write_snapshot(
    studio_dir: Path, metro_id: str, steps: list[dict[str, Any]], layers: dict[str, bytes], manifest_extra: str = ""
) -> Path:
    """A snapshot folder like snapshot.write_snapshot makes: layer files, manifest.json with their SHA-256, tracts."""
    folder = studio_dir / "snapshots" / metro_id / "2026-10-14"
    folder.mkdir(parents=True)
    for name, body in layers.items():
        (folder / name).write_bytes(body)
    manifest = {
        "metro_id": metro_id,
        "snapshot_date": "2026-10-14",
        "created_at_utc": "2026-10-14T01:32:06+00:00",
        "steps": steps,
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in layers.items()},
        "note": manifest_extra,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "tracts.json").write_text(json.dumps({"tracts": {"48201000100": {"lat": 29.7, "lon": -95.3}}}))
    return folder


STEPS = [
    {"name": "tract_geography", "status": "ok", "detail": "1615 tracts"},
    {"name": "acs_tracts", "status": "ok", "detail": "ok detail"},
    {"name": "overture_places", "status": "failed", "detail": "Cannot reach host: secret-looking detail"},
    {"name": "enhancement:traffic_state:TX", "status": "not_available", "detail": "no dataset"},
]


class FakeRun:
    """Stands in for subprocess.run: records the call and runs an action that writes what the builder would."""

    def __init__(self, action: Any = None, returncode: int = 1, stderr: bytes = b"") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.action = action
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append((command, kwargs))
        if self.action is not None:
            self.action()
        return subprocess.CompletedProcess(command, self.returncode, stdout=None, stderr=self.stderr)


def run_step(tmp_path: Path, secrets: dict[str, str], run: FakeRun, environ: dict[str, str] | None = None) -> list[str]:
    """Run build_snapshots on tmp folders; returns the emitted lines."""
    lines: list[str] = []
    build_snapshots(
        write_secrets(tmp_path / "secrets", secrets),
        tmp_path / "studio",
        timeout_s=900,
        environ=environ or {"PATH": "/usr/bin"},
        emit=lines.append,
        run=run,
        metro_ids=METROS,
    )
    return lines


def assert_no_secret_value(lines: list[str]) -> None:
    """No emitted line carries a secret value."""
    text = "\n".join(lines)
    assert CENSUS_VALUE not in text and FRED_VALUE not in text


def test_secrets_are_read_from_files_and_their_status_never_holds_the_value(tmp_path: Path) -> None:
    folder = write_secrets(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE, "FRED_API_KEY": "short"})
    (folder / "EMPTY_KEY").write_text("\n", encoding="utf-8")

    values, statuses = read_build_secrets(folder, ("CENSUS_API_KEY", "FRED_API_KEY", "EMPTY_KEY", "MISSING_KEY"))

    assert values == {"CENSUS_API_KEY": CENSUS_VALUE}  # stripped of the trailing newline
    assert statuses == {
        "CENSUS_API_KEY": "provided",
        "FRED_API_KEY": "too short, ignored",
        "EMPTY_KEY": "not provided",
        "MISSING_KEY": "not provided",
    }


def test_the_child_gets_only_the_build_secrets_as_studio_keys() -> None:
    base = {"PATH": "/usr/bin", "BLS_API_KEY": "from-the-environment", "CENSUS_API_KEY": "stale"}

    environment = child_environment(base, {"CENSUS_API_KEY": CENSUS_VALUE})

    assert environment == {"PATH": "/usr/bin", "CENSUS_API_KEY": CENSUS_VALUE}


def test_without_the_census_secret_nothing_runs_and_every_metro_is_limited(tmp_path: Path) -> None:
    (tmp_path / "studio" / "cache" / "ab").mkdir(parents=True)
    (tmp_path / "studio" / "cache" / "ab" / "x.body").write_bytes(b"cached answer")
    run = FakeRun()

    lines = run_step(tmp_path, {"FRED_API_KEY": FRED_VALUE}, run)

    assert run.calls == []
    assert sorted(path.name for path in (tmp_path / "studio").iterdir()) == ["snapshots"]
    assert list((tmp_path / "studio" / "snapshots").iterdir()) == []
    assert "build secret CENSUS_API_KEY: not provided" in lines
    assert "build secret FRED_API_KEY: provided" in lines
    assert any(line.startswith("Studio snapshots skipped") for line in lines)
    assert sum("no snapshot (runs in limited mode" in line for line in lines) == len(METROS)
    assert_no_secret_value(lines)


def test_with_the_secret_the_builder_runs_and_only_complete_snapshots_are_kept(tmp_path: Path) -> None:
    studio = tmp_path / "studio"

    def builder() -> None:
        write_snapshot(studio, "alpha_metro", STEPS, {"places.json": b"{}"})
        broken = write_snapshot(studio, "beta_metro", STEPS, {"roads.npz": b"roads"})
        (broken / "roads.npz").write_bytes(b"cut off")  # no longer matches the manifest's SHA-256
        (studio / "cache" / "cd").mkdir(parents=True)
        (studio / "cache" / "cd" / "y.json").write_text('{"url": "https://api.census.gov/data"}')
        (studio / "duckdb_extensions").mkdir()

    run = FakeRun(builder, returncode=1)
    lines = run_step(
        tmp_path,
        {"CENSUS_API_KEY": CENSUS_VALUE, "FRED_API_KEY": FRED_VALUE},
        run,
        environ={"PATH": "/usr/bin", "HUD_USER_TOKEN": "not-for-the-child"},
    )

    ((command, kwargs),) = run.calls
    assert command[1:] == ["-m", "uwcopilot.studio.data.snapshot", "--metro", "all"]
    assert kwargs["env"] == {"PATH": "/usr/bin", "CENSUS_API_KEY": CENSUS_VALUE, "FRED_API_KEY": FRED_VALUE}
    assert kwargs["timeout"] == 900
    assert kwargs["stdout"] == subprocess.DEVNULL  # the builder's output (request details) is never shown
    assert sorted(path.name for path in studio.iterdir()) == ["snapshots"]
    assert sorted(path.name for path in (studio / "snapshots").iterdir()) == ["alpha_metro"]
    assert any(
        line.startswith("removed incomplete snapshot beta_metro/2026-10-14: layer file roads.npz") for line in lines
    )
    assert "deleted from data/studio: cache, duckdb_extensions" in lines
    assert any(line.startswith("The snapshot builder finished after ") for line in lines)
    assert (
        "  alpha_metro: snapshot 2026-10-14 kept (written 2026-10-14T01:32:06+00:00); failed steps: overture_places; "
        "not available: enhancement:traffic_state:TX"
    ) in lines
    assert "  beta_metro: no snapshot (runs in limited mode: no data snapshot)" in lines
    assert not any("secret-looking detail" in line for line in lines)  # step names only, never details
    assert_no_secret_value(lines)


def test_a_builder_cut_off_by_the_time_limit_keeps_only_finished_metros(tmp_path: Path) -> None:
    studio = tmp_path / "studio"

    def builder_then_timeout() -> None:
        write_snapshot(studio, "alpha_metro", STEPS, {})
        partial = studio / "snapshots" / "beta_metro" / "2026-10-14"
        partial.mkdir(parents=True)
        (partial / "places.json").write_bytes(b"{}")
        raise subprocess.TimeoutExpired(cmd="snapshot", timeout=900)

    lines = run_step(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE}, FakeRun(builder_then_timeout))

    assert "The snapshot builder reached the time limit of 900 s and was stopped." in lines
    assert sorted(path.name for path in (studio / "snapshots").iterdir()) == ["alpha_metro"]
    assert "removed incomplete snapshot beta_metro/2026-10-14: manifest.json is missing" in lines


def test_a_crash_is_reported_by_its_exception_name_only(tmp_path: Path) -> None:
    stderr = (
        b'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n'
        + f"httpx.ConnectError: https://api.census.gov/data?key={CENSUS_VALUE}\n".encode()
    )

    lines = run_step(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE}, FakeRun(stderr=stderr))

    crash_lines = [line for line in lines if line.startswith("The snapshot builder stopped after ")]
    assert len(crash_lines) == 1 and crash_lines[0].endswith(" s with httpx.ConnectError (exit code 1).")
    assert_no_secret_value(lines)


@pytest.mark.parametrize(
    "leaked_form",
    [
        CENSUS_VALUE,
        base64.b64encode(CENSUS_VALUE.encode()).decode(),
        # inside a longer base64 blob, at byte offsets 2 and 1 of a 3-byte group, and in the URL-safe alphabet
        base64.b64encode(b"k=" + CENSUS_VALUE.encode()).decode(),
        base64.b64encode(b"key=" + CENSUS_VALUE.encode()).decode(),
        base64.urlsafe_b64encode(b"?" + CENSUS_VALUE.encode() + b"&").decode(),
    ],
    ids=["as-is", "base64", "base64-offset-2", "base64-offset-1", "base64-url-safe"],
)
def test_a_secret_value_in_a_kept_file_removes_every_snapshot(tmp_path: Path, leaked_form: str) -> None:
    studio = tmp_path / "studio"

    def leaky_builder() -> None:
        write_snapshot(studio, "alpha_metro", STEPS, {}, manifest_extra=f"url?key={leaked_form}")
        write_snapshot(studio, "beta_metro", STEPS, {})

    lines = run_step(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE}, FakeRun(leaky_builder))

    assert list((studio / "snapshots").iterdir()) == []
    assert "a value of CENSUS_API_KEY was found in the snapshot files: every snapshot was removed" in lines
    assert_no_secret_value(lines)


def test_error_type_needs_a_traceback() -> None:
    assert error_type_from(b"GET https://x failed (attempt 1)\n") is None
    assert error_type_from(b"Traceback (most recent call last):\nKeyError: 'x'\n") == "KeyError"
    assert error_type_from(None) is None


@pytest.mark.parametrize(
    ("last_line", "expected"),
    [
        ("KeyboardInterrupt", "KeyboardInterrupt"),
        ("SystemExit: 1", "SystemExit"),
        ("uwcopilot.studio.data.http.UpstreamError: HTTP 500", "uwcopilot.studio.data.http.UpstreamError"),
        # A bare token is not an exception name: a key on a line of its own must never be echoed.
        ("a3f90000000000000000000000000000000000ff", "unknown"),
        ("A3F90000000000000000000000000000000000FF", "unknown"),
        ("lowercase.name: message", "unknown"),
        ("X" * 101 + "Error: too long", "unknown"),
    ],
)
def test_error_type_accepts_only_exception_class_names(last_line: str, expected: str) -> None:
    stderr = f'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: county 48201:\n{last_line}\n'
    assert error_type_from(stderr.encode()) == expected


def test_a_printed_line_that_holds_a_secret_is_withheld() -> None:
    lines: list[str] = []
    emit = guarded_emit(lines.append, {"CENSUS_API_KEY": CENSUS_VALUE})

    emit("houston_tx: snapshot 2026-10-14 kept")
    emit(f"stopped with {CENSUS_VALUE}")
    emit("blob " + base64.b64encode(b"key=" + CENSUS_VALUE.encode()).decode())

    assert lines == [
        "houston_tx: snapshot 2026-10-14 kept",
        "(a line was withheld because it contained a value of CENSUS_API_KEY)",
        "(a line was withheld because it contained a value of CENSUS_API_KEY)",
    ]


def test_a_crash_whose_name_is_the_secret_is_never_printed(tmp_path: Path) -> None:
    # A made-up key shaped like a class name: error_type_from accepts it, and the guard still withholds the line.
    shaped_like_a_class = "CensusKeyShapedLikeAClassName0123456789"
    stderr = f"Traceback (most recent call last):\n{shaped_like_a_class}: boom\n".encode()

    lines = run_step(tmp_path, {"CENSUS_API_KEY": shaped_like_a_class}, FakeRun(stderr=stderr))

    assert shaped_like_a_class not in "\n".join(lines)
    assert "(a line was withheld because it contained a value of CENSUS_API_KEY)" in lines


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        (
            [{"name": "tract_geography", "status": "ok"}, {"name": "acs_tracts", "status": "failed"}],
            "acs_tracts is failed",
        ),
        ([{"name": "tract_geography", "status": "ok"}], "acs_tracts is missing"),
        (
            [{"name": "tract_geography", "status": "not_available"}, {"name": "acs_tracts", "status": "ok"}],
            "tract_geography is not available",
        ),
    ],
    ids=["acs-failed", "acs-missing", "tracts-not-available"],
)
def test_a_snapshot_without_its_tracts_or_their_acs_values_is_removed(
    tmp_path: Path, steps: list[dict[str, Any]], reason: str
) -> None:
    # A wrong or not yet active key: the keyless steps succeed, ACS fails. Such a snapshot would offer area
    # suggestions and simulations that answer 409, so the metro stays at "no data snapshot" instead.
    studio = tmp_path / "studio"

    def builder() -> None:
        write_snapshot(studio, "alpha_metro", [*steps, {"name": "lehd_daytime_workers", "status": "ok"}], {})
        write_snapshot(studio, "beta_metro", STEPS, {})

    lines = run_step(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE}, FakeRun(builder))

    assert sorted(path.name for path in (studio / "snapshots").iterdir()) == ["beta_metro"]
    assert f"removed incomplete snapshot alpha_metro/2026-10-14: required step {reason}" in lines
    assert "  alpha_metro: no snapshot (runs in limited mode: no data snapshot)" in lines


def test_the_required_steps_are_steps_the_snapshot_builder_runs() -> None:
    source = inspect.getsource(build_snapshot)
    for name in REQUIRED_STEPS:
        assert f'run_step(build, "{name}"' in source, name


def test_a_secret_inside_a_compressed_npz_member_removes_every_snapshot(tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("segments.npy", b"\x93NUMPY" + b"0" * 200 + CENSUS_VALUE.encode() + b"0" * 200)
    assert CENSUS_VALUE.encode() not in buffer.getvalue()  # compressed: a raw search alone would miss it

    def leaky_builder() -> None:
        write_snapshot(studio, "alpha_metro", STEPS, {"roads.npz": buffer.getvalue()})

    lines = run_step(tmp_path, {"CENSUS_API_KEY": CENSUS_VALUE}, FakeRun(leaky_builder))

    assert list((studio / "snapshots").iterdir()) == []
    assert "a value of CENSUS_API_KEY was found in the snapshot files: every snapshot was removed" in lines


def test_snapshot_problem_accepts_a_complete_folder(tmp_path: Path) -> None:
    folder = write_snapshot(tmp_path, "alpha_metro", STEPS, {"places.json": b"{}"})
    assert snapshot_problem(folder) is None
    (folder / "tracts.json").write_text("{not json")
    assert snapshot_problem(folder) == "manifest.json or tracts.json is not valid JSON"


def test_main_without_secrets_prints_the_skip_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = build_studio_snapshots.main(
        ["--secrets-dir", str(tmp_path / "none"), "--studio-dir", str(tmp_path / "studio"), "--timeout-s", "5"],
        environ={},
    )

    assert code == 0
    assert "Studio snapshots skipped" in capsys.readouterr().out
    assert (tmp_path / "studio" / "snapshots").is_dir()


def test_the_real_metros_are_the_public_ones() -> None:
    ids = real_metro_ids()
    assert ids and "synthetic_metro" not in ids


# --- The Dockerfile stage that runs the script -------------------------------------------------------------------


def dockerfile_stage(name: str) -> str:
    """The text of one stage of the real Dockerfile (from its FROM line to the next FROM)."""
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(rf"^FROM [^\n]* AS {name}\n(.*?)(?=^FROM |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, f"no stage {name} in the Dockerfile"
    return match.group(1)


def test_the_snapshot_stage_mounts_exactly_the_build_secrets_and_only_as_optional_secrets() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    stage = dockerfile_stage("studio-snapshots")
    mounted = set(re.findall(r"--mount=type=secret,id=([A-Z_]+),required=false", stage))

    assert mounted == set(BUILD_SECRETS)
    assert REQUIRED_SECRET in mounted
    assert "python scripts/build_studio_snapshots.py --secrets-dir /run/secrets" in stage
    assert "timeout " in stage  # an outer time limit around the whole step
    # A key never becomes an ARG or ENV (both are visible in the image's history and config) and is never echoed.
    for name in (*BUILD_SECRETS, "BLS_API_KEY", "HUD_USER_TOKEN", "ORS_API_KEY", "KAGGLE_API_TOKEN"):
        assert not re.search(rf"^\s*(ARG|ENV)\b[^\n]*\b{name}\b", dockerfile, flags=re.MULTILINE)
        assert f"${name}" not in dockerfile and f"${{{name}}}" not in dockerfile
    assert "ARG UWC_GIT_SHA\n" in stage  # every new commit re-runs the step (secrets are not part of the cache key)


def test_the_runtime_copies_only_the_snapshots_from_that_stage() -> None:
    runtime = dockerfile_stage("runtime")
    copies = re.findall(r"^COPY --from=studio-snapshots (\S+) (\S+)$", runtime, flags=re.MULTILINE)

    assert copies == [("/app/data/studio/snapshots", "./data/studio/snapshots")]
