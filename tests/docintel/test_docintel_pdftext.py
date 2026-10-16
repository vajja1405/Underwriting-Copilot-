"""Tests for the text-layer reader (docintel/pdftext.py) on rendered SYNTHETIC packets."""

import io

import pdfplumber
from docintel_pipeline_fixtures import built_packet
from reportlab.pdfgen.canvas import Canvas

from uwcopilot.docintel.pdftext import MIN_TEXT_CHARS, page_sizes, read_pdf_text, read_text_layer


def test_text_layer_lines_match_the_printed_rows() -> None:
    path, truth = built_packet("PKT-C-015", "credit")
    pages = read_pdf_text(path)
    assert len(pages) == len(truth.pages)
    for page, expected in zip(pages, truth.pages, strict=True):
        assert page is not None, f"page {expected.page_number} should have a text layer"
        assert [line.text for line in page.lines] == expected.text_lines
        assert all(line.confidence == 1.0 for line in page.lines)
        assert page.source == "text_layer"


def test_word_boxes_match_the_oracle_citations() -> None:
    path, truth = built_packet("PKT-C-015", "credit")
    page = read_pdf_text(path)[0]
    application = truth.oracle_extraction["application"]
    cited = application["requested_amount"]
    line = page.lines[cited["line"] - 1]
    boxes = [word.bbox for word in line.words]
    assert tuple(cited["bbox"]) in boxes


def test_scanned_pages_have_no_text_layer() -> None:
    path, truth = built_packet("PKT-C-009", "credit")
    pages = read_pdf_text(path)
    for page, expected in zip(pages, truth.pages, strict=True):
        assert (page is None) == expected.scanned


def test_a_nearly_empty_page_counts_as_no_text_layer() -> None:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, invariant=1)
    canvas.drawString(72, 720, "x" * (MIN_TEXT_CHARS - 1))
    canvas.showPage()
    canvas.drawString(72, 720, "y" * MIN_TEXT_CHARS)
    canvas.save()
    buffer.seek(0)
    with pdfplumber.open(buffer) as pdf:
        assert read_text_layer(pdf.pages[0], 1) is None
        assert read_text_layer(pdf.pages[1], 2) is not None


def test_page_sizes_include_landscape_pages() -> None:
    path, truth = built_packet("PKT-I-013", "insurance")
    sizes = page_sizes(path)
    assert len(sizes) == len(truth.pages)
    schedule_pages = [page.page_number for page in truth.pages if page.doc_type == "property_schedule"]
    width, height = sizes[schedule_pages[0] - 1]
    assert width > height
