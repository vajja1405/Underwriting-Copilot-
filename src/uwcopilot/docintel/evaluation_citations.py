"""Citation evaluation: does each extracted value point at printed text that supports it? (Phase 2)

Three groups of counters, each per field type ("all", "money", "date", ...), all summed over packets:

- citations.page.<kind> (total / correct / same_page): for every CORRECT value (a true positive), whether the
  cited page's truth text lines print the value (evaluation.printed_forms), and whether the cited page is the
  value's true page. This is only a sanity check for the rules extractor: it cites the page it read the value from,
  so a correct value fails only when printed_forms misses a format. It matters for an LLM extractor, which could
  cite a page it did not read.
- citations.supported.<kind> (total / correct): for EVERY extracted value that cites a page, correct or not,
  whether the cited page's truth text prints the EXTRACTED value. An OCR misread ("41" read for "1") fails: no
  line prints it. A right value put in the wrong field still passes (its text is on the page).
- citations.line.<kind> (total / correct): for every correct value whose true position is known, whether the cited
  LINE is the value's true line.
  * Text-layer page: the cited line number equals the oracle's line number (the text layer's reading order is the
    renderer's line order, so the numbers are comparable).
  * Scanned page: OCR line numbers are not comparable, so boxes are. A scanned page is slightly rotated and
    shifted, so page_alignment first fits, by least squares over the page's correct values, the straight-line map
    from an oracle box centre (x, y) to the cited box centre: y' = a x + b y + c (and the same for x'). A citation
    is on the right line when its centre's vertical distance from the mapped oracle centre is at most
    LINE_TOLERANCE_PT (half a printed line). A page with fewer than MIN_ALIGNMENT_POINTS correct values is not
    scored (its values are counted in citations.line_unscored).
"""

from collections import Counter
from typing import Any, NamedTuple

import numpy as np

from uwcopilot.docintel.schemas import flatten_fields, parse_extraction

LINE_TOLERANCE_PT = 5.0  # half of the ~10-12 point line pitch of the documents
MIN_ALIGNMENT_POINTS = 6  # values needed to fit a scanned page's alignment (3 unknowns per coordinate)

Box = tuple[float, float, float, float]


class Located(NamedTuple):
    """A value with its citation: field, key, value, page, line and box (None when unknown)."""

    field: str
    key: str | None
    value: Any
    page: int | None
    line: int | None
    bbox: Box | None


def centre(box: Box) -> tuple[float, float]:
    """The centre (x, y) of a box (x0, top, x1, bottom)."""
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def oracle_positions(oracle_extraction: dict[str, Any]) -> dict[tuple[str, str | None], list[Located]]:
    """The oracle extraction's values with their true page, line and box, grouped by (field, key)."""
    grouped: dict[tuple[str, str | None], list[Located]] = {}
    for row in flatten_fields(parse_extraction(oracle_extraction)):
        item = Located(row.field, row.key, row.value, row.page, row.line, row.bbox)
        grouped.setdefault((row.field, row.key), []).append(item)
    return grouped


def true_position(found: Any, oracle: dict[tuple[str, str | None], list[Located]], same: Any) -> Located | None:
    """The oracle entry of a correct value: same field, key and page, and an equal value (same(found, oracle))."""
    for item in oracle.get((found.field, found.key), []):
        if item.page == found.page and same(found, item):
            return item
    return None


def page_alignment(pairs: list[tuple[Box, Box]]) -> tuple[np.ndarray, np.ndarray] | None:
    """Least-squares maps oracle centre -> cited centre for one scanned page: (x coefficients, y coefficients),
    each [a, b, c] for a * x + b * y + c. None with fewer than MIN_ALIGNMENT_POINTS pairs."""
    if len(pairs) < MIN_ALIGNMENT_POINTS:
        return None
    sources = np.array([[*centre(oracle), 1.0] for oracle, _ in pairs])
    targets = np.array([centre(cited) for _, cited in pairs])
    x_coefficients = np.linalg.lstsq(sources, targets[:, 0], rcond=None)[0]
    y_coefficients = np.linalg.lstsq(sources, targets[:, 1], rcond=None)[0]
    return x_coefficients, y_coefficients


def on_true_line_scanned(oracle: Box, cited: Box, alignment: tuple[np.ndarray, np.ndarray]) -> bool:
    """True when the cited box centre is within LINE_TOLERANCE_PT (vertically) of the mapped oracle centre."""
    x, y = centre(oracle)
    mapped_y = float(alignment[1] @ np.array([x, y, 1.0]))
    return abs(centre(cited)[1] - mapped_y) <= LINE_TOLERANCE_PT


def line_counts(matched: list[tuple[Located, Located, str]], scanned_pages: set[int], kind_of: Any) -> Counter:
    """citations.line.* counters from (cited, oracle, field) triples of correct values (module docstring)."""
    counts: Counter = Counter()
    by_page: dict[int, list[tuple[Located, Located, str]]] = {}
    for cited, oracle, field in matched:
        if cited.page is None:
            continue
        by_page.setdefault(cited.page, []).append((cited, oracle, field))
    for page, triples in by_page.items():
        alignment = None
        if page in scanned_pages:
            boxes = [(oracle.bbox, cited.bbox) for cited, oracle, _ in triples if oracle.bbox and cited.bbox]
            alignment = page_alignment(boxes)
        for cited, oracle, field in triples:
            if page in scanned_pages:
                if alignment is None or oracle.bbox is None or cited.bbox is None:
                    counts["citations.line_unscored"] += 1
                    continue
                right = on_true_line_scanned(oracle.bbox, cited.bbox, alignment)
            else:
                right = cited.line is not None and cited.line == oracle.line
            for kind in ("all", kind_of(field)):
                counts[f"citations.line.{kind}.total"] += 1
                counts[f"citations.line.{kind}.correct"] += int(right)
    return counts
