"""Page text models shared by the text-layer reader (pdftext.py), the OCR reader (ocr.py) and the extractors.

A page is a list of lines in reading order (top to bottom); a line is a list of words (left to right). Every word
and line keeps its bounding box in PDF points with the origin at the TOP-LEFT of the page, the same convention as
pdfplumber and as the Cited.bbox of docintel/schemas.py: (x0, top, x1, bottom).

Confidence is between 0 and 1. Words from the PDF text layer have confidence 1.0 (the text is exactly what was
printed). OCR words carry Tesseract's word confidence divided by 100; a line's confidence is the mean of its
words' confidences.

Line numbers are 1-based and count the lines of one page in reading order. For a text-layer page they match the
generator's printed rows; for an OCR page they follow Tesseract's line order.

group_words_into_lines() turns loose words (from the text layer) into lines: words whose vertical centres are within
LINE_TOLERANCE points of a line's centre join that line. OCR pages use Tesseract's own line grouping instead (see
ocr.py), because a skewed scan tilts a row by more than the gap between rows.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Box = tuple[float, float, float, float]
PageSource = Literal["text_layer", "ocr"]

# Words whose vertical centres differ by at most this many points are on the same printed row. The packets use
# 6-12 point fonts with at least 8.7 points between rows, so 3 points keeps rows apart.
LINE_TOLERANCE = 3.0


class Word(BaseModel):
    """One word: its text, bounding box (PDF points, top-left origin) and confidence (0-1)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    conf: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def bbox(self) -> Box:
        """(x0, top, x1, bottom)."""
        return (self.x0, self.top, self.x1, self.bottom)

    @property
    def center_y(self) -> float:
        """Vertical centre of the word box."""
        return (self.top + self.bottom) / 2.0


class Line(BaseModel):
    """One printed line: its 1-based number on the page, its text, box, words and confidence."""

    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    text: str
    bbox: Box
    words: list[Word]
    confidence: float = Field(ge=0.0, le=1.0)


class PageText(BaseModel):
    """The text of one packet page, from the text layer or from OCR."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    width: float
    height: float
    source: PageSource
    lines: list[Line]
    char_count: int = 0
    ocr_psm: int | None = None  # the Tesseract page segmentation mode kept for an OCR page
    ocr_mean_confidence: float | None = None
    deskew_degrees: float | None = None  # the rotation applied before OCR

    @property
    def text(self) -> str:
        """All lines joined with newlines."""
        return "\n".join(line.text for line in self.lines)


def union_box(boxes: list[Box]) -> Box:
    """The smallest box that holds every box in the list (rounded to 0.01 point)."""
    return (
        round(min(box[0] for box in boxes), 2),
        round(min(box[1] for box in boxes), 2),
        round(max(box[2] for box in boxes), 2),
        round(max(box[3] for box in boxes), 2),
    )


def make_line(line_number: int, words: list[Word]) -> Line:
    """A Line from its words (sorted left to right); text joins the words with single spaces."""
    ordered = sorted(words, key=lambda word: word.x0)
    confidence = sum(word.conf for word in ordered) / len(ordered)
    return Line(
        line_number=line_number,
        text=" ".join(word.text for word in ordered),
        bbox=union_box([word.bbox for word in ordered]),
        words=ordered,
        confidence=round(confidence, 4),
    )


def group_words_into_lines(words: list[Word], tolerance: float = LINE_TOLERANCE) -> list[Line]:
    """Group words into lines by vertical centre (see the module docstring), numbered top to bottom."""
    groups: list[list[Word]] = []
    centers: list[float] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x0)):
        if groups and abs(word.center_y - centers[-1]) <= tolerance:
            groups[-1].append(word)
            centers[-1] = sum(item.center_y for item in groups[-1]) / len(groups[-1])
        else:
            groups.append([word])
            centers.append(word.center_y)
    return [make_line(number, group) for number, group in enumerate(groups, start=1)]


def renumber(lines: list[Line]) -> list[Line]:
    """The same lines numbered 1..n in their current order."""
    return [line.model_copy(update={"line_number": number}) for number, line in enumerate(lines, start=1)]
