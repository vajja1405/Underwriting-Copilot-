"""One Ollama job at a time: a file lock that every script calling the local model holds for its whole run.

The local Ollama server answers one request at a time on CPU. Two evaluation jobs running together would slow
each other down and make their latency numbers meaningless, so each script that calls Ollama wraps its run in

    with ollama_queue_lock("scripts/eval_generation.py --config qwen3b_v1_k3"):
        ...

The lock is an exclusive fcntl.flock lock on data/cache/ollama.lock (the shared contract in docs/innovations.md,
section 4). The operating system releases it when the process exits, even after a crash, so a leftover lock file
never blocks the next run. While holding the lock, the file says who holds it (pid, label, time), which is what a
waiting script prints. fcntl is POSIX-only (Linux, macOS), like the rest of the project's tooling.

One server, many checkouts: the default lock file lives inside this checkout, but the Ollama server serves the
whole machine. Jobs started from another checkout (a git worktree, or a parallel agent's copy of the repo) take a
different default file and would run alongside each other. To serialize them, set UWC_OLLAMA_LOCK_PATH to the
same absolute path (for example /tmp/uwcopilot-ollama.lock) in every checkout; see .env.example.
"""

import fcntl
import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from uwcopilot.common.paths import DATA_DIR, ENV_FILE
from uwcopilot.common.settings import env_reader
from uwcopilot.llm.settings import SettingsError

DEFAULT_LOCK_PATH: Path = DATA_DIR / "cache" / "ollama.lock"
LOCK_PATH_VARIABLE = "UWC_OLLAMA_LOCK_PATH"
DEFAULT_POLL_INTERVAL_S = 1.0


class OllamaQueueBusy(TimeoutError):
    """Another job held the Ollama queue lock for longer than the caller was willing to wait."""


@dataclass(frozen=True)
class QueueHolder:
    """Who holds the lock: written into the lock file while it is held."""

    pid: int
    label: str
    acquired_at_utc: str
    waited_s: float


def resolve_lock_path(environ: Mapping[str, str] | None = None) -> Path:
    """The lock file every Ollama job should use: UWC_OLLAMA_LOCK_PATH if it is set, else DEFAULT_LOCK_PATH.

    The variable is read like every project setting (the repo .env file wins over the process environment).
    It must be an absolute path, so that every checkout points at the same file.
    """
    value = env_reader(ENV_FILE, environ)(LOCK_PATH_VARIABLE)
    if value is None:
        return DEFAULT_LOCK_PATH
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SettingsError(f"{LOCK_PATH_VARIABLE} must be an absolute path (got {value!r}).")
    return path


def read_lock_holder(lock_path: Path | None = None) -> dict[str, object] | None:
    """The holder record in the lock file, or None if the file is missing, empty or unreadable.

    lock_path=None means resolve_lock_path(). The record can be stale (the holder exited and the OS released the
    lock); only the lock itself is reliable.
    """
    lock_path = lock_path or resolve_lock_path()
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def try_lock(handle: TextIO) -> bool:
    """Take the exclusive lock without waiting. Returns False if another open file holds it."""
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def wait_for_lock(
    handle: TextIO, lock_path: Path, label: str, timeout_s: float | None, poll_interval_s: float
) -> float:
    """Poll until the lock is ours and return the seconds waited; raise OllamaQueueBusy after timeout_s.

    timeout_s=None waits as long as it takes. A note is printed to stderr once, when waiting starts.
    """
    started = time.monotonic()
    announced = False
    while not try_lock(handle):
        waited = time.monotonic() - started
        holder = read_lock_holder(lock_path)
        if timeout_s is not None and waited >= timeout_s:
            raise OllamaQueueBusy(
                f"The Ollama queue lock {lock_path} is still held after {waited:.0f} s (holder: {holder}). "
                f"{label!r} did not start."
            )
        if not announced:
            print(f"Waiting for the Ollama queue lock {lock_path} (holder: {holder}) ...", file=sys.stderr)
            announced = True
        time.sleep(poll_interval_s)
    return time.monotonic() - started


@contextmanager
def ollama_queue_lock(
    label: str,
    lock_path: Path | None = None,
    timeout_s: float | None = None,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Iterator[QueueHolder]:
    """Hold the Ollama queue lock for the duration of the with-block.

    label says which job holds it (for example the script name and config id). lock_path=None means
    resolve_lock_path() (UWC_OLLAMA_LOCK_PATH, else data/cache/ollama.lock). timeout_s=None waits until the
    lock is free; otherwise OllamaQueueBusy is raised after that many seconds and the block never runs.
    """
    lock_path = lock_path or resolve_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" opens without truncating, so the current holder's record is kept until this process owns the lock.
    with lock_path.open("a+", encoding="utf-8") as handle:
        waited = wait_for_lock(handle, lock_path, label, timeout_s, poll_interval_s)
        holder = QueueHolder(
            pid=os.getpid(),
            label=label,
            acquired_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            waited_s=round(waited, 3),
        )
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(asdict(holder)) + "\n")
            handle.flush()
            yield holder
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)
