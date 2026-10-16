"""Small file helpers shared by the packet store and the audit log: private writes and JSON Lines reading."""

import os
from pathlib import Path


def write_private_file(path: Path, data: bytes) -> None:
    """Write data to path (mode 0600) via a temporary file and a rename, so a crash never leaves half a file."""
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def jsonl_lines(text: str) -> list[str]:
    """The lines of a JSON Lines text, split on "\\n" only (a final empty piece, after the last "\\n", is dropped).

    str.splitlines() is not used: it also splits on U+2028, U+2029, U+0085 and other separators that JSON keeps raw
    inside a string (json.dumps with ensure_ascii=False), which would cut one record in two. json.dumps always
    escapes "\\n" inside strings, so "\\n" is the only line end in a JSON Lines file.
    """
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines
