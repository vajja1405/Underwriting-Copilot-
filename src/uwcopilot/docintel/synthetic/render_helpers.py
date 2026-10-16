"""Small drawing helpers shared by the credit and insurance renderers (titles, letterheads, tables, labels).

Codes are printed as words with label(): "line_of_credit" -> "Line of credit", "llc" -> "LLC". Every label maps
back to its code by casefolding and replacing spaces with "_" (the rule the extraction pipeline uses).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from uwcopilot.docintel.schemas import Cited
from uwcopilot.docintel.synthetic.common import code_label, format_date
from uwcopilot.docintel.synthetic.render_base import LEFT_MARGIN, PageWriter, Row, Segment

LABEL_OVERRIDES: dict[str, str] = {"llc": "LLC", "owner_occupied_cre": "Owner occupied CRE"}
TITLE_SIZE = 12.0
SECTION_SIZE = 10.0


def label(code: str) -> str:
    """A code printed as words (see the module docstring)."""
    return LABEL_OVERRIDES.get(code, code_label(code))


def heading(page: PageWriter, text: str, size: float = TITLE_SIZE) -> Row:
    """A bold title row."""
    return page.row([Segment(text, LEFT_MARGIN, size=size, font="Helvetica-Bold")])


def section(page: PageWriter, text: str) -> Row:
    """A bold section heading with a little space above it."""
    page.skip(4)
    return page.row([Segment(text, LEFT_MARGIN, size=SECTION_SIZE, font="Helvetica-Bold")])


def note(page: PageWriter, text: str, size: float = 8.0) -> Row:
    """A plain small text row."""
    return page.row([Segment(text, LEFT_MARGIN, size=size)])


def field_date(page: PageWriter, label_text: str, value: date, style: str = "iso", value_x: float = 230.0) -> Cited:
    """'Label: <date>' with the date in a document style, cited as a date."""
    return page.field(label_text, format_date(value, style), value, value_x=value_x)


def table_header(page: PageWriter, columns: list[tuple[str, float, str]]) -> Row:
    """A bold header row: (text, x, align) per column, with a rule under it."""
    segments = [Segment(text, x, font="Helvetica-Bold", align=align) for text, x, align in columns]
    printed = page.row(segments)
    page.hline(3.0)
    return printed


@dataclass
class Cell:
    """One table cell: a name used to cite it, the printed text and its position."""

    name: str
    text: str
    x: float
    align: str = "left"
    max_width: float | None = None


def table_row(page: PageWriter, row_cells: list[Cell], size: float = 8.5) -> tuple[Row, dict[str, int]]:
    """Print the non-empty cells as one row; returns the row and each cell name's segment index (for cite)."""
    printed = [cell for cell in row_cells if cell.text != ""]
    segments = [Segment(cell.text, cell.x, size=size, align=cell.align, max_width=cell.max_width) for cell in printed]
    return page.row(segments), {cell.name: index for index, cell in enumerate(printed)}


def cite_named(page: PageWriter, row: Row, positions: dict[str, int], name: str, value: Any) -> Cited:
    """A Cited value for the named cell of a printed table row."""
    return page.cite(row, positions[name], value)
