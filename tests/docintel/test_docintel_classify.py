"""Tests for page classification and document segmentation (docintel/classify.py)."""

import pytest
from docintel_pipeline_fixtures import built_packet

from uwcopilot.docintel.classify import (
    classify_page,
    page_counter,
    segment_pages,
    statement_period,
)
from uwcopilot.docintel.layout import PageText, Word, make_line
from uwcopilot.docintel.pdftext import read_pdf_text
from uwcopilot.docintel.truth import PacketTruth


def page_from_lines(number: int, texts: list[str], source: str = "text_layer") -> PageText:
    """A PageText whose lines hold the given texts (one word box per whitespace-separated word)."""
    lines = []
    for index, text in enumerate(texts, start=1):
        words = [
            Word(
                text=token, x0=54.0 + 30 * position, top=20.0 * index, x1=80.0 + 30 * position, bottom=20.0 * index + 9
            )
            for position, token in enumerate(text.split())
        ]
        lines.append(make_line(index, words))
    return PageText(page_number=number, width=612.0, height=792.0, source=source, lines=lines)


def text_pages(packet_id: str, line: str) -> tuple[list[PageText], PacketTruth]:
    """The text-layer pages of a packet without scanned pages, and its truth."""
    path, truth = built_packet(packet_id, line)
    pages = read_pdf_text(path)
    assert all(page is not None for page in pages)
    return [page for page in pages if page is not None], truth


def truth_documents(truth: PacketTruth) -> list[tuple[str, str, list[int]]]:
    """(doc_id, doc_type, pages) of every document present in the packet."""
    return [(item.doc_id, item.doc_type, item.pages) for item in truth.documents if item.pages]


@pytest.mark.parametrize(("packet_id", "line"), [("PKT-C-015", "credit"), ("PKT-I-013", "insurance")])
def test_every_page_and_document_matches_the_truth(packet_id: str, line: str) -> None:
    pages, truth = text_pages(packet_id, line)
    segments, classes = segment_pages(pages, line)
    assert [item.doc_type for item in classes] == [page.doc_type for page in truth.pages]
    assert [(item.doc_id, item.doc_type, item.page_numbers) for item in segments] == truth_documents(truth)
    assert all(segment.confidence > 0.5 for segment in segments)
    info = segments[0].info()
    assert info.ocr_used is False
    assert info.pages == segments[0].page_numbers


@pytest.mark.parametrize(("packet_id", "line"), [("PKT-C-015", "credit"), ("PKT-I-013", "insurance")])
def test_segmentation_without_footers_uses_periods_carriers_and_one_page_types(packet_id: str, line: str) -> None:
    pages, truth = text_pages(packet_id, line)
    stripped = [page.model_copy(update={"lines": page.lines[:-1]}) for page in pages]
    assert all(page_counter(page) is None for page in stripped)
    segments, _ = segment_pages(stripped, line)
    assert [(item.doc_id, item.doc_type, item.page_numbers) for item in segments] == truth_documents(truth)


def test_footer_counter() -> None:
    assert page_counter(page_from_lines(1, ["Revenue $1.00", "SYNTHETIC - for demonstration only Page 2 of 3"])) == (
        2,
        3,
    )
    assert page_counter(page_from_lines(1, ["Page 1 of 1", "SYNTHETIC for demonstration only"])) == (1, 1)
    assert page_counter(page_from_lines(1, ["Page 4 of 3"])) is None
    assert page_counter(page_from_lines(1, ["no footer here"])) is None


def test_a_continuation_page_takes_its_document_type_from_the_footer() -> None:
    first = page_from_lines(
        1, ["NORTHWIND BUSINESS CREDIT - SMALL BUSINESS LOAN APPLICATION (SYNTHETIC)", "Page 1 of 2"]
    )
    second = page_from_lines(2, ["Profit and Loss words that would mislead", "Page 2 of 2"])
    segments, classes = segment_pages([first, second], "credit")
    assert len(segments) == 1
    assert classes[1].doc_type == "loan_application"
    assert classes[1].confidence == 0.0


def test_a_page_without_any_keyword_continues_the_previous_type() -> None:
    previous = classify_page(page_from_lines(1, ["Statement period 2026-01-01 to 2026-01-31"]), "credit")
    blank = classify_page(page_from_lines(2, ["nothing useful"]), "credit", previous.doc_type)
    assert previous.doc_type == "bank_statement"
    assert blank.doc_type == "bank_statement"
    assert blank.confidence == 0.0


def test_ocr_pages_match_keywords_approximately() -> None:
    texts = ["Busines Checking Statemnt", "Statement perod 2026-01-01 to 2026-01-31", "Closing balanse $1.00"]
    result = classify_page(page_from_lines(1, texts, source="ocr"), "credit")
    assert result.doc_type == "bank_statement"


def test_only_the_packet_lines_types_compete() -> None:
    page = page_from_lines(1, ["Loss summary", "Valued as of 2026-01-01", "Carrier-produced loss run"])
    assert classify_page(page, "insurance").doc_type == "loss_run"
    assert classify_page(page, "credit").doc_type != "loss_run"


def test_statement_period_key() -> None:
    page = page_from_lines(1, ["Statement period 2026-01-01 to 2026-01-31"])
    assert statement_period(page) == "2026-01-01 2026-01-31"
    assert (
        statement_period(page_from_lines(1, ["Statement period 2O26-01-01 to 2026-01-31"])) == "2026-01-01 2026-01-31"
    )
    assert statement_period(page_from_lines(1, ["Opening balance $1.00"])) is None
