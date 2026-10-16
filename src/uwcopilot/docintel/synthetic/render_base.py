"""PageWriter: draws a SYNTHETIC document with reportlab and records exactly what it printed, line by line.

Every page is written top to bottom as a sequence of rows. A row is one printed line: one or more text segments
on the same baseline (a label and its value, or the cells of a table row). The writer records, for every row,
the joined text (PageTruth.text_lines, in reading order, so the row number is the 1-based line number) and the
bounding box of every segment. A renderer that prints a value asks the writer for a Cited value pointing at that
row and segment: that is how the oracle extraction gets its page, line, bbox and source text.

Every page ends with the footer row "SYNTHETIC - for demonstration only" and "Page i of n" (numbered within the
document). Pages are US Letter, portrait or landscape.

The writer also works without a canvas (draw=False): it lays out the same rows and records the same text and
citations but draws nothing. The --json-only mode uses that to build the truth without rendering a PDF.

Page counts: a footer needs "of n" before the document is drawn. render_document() therefore lays a document out
twice: first in counting mode (n_pages=None, nothing drawn) to learn how many pages it takes, then for real.
Renderers must be pure functions of the case (no random draws), so both passes print exactly the same rows.
"""

import io
from dataclasses import dataclass, field
from typing import Any

from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from uwcopilot.docintel.schemas import Cited
from uwcopilot.docintel.truth import SYNTHETIC_FOOTER

PORTRAIT: tuple[float, float] = letter
LANDSCAPE: tuple[float, float] = landscape(letter)
LEFT_MARGIN = 54.0
TOP_MARGIN = 50.0
CONTENT_BOTTOM = 56.0  # rows may not go below this baseline; the footer sits under it
FOOTER_BASELINE = 30.0
FOOTER_SIZE = 7.5
DEFAULT_SIZE = 9.0
LEADING_FACTOR = 1.45
MIN_FONT_SIZE = 6.0
# pdfminer places a glyph box from (baseline + descent) to (baseline + descent + size); descent is about -0.21 em
# for the standard fonts. The recorded bboxes use the same convention, in points with a top-left origin.
DESCENT_EM = 0.207


class LayoutError(RuntimeError):
    """A renderer tried to draw below the content area; it must plan its pages better."""


@dataclass
class Segment:
    """One piece of text on a row. align 'right' means x is the right edge (used for numbers in tables)."""

    text: str
    x: float
    size: float = DEFAULT_SIZE
    font: str = "Helvetica"
    align: str = "left"
    max_width: float | None = None


@dataclass
class Row:
    """A printed row: its 1-based line number on the page, its text and one bbox per segment."""

    page_number: int
    line: int
    text: str
    boxes: list[tuple[float, float, float, float]]


@dataclass
class PageRecord:
    """What one finished page printed (becomes a PageTruth)."""

    page_number: int
    page_in_doc: int
    text_lines: list[str]


def fitted_size(text: str, font: str, size: float, max_width: float | None) -> float:
    """The font size that makes text fit max_width (never below MIN_FONT_SIZE)."""
    if max_width is None:
        return size
    while size > MIN_FONT_SIZE and stringWidth(text, font, size) > max_width:
        size -= 0.25
    return size


class PageWriter:
    """One page of a document. Rows are added top to bottom; finish() adds the footer."""

    def __init__(self, document: "DocumentWriter", page_number: int, page_in_doc: int) -> None:
        self.document = document
        self.page_number = page_number
        self.page_in_doc = page_in_doc
        self.width, self.height = document.page_size
        self.y = self.height - TOP_MARGIN
        self.lines: list[str] = []

    @property
    def canvas(self) -> Canvas | None:
        """The reportlab canvas, or None in layout-only mode."""
        return self.document.canvas

    @property
    def right(self) -> float:
        """The right edge of the content area."""
        return self.width - LEFT_MARGIN

    def space_left(self) -> float:
        """Points between the current position and the bottom of the content area."""
        return self.y - CONTENT_BOTTOM

    def skip(self, points: float) -> None:
        """Leave vertical space without printing anything."""
        self.y -= points

    def row(self, segments: list[Segment], leading: float | None = None) -> Row:
        """Print one row of segments on a shared baseline and record it as the next line of the page."""
        if not segments:
            raise ValueError("a row needs at least one segment")
        largest = max(segment.size for segment in segments)
        self.y -= leading if leading is not None else largest * LEADING_FACTOR
        if self.y < CONTENT_BOTTOM:
            raise LayoutError(f"page {self.page_number}: row would print below the content area")
        return self.draw_row(segments, self.y)

    def draw_row(self, segments: list[Segment], baseline: float) -> Row:
        """Draw the segments at a baseline, record the row text and return it with one bbox per segment."""
        boxes = []
        texts = []
        for segment in segments:
            size = fitted_size(segment.text, segment.font, segment.size, segment.max_width)
            width = stringWidth(segment.text, segment.font, size)
            x0 = segment.x - width if segment.align == "right" else segment.x
            if self.canvas is not None:
                self.canvas.setFont(segment.font, size)
                self.canvas.drawString(x0, baseline, segment.text)
            top = self.height - baseline - (1.0 - DESCENT_EM) * size
            bottom = self.height - baseline + DESCENT_EM * size
            boxes.append((round(x0, 2), round(top, 2), round(x0 + width, 2), round(bottom, 2)))
            texts.append(segment.text)
        self.lines.append(" ".join(texts))
        return Row(page_number=self.page_number, line=len(self.lines), text=self.lines[-1], boxes=boxes)

    def text(self, text: str, x: float = LEFT_MARGIN, size: float = DEFAULT_SIZE, font: str = "Helvetica") -> Row:
        """Print a single text segment as a row."""
        return self.row([Segment(text, x, size=size, font=font)])

    def cite(self, row: Row, index: int, value: Any) -> Cited:
        """A Cited value for segment `index` of a printed row (confidence 1.0: the renderer knows what it drew)."""
        return Cited(
            value=value,
            doc_id=self.document.doc_id,
            page=row.page_number,
            line=row.line,
            bbox=row.boxes[index],
            confidence=1.0,
            source_text=row.text,
        )

    def cite_lines(self, rows: list[Row], value: Any) -> Cited:
        """A Cited value for free text printed over several rows: the first line, the union box, the joined text."""
        if not rows:
            return Cited(value=value, doc_id=self.document.doc_id, confidence=1.0)
        boxes = [box for row in rows for box in row.boxes]
        union = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        return Cited(
            value=value,
            doc_id=self.document.doc_id,
            page=rows[0].page_number,
            line=rows[0].line,
            bbox=union,
            confidence=1.0,
            source_text=" ".join(row.text for row in rows),
        )

    def field(self, label: str, text: str, value: Any, value_x: float = 230.0, size: float = DEFAULT_SIZE) -> Cited:
        """Print 'Label: value' on one row and return the value cited."""
        printed = self.row([Segment(label, LEFT_MARGIN, size=size, font="Helvetica-Bold"), Segment(text, value_x)])
        return self.cite(printed, 1, value)

    def wrapped(self, text: str, width: float, x: float = LEFT_MARGIN, size: float = DEFAULT_SIZE) -> list[Row]:
        """Print free text wrapped to `width` points, one row per wrapped line."""
        rows = []
        for line in wrap_text(text, "Helvetica", size, width):
            rows.append(self.row([Segment(line, x, size=size)]))
        return rows

    def hline(self, y_offset: float = 4.0, x0: float | None = None, x1: float | None = None) -> None:
        """A horizontal rule just under the current row (drawing only; no text)."""
        if self.canvas is not None:
            self.canvas.setLineWidth(0.5)
            self.canvas.line(x0 or LEFT_MARGIN, self.y - y_offset, x1 or self.right, self.y - y_offset)

    def box(self, x: float, y_top: float, width: float, height: float, fill_gray: float | None = None) -> None:
        """A rectangle given by its top-left corner in reportlab coordinates (drawing only)."""
        if self.canvas is None:
            return
        self.canvas.setLineWidth(0.6)
        if fill_gray is not None:
            self.canvas.setFillGray(fill_gray)
            self.canvas.rect(x, y_top - height, width, height, stroke=1, fill=1)
            self.canvas.setFillGray(0.0)
        else:
            self.canvas.rect(x, y_top - height, width, height, stroke=1, fill=0)

    def finish(self) -> PageRecord:
        """Print the footer row and close the page."""
        footer = [
            Segment(SYNTHETIC_FOOTER, LEFT_MARGIN, size=FOOTER_SIZE),
            Segment(
                f"Page {self.page_in_doc} of {self.document.pages_label}", self.right, size=FOOTER_SIZE, align="right"
            ),
        ]
        self.draw_row(footer, FOOTER_BASELINE)
        if self.canvas is not None:
            self.canvas.showPage()
        return PageRecord(self.page_number, self.page_in_doc, list(self.lines))


@dataclass
class DocumentWriter:
    """All pages of one document. n_pages is planned before drawing so every footer can say 'Page i of n'.

    n_pages None is counting mode: any number of pages may be added and close() does not check the count.
    """

    doc_id: str
    first_page: int
    n_pages: int | None
    draw: bool
    page_size: tuple[float, float] = PORTRAIT
    canvas: Canvas | None = None
    records: list[PageRecord] = field(default_factory=list)
    buffer: io.BytesIO = field(default_factory=io.BytesIO)
    _open_page: PageWriter | None = None

    @property
    def pages_label(self) -> str:
        """The "n" of "Page i of n" ("?" in counting mode, which is never drawn)."""
        return "?" if self.n_pages is None else str(self.n_pages)

    def __post_init__(self) -> None:
        """Create the canvas (invariant=1 makes the PDF bytes depend only on the content)."""
        if self.draw:
            self.canvas = Canvas(self.buffer, pagesize=self.page_size, invariant=1, pageCompression=1)
            self.canvas.setTitle("SYNTHETIC document")
            self.canvas.setAuthor("uwcopilot synthetic packet generator")
            self.canvas.setCreator("uwcopilot")
            self.canvas.setProducer("uwcopilot")

    def new_page(self) -> PageWriter:
        """Finish the open page (if any) and start the next one."""
        if self._open_page is not None:
            self.records.append(self._open_page.finish())
        page_in_doc = len(self.records) + 1
        if self.n_pages is not None and page_in_doc > self.n_pages:
            raise LayoutError(f"{self.doc_id}: more pages than the {self.n_pages} planned")
        self._open_page = PageWriter(self, self.first_page + page_in_doc - 1, page_in_doc)
        return self._open_page

    def close(self) -> bytes | None:
        """Finish the last page and return the PDF bytes (None in layout-only mode)."""
        if self._open_page is not None:
            self.records.append(self._open_page.finish())
            self._open_page = None
        if self.n_pages is not None and len(self.records) != self.n_pages:
            raise LayoutError(f"{self.doc_id}: drew {len(self.records)} pages, planned {self.n_pages}")
        if self.canvas is None:
            return None
        self.canvas.save()
        return self.buffer.getvalue()


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap: split text into lines no wider than `width` points (a long word gets its own line)."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and stringWidth(candidate, font, size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
