"""Where Studio keeps downloaded data on disk (all under data/, which is gitignored: raw data is never committed).

- data/studio/cache/      one file pair per HTTP response (body + metadata with SHA-256 and request date);
- data/studio/snapshots/  one folder per metro and snapshot date, written by `make studio-data METRO=<id>`;
- data/studio/reference/  reference files such as the Census CBSA delineation file.
"""

from pathlib import Path

from uwcopilot.common.paths import DATA_DIR

STUDIO_DATA_DIR: Path = DATA_DIR / "studio"
CACHE_DIR: Path = STUDIO_DATA_DIR / "cache"
SNAPSHOTS_DIR: Path = STUDIO_DATA_DIR / "snapshots"
REFERENCE_DIR: Path = STUDIO_DATA_DIR / "reference"
# Every downloaded file also gets a line here (the repo-wide rule from common.files.append_checksum_log).
CHECKSUM_LOG: Path = STUDIO_DATA_DIR / "checksums.jsonl"
