"""Tests for rendered SYNTHETIC packets: truth validity, determinism, cited values on the page, footer, scanning."""

import hashlib
import io
import json
import re
from datetime import date

import pdfplumber
import pytest

from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import format_date, packet_seed
from uwcopilot.docintel.truth import SYNTHETIC_FOOTER, FieldTruth, PacketTruth

PACKETS = [("PKT-C-001", "credit"), ("PKT-C-002", "credit"), ("PKT-I-001", "insurance"), ("PKT-I-002", "insurance")]
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEXT_BOOLEANS = {"is_control_person": "control person", "carrier_produced": "carrier-produced"}


@pytest.fixture(scope="module")
def packets() -> dict[str, tuple[bytes, PacketTruth]]:
    """Four drawn packets (two per line), seed 42."""
    built = {}
    for packet_id, line in PACKETS:
        pdf, truth = build_packet(packet_id, line, packet_seed(42, packet_id), "dev", draw=True)
        built[packet_id] = (pdf, truth)
    return built


def candidates(row: FieldTruth) -> list[str]:
    """Ways a value can be printed (casefolded): money, numbers, percents, dates in three styles, code labels."""
    value = row.value
    name = row.field.rsplit(".", 1)[-1]
    if isinstance(value, bool):
        return [("yes" if value else "no"), TEXT_BOOLEANS.get(name, "\0")]
    if isinstance(value, int):
        return [str(value), f"{value:,}"]
    if isinstance(value, float):
        return [f"{abs(value):,.2f}", f"{value:.1f}%", f"{value:.2f}%"]
    text = str(value)
    if ISO_DATE.match(text):
        day = date.fromisoformat(text)
        return [format_date(day, style).casefold() for style in ("iso", "mdy", "dmy_abbrev")]
    return [text.casefold(), text.replace("_", " ").casefold()]


def test_truth_validates_and_round_trips(packets: dict) -> None:
    for _, truth in packets.values():
        again = PacketTruth.model_validate_json(json.dumps(truth.model_dump(mode="json")))
        assert again == truth
        assert truth.pdf_file == "packet.pdf" and len(truth.pdf_sha256) == 64


def test_pdf_hash_matches_truth(packets: dict) -> None:
    for pdf, truth in packets.values():
        assert hashlib.sha256(pdf).hexdigest() == truth.pdf_sha256


@pytest.mark.parametrize("packet_id,line", [("PKT-C-001", "credit"), ("PKT-I-002", "insurance")])
def test_same_seed_gives_identical_pdf_and_truth(packets: dict, packet_id: str, line: str) -> None:
    pdf, truth = build_packet(packet_id, line, packet_seed(42, packet_id), "dev", draw=True)
    first_pdf, first_truth = packets[packet_id]
    assert pdf == first_pdf
    assert truth.model_dump(mode="json") == first_truth.model_dump(mode="json")


def test_every_field_value_is_printed_on_its_page(packets: dict) -> None:
    for _, truth in packets.values():
        pages = {page.page_number: page.text_lines for page in truth.pages}
        assert truth.fields
        for row in truth.fields:
            lines = [line.casefold() for line in pages[row.page_number]]
            joined = " ".join(lines)
            found = any(option in line for option in candidates(row) for line in [*lines, joined])
            assert found, f"{truth.packet_id} {row.field} {row.value!r} not on page {row.page_number}"


def test_footer_on_every_page(packets: dict) -> None:
    for _, truth in packets.values():
        documents = {document.doc_id: document for document in truth.documents}
        for page in truth.pages:
            footer = page.text_lines[-1]
            total = len(documents[page.doc_id].pages)
            assert footer == f"{SYNTHETIC_FOOTER} Page {page.page_in_doc} of {total}"


def test_scanned_pages_have_no_text_layer_and_clean_pages_do(packets: dict) -> None:
    scanned_seen = clean_seen = 0
    for packet_id in ("PKT-C-001", "PKT-I-001"):
        pdf, truth = packets[packet_id]
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            for page_truth, page in zip(truth.pages, document.pages, strict=True):
                characters = len(page.chars)
                if page_truth.scanned:
                    scanned_seen += 1
                    assert characters == 0
                else:
                    clean_seen += 1
                    assert characters > 20
    assert scanned_seen > 0 and clean_seen > 0


def test_footer_text_is_in_the_text_layer_of_clean_pages(packets: dict) -> None:
    pdf, truth = packets["PKT-C-002"]
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        for page_truth, page in zip(truth.pages, document.pages, strict=True):
            if not page_truth.scanned:
                assert SYNTHETIC_FOOTER in (page.extract_text() or "")


def test_documents_cover_pages_in_order(packets: dict) -> None:
    for _, truth in packets.values():
        numbers = [number for document in truth.documents for number in document.pages]
        assert numbers == list(range(1, len(truth.pages) + 1))
        assert [document.doc_id for document in truth.documents] == [
            f"D{index:02d}" for index in range(1, len(truth.documents) + 1)
        ]
