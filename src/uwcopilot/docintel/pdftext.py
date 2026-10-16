"""Read the text layer of packet PDF pages with pdfplumber and turn it into PageText lines (docintel/layout.py).

pdfplumber gives words with their boxes in PDF points, top-left origin. Words are grouped into lines by their
vertical centre (layout.group_words_into_lines) and numbered top to bottom. Text-layer words have confidence 1.0.

A page with fewer than MIN_TEXT_CHARS characters is treated as having NO text layer (a scanned image page, or a
nearly empty page): read_text_layer returns None for it and the pipeline sends it to OCR (ocr.py).
"""

from pathlib import Path

import pdfplumber
from pdfplumber.page import Page

from uwcopilot.docintel.layout import PageText, Word, group_words_into_lines

MIN_TEXT_CHARS = 20
# pdfplumber's default word splitting (3 points) keeps a label and a value printed 20+ points apart as two words.
WORD_X_TOLERANCE = 3.0
WORD_Y_TOLERANCE = 3.0


def page_words(page: Page) -> list[Word]:
    """Every word of a pdfplumber page as a layout Word (confidence 1.0)."""
    words = []
    for item in page.extract_words(x_tolerance=WORD_X_TOLERANCE, y_tolerance=WORD_Y_TOLERANCE):
        words.append(
            Word(
                text=item["text"],
                x0=round(float(item["x0"]), 2),
                top=round(float(item["top"]), 2),
                x1=round(float(item["x1"]), 2),
                bottom=round(float(item["bottom"]), 2),
                conf=1.0,
            )
        )
    return words


def has_text_layer(page: Page) -> bool:
    """True when the page has at least MIN_TEXT_CHARS printed characters."""
    return len(page.chars) >= MIN_TEXT_CHARS


def read_text_layer(page: Page, page_number: int) -> PageText | None:
    """The page's text-layer lines, or None when it has no text layer (fewer than MIN_TEXT_CHARS characters)."""
    if not has_text_layer(page):
        return None
    lines = group_words_into_lines(page_words(page))
    return PageText(
        page_number=page_number,
        width=float(page.width),
        height=float(page.height),
        source="text_layer",
        lines=lines,
        char_count=len(page.chars),
    )


def read_pdf_text(pdf_path: Path) -> list[PageText | None]:
    """The text layer of every page of a PDF (None for pages without one), in page order."""
    results: list[PageText | None] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            results.append(read_text_layer(page, number))
    return results


def page_sizes(pdf_path: Path) -> list[tuple[float, float]]:
    """(width, height) in points of every page of a PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return [(float(page.width), float(page.height)) for page in pdf.pages]
