"""Shared helpers for the docintel pipeline tests: SYNTHETIC packets built once per test run, and field scoring.

Packets are built with the generator (tests may import uwcopilot.docintel.synthetic and truth; pipeline modules
may not). The rendered packets used here come from master seed 42, which is not the evaluation set (`make
packets` uses seed 20261013 since generator v2), so nothing is tuned on the evaluation set.
"""

import atexit
import shutil
import tempfile
from functools import cache
from pathlib import Path

from uwcopilot.docintel.pipeline import PacketResult, run_packet
from uwcopilot.docintel.schemas import PacketExtraction, flatten_fields
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.truth import PacketTruth

MASTER_SEED = 42
# Dev-split packets: two text-layer-only packets and two with one scanned page each.
TEXT_ONLY_PACKETS = (("PKT-C-015", "credit"), ("PKT-I-013", "insurance"))
SCANNED_PACKETS = (("PKT-C-009", "credit"), ("PKT-I-009", "insurance"))
CACHE_DIR = Path(tempfile.mkdtemp(prefix="uwc_docintel_pipeline_tests_"))
atexit.register(shutil.rmtree, CACHE_DIR, ignore_errors=True)


def tesseract_available() -> bool:
    """True when the tesseract binary is installed."""
    return shutil.which("tesseract") is not None


@cache
def built_packet(packet_id: str, line: str) -> tuple[Path, PacketTruth]:
    """(path of the rendered PDF, truth) of one seed-42 packet, built once per test run."""
    pdf, truth = build_packet(packet_id, line, packet_seed(MASTER_SEED, packet_id), "dev", draw=True)
    path = CACHE_DIR / f"{packet_id}.pdf"
    path.write_bytes(pdf)
    return path, truth


@cache
def pipeline_result(packet_id: str, line: str) -> PacketResult:
    """The rules pipeline's result on one packet, computed once per test run."""
    path, _ = built_packet(packet_id, line)
    return run_packet(path, packet_id, line)


def normalized(value: object) -> object:
    """A value as the scoring compares it: text casefolded with single spaces, numbers rounded to cents."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return round(float(value), 2)
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


def field_counts(extraction: PacketExtraction, truth: PacketTruth, pages: set[int] | None = None) -> list[int]:
    """[true positives, false positives, false negatives] of (field, key, value) rows, optionally on some pages."""
    expected: dict[tuple, list] = {}
    for row in truth.fields:
        if pages is None or row.page_number in pages:
            expected.setdefault((row.field, row.key), []).append(normalized(row.value))
    found: dict[tuple, list] = {}
    for row in flatten_fields(extraction):
        if pages is None or row.page in pages:
            found.setdefault((row.field, row.key), []).append(normalized(row.value))
    counts = [0, 0, 0]
    for key in set(expected) | set(found):
        remaining = list(expected.get(key, []))
        for value in found.get(key, []):
            if value in remaining:
                remaining.remove(value)
                counts[0] += 1
            else:
                counts[1] += 1
        counts[2] += len(remaining)
    return counts


def f1_score(counts: list[int]) -> float:
    """F1 from [tp, fp, fn] (0.0 when nothing matched)."""
    true_positives, false_positives, false_negatives = counts
    if true_positives == 0:
        return 0.0
    return 2 * true_positives / (2 * true_positives + false_positives + false_negatives)
