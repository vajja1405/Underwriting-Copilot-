"""Tests for uwcopilot.llm.ollama_queue: the file lock lets one Ollama job run at a time, across processes."""

import select
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from uwcopilot.llm import ollama_queue
from uwcopilot.llm.ollama_queue import (
    DEFAULT_LOCK_PATH,
    LOCK_PATH_VARIABLE,
    OllamaQueueBusy,
    ollama_queue_lock,
    read_lock_holder,
    resolve_lock_path,
)
from uwcopilot.llm.settings import SettingsError

CHILD_TIMEOUT_S = 60.0

# A child process: take the lock, write a "<out>.locked" marker, hold the lock until the release file exists,
# then write when it got the lock, when it let go, and how long it waited for it.
CHILD_HOLDS_LOCK = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from uwcopilot.llm.ollama_queue import ollama_queue_lock

    lock_path, out_path, release_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    with ollama_queue_lock(f"child {out_path.name}", lock_path=lock_path, poll_interval_s=0.05) as holder:
        start = time.time()
        out_path.with_suffix(".locked").write_text("locked")
        deadline = time.monotonic() + 60
        while not release_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        end = time.time()
    out_path.write_text(f"{start} {end} {holder.waited_s}")
    """
)

# A child process that takes the lock and dies without releasing it.
CHILD_CRASHES_WITH_LOCK = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from uwcopilot.llm.ollama_queue import ollama_queue_lock

    with ollama_queue_lock("crashing child", lock_path=Path(sys.argv[1])):
        Path(sys.argv[2]).write_text("locked")
        os._exit(1)
    """
)


def run_child(code: str, *args: str) -> subprocess.Popen[bytes]:
    """Start a Python child process running code with the given arguments."""
    return subprocess.Popen([sys.executable, "-c", code, *args], stderr=subprocess.PIPE)


def wait_for_file(path: Path, timeout_s: float = CHILD_TIMEOUT_S) -> None:
    """Wait until a child process has written path."""
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} was never written"
        time.sleep(0.02)


def wait_for_stderr_line(child: subprocess.Popen[bytes], text: str, timeout_s: float = CHILD_TIMEOUT_S) -> None:
    """Read the child's stderr until a line containing text appears."""
    assert child.stderr is not None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        readable, _, _ = select.select([child.stderr], [], [], 0.1)
        if readable:
            line = child.stderr.readline().decode("utf-8", errors="replace")
            assert line, "the child exited before printing the expected line"
            if text in line:
                return
    raise AssertionError(f"the child never printed {text!r}")


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore the repo .env file, so a test sees only the variables it sets itself."""
    monkeypatch.setattr(ollama_queue, "ENV_FILE", None)


def test_default_lock_path_is_the_shared_contract() -> None:
    assert DEFAULT_LOCK_PATH.parts[-3:] == ("data", "cache", "ollama.lock")


def test_lock_serializes_two_processes(tmp_path: Path) -> None:
    """The second job is started while the first holds the lock, and must wait until the first lets go."""
    lock_path = tmp_path / "ollama.lock"
    first_out, second_out = tmp_path / "first.txt", tmp_path / "second.txt"
    release_first, release_second = tmp_path / "release_first", tmp_path / "release_second"
    release_second.write_text("go")  # the second job lets go as soon as it has the lock

    first = run_child(CHILD_HOLDS_LOCK, str(lock_path), str(first_out), str(release_first))
    wait_for_file(first_out.with_suffix(".locked"))
    second = run_child(CHILD_HOLDS_LOCK, str(lock_path), str(second_out), str(release_second))
    wait_for_stderr_line(second, "Waiting for the Ollama queue lock")
    assert not second_out.with_suffix(".locked").exists(), "the second job got the lock while the first held it"
    release_first.write_text("go")

    assert first.wait(timeout=CHILD_TIMEOUT_S) == 0
    assert second.wait(timeout=CHILD_TIMEOUT_S) == 0
    first_end = float(first_out.read_text().split()[1])
    second_start, _, second_waited = (float(value) for value in second_out.read_text().split())
    assert second_start >= first_end, "the two jobs overlapped"
    assert second_waited > 0


def test_holder_is_recorded_while_held_and_cleared_after(tmp_path: Path) -> None:
    lock_path = tmp_path / "ollama.lock"
    with ollama_queue_lock("eval job", lock_path=lock_path) as holder:
        record = read_lock_holder(lock_path)
        assert record is not None
        assert record["label"] == "eval job"
        assert record["pid"] == holder.pid
    assert read_lock_holder(lock_path) is None


def test_waiting_longer_than_the_timeout_raises_busy(tmp_path: Path) -> None:
    lock_path = tmp_path / "ollama.lock"
    ran = []
    with ollama_queue_lock("first job", lock_path=lock_path):
        started = time.monotonic()
        with pytest.raises(OllamaQueueBusy, match="first job"):
            with ollama_queue_lock("second job", lock_path=lock_path, timeout_s=0.3, poll_interval_s=0.05):
                ran.append("second job")
        assert time.monotonic() - started >= 0.3
    assert ran == []
    with ollama_queue_lock("third job", lock_path=lock_path, timeout_s=0.3) as holder:
        assert holder.label == "third job"


def test_lock_is_released_when_the_holder_crashes(tmp_path: Path) -> None:
    lock_path = tmp_path / "ollama.lock"
    marker = tmp_path / "locked.txt"
    child = run_child(CHILD_CRASHES_WITH_LOCK, str(lock_path), str(marker))
    assert child.wait(timeout=60) == 1
    assert marker.read_text() == "locked"
    with ollama_queue_lock("after crash", lock_path=lock_path, timeout_s=2) as holder:
        assert holder.waited_s < 2


def test_lock_path_override_must_be_absolute(no_env_file: None, tmp_path: Path) -> None:
    assert resolve_lock_path(environ={}) == DEFAULT_LOCK_PATH
    shared = tmp_path / "machine-wide.lock"
    assert resolve_lock_path(environ={LOCK_PATH_VARIABLE: str(shared)}) == shared
    with pytest.raises(SettingsError, match="absolute"):
        resolve_lock_path(environ={LOCK_PATH_VARIABLE: "relative/ollama.lock"})


def test_jobs_in_two_checkouts_share_the_lock_named_by_the_variable(
    no_env_file: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "machine-wide" / "ollama.lock"
    monkeypatch.setenv(LOCK_PATH_VARIABLE, str(shared))
    with ollama_queue_lock("job from checkout A") as holder:
        assert read_lock_holder() == read_lock_holder(shared)
        assert read_lock_holder(shared)["label"] == holder.label
        with pytest.raises(OllamaQueueBusy):
            with ollama_queue_lock("job from checkout B", timeout_s=0.2, poll_interval_s=0.05):
                pass
