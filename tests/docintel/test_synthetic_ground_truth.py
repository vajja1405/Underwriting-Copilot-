"""Independent checks of the SYNTHETIC ground truth against the rendered PDF (not against the generator's own lists).

test_synthetic_packets.py checks that each truth value appears somewhere in the generator's own text_lines. These
tests go further, on rendered master-seed-42 packets (not the evaluation set):
- the PDF text layer (read with pdfplumber, as a reader would) prints exactly the truth's text_lines of every clean
  page, and nothing else;
- every value of the oracle extraction cites its own line: source_text is text_lines[line - 1] (a free-text value
  that spans several lines starts with its first line), the value is printed on that line, and a negative amount is
  printed with its minus sign (or in parentheses).
So a generator change that moves a value to another line, drops a sign, or prints something its truth does not
record fails here.
"""

import re
from collections.abc import Iterator
from typing import Any

import pdfplumber
import pytest
from docintel_pipeline_fixtures import built_packet

from uwcopilot.docintel.evaluation import printed_forms

PACKETS = [("PKT-C-015", "credit"), ("PKT-I-013", "insurance"), ("PKT-C-009", "credit")]


def squeezed(text: str) -> str:
    """Text without whitespace, casefolded (pdfplumber and the renderer may space words differently)."""
    return re.sub(r"\s+", "", text).casefold()


def cited_values(node: Any, path: str = "") -> Iterator[tuple[str, dict]]:
    """(field path, cited dict) for every found Cited value of an oracle extraction dict."""
    if isinstance(node, dict):
        if {"value", "page", "line", "source_text"} <= set(node):
            if node["value"] is not None:
                yield path, node
            return
        for key, child in node.items():
            yield from cited_values(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for child in node:
            yield from cited_values(child, path)


@pytest.mark.parametrize(("packet_id", "line"), PACKETS)
def test_the_text_layer_prints_exactly_the_truth_lines_of_each_clean_page(packet_id: str, line: str) -> None:
    path, truth = built_packet(packet_id, line)
    with pdfplumber.open(path) as pdf:
        assert len(pdf.pages) == len(truth.pages)
        for page_truth, page in zip(truth.pages, pdf.pages, strict=True):
            if page_truth.scanned:
                assert len((page.extract_text() or "").strip()) < 20  # no text layer
                continue
            printed = page.extract_text() or ""
            for text_line in page_truth.text_lines:
                assert squeezed(text_line) in squeezed(printed), (packet_id, page_truth.page_number, text_line)
            recorded = squeezed("".join(page_truth.text_lines))
            for printed_line in printed.splitlines():
                assert squeezed(printed_line) in recorded, (packet_id, page_truth.page_number, printed_line)


@pytest.mark.parametrize(("packet_id", "line"), PACKETS)
def test_every_oracle_value_is_printed_on_its_own_line_with_its_sign(packet_id: str, line: str) -> None:
    _, truth = built_packet(packet_id, line)
    pages = {page.page_number: page.text_lines for page in truth.pages}
    checked = 0
    for field, cited in cited_values(truth.oracle_extraction):
        text_line = pages[cited["page"]][cited["line"] - 1]
        source = cited["source_text"]
        assert source == text_line or source.startswith(text_line), (packet_id, field, source, text_line)
        value = cited["value"]
        if isinstance(value, bool):
            continue
        forms = printed_forms(field, value)
        assert any(form in source.casefold() for form in forms), (packet_id, field, value, source)
        if isinstance(value, float) and value < 0 and field.split(".")[-1] not in ("ownership_pct",):
            amount = f"{abs(value):,.2f}"
            before = source[: source.index(amount)][-2:]
            assert "-" in before or "(" in before, (packet_id, field, value, source)
        checked += 1
    assert checked > 50
