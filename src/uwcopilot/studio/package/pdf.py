"""render_pdf: the founder package as a PDF, with reportlab (no browser, no network).

    pdf = render_pdf(package)      # bytes starting with %PDF

What every page carries (Studio addendum rule C and the S6 plan): the product name, the disclaimer "Educational
planning tool. Not financial, legal or lending advice. Lenders make their own decisions." and "Page x/y" in the
footer; a SYNTHETIC tag in the header when the package is SYNTHETIC (and a banner on the cover). The page total is
known only at the end, so the canvas keeps every page and draws headers and footers when the document is saved.

The text comes from text.py (the same words the audit reads): values from the ledger, paragraphs from the renderer,
"[k]" references as small links to row k of the evidence appendix, and every value cell links to its claim's row.
The output is deterministic (reportlab's invariant mode: no creation time in the file), so the same package gives
the same bytes. A package whose audit found an untraced number is refused (PackageNotAudited).
"""

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from uwcopilot.studio.package import pdf_figures
from uwcopilot.studio.package import pdf_styles as ui
from uwcopilot.studio.package.constants import STATUS_MEANINGS, STATUS_WORDS
from uwcopilot.studio.package.cover import contents_note, contents_rows, cover_lines, is_synthetic
from uwcopilot.studio.package.models import Block, Cell, Figure, KeyFacts, Package, Section
from uwcopilot.studio.package.models import Paragraph as ParagraphBlock
from uwcopilot.studio.package.models import Table as TableBlock
from uwcopilot.studio.package.text import cell_text, render_draft, result_word

PAGE_WIDTH, PAGE_HEIGHT = LETTER
MARGIN_X = 0.7 * inch
MARGIN_TOP = 0.85 * inch
MARGIN_BOTTOM = 0.8 * inch
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN_X
REFERENCE = re.compile(r"\[(\d+)\]")
STATUS_COLORS = {
    "VERIFIED": ui.PASS,
    "USER_STATED": ui.MUTED,
    "ASSUMPTION": ui.FINDING,
    "MODEL_ESTIMATE": ui.NAVY,
    "CONFLICTING": ui.WARN,
    "UNKNOWN": ui.WARN,
}
SECTION_BREAK_BEFORE = frozenset({"executive_summary", "appendix"})


class PackageNotAudited(ValueError):
    """The package text holds a number that traces to no ledger claim, so it is not printed."""


# --------------------------------------------------------------------------------------------------------------
# Text markup
# --------------------------------------------------------------------------------------------------------------


def linked_references(markup: str) -> str:
    """'[17]' -> a small superscript link to appendix row 17 (the text stays "[17]")."""
    return REFERENCE.sub(
        lambda match: (
            f'<super><font size="5.6" color="#2A7F8E"><a href="#c{match.group(1)}">[{match.group(1)}]</a>'
            "</font></super>"
        ),
        markup,
    )


def draft_markup(draft: str, package: Package) -> str:
    """A slot draft rendered from the ledger, escaped, with appendix links."""
    text, _ = render_draft(draft, package.ledger, package.appendix_index)
    return linked_references(escape(text))


def cell_markup(cell: Cell, package: Package) -> str:
    """The markup of one table cell (the words come from text.cell_text)."""
    ledger, index = package.ledger, package.appendix_index
    words = escape(cell_text(cell, ledger, index, package.runs))
    if cell.kind == "draft":
        words = linked_references(words)
    elif cell.kind == "claim" and cell.ref in index:
        words = f'<a href="#c{index[cell.ref]}" color="#1E2329">{words}</a>'
    elif cell.kind == "status":
        item = ledger.claim(cell.ref)
        if item is not None:
            words = f'<font color="{STATUS_COLORS[item.status].hexval()}">{words}</font>'
    elif cell.kind == "assert":
        assertion = ledger.assertion(cell.ref)
        if assertion is not None:
            color = ui.RESULT_COLORS[result_word(assertion)].hexval()
            words = f'<font color="{color}">{words}</font>'
    elif cell.kind == "ref" and cell.ref in index:
        words = f'<a name="c{index[cell.ref]}"/><font name="{ui.FONT_MONO}">{words}</font>'
    elif cell.kind == "source":
        words = f'<font color="{ui.MUTED.hexval()}">{words}</font>'
    return f"<b>{words}</b>" if cell.bold else words


# --------------------------------------------------------------------------------------------------------------
# Blocks -> flowables
# --------------------------------------------------------------------------------------------------------------


def paragraph_flowable(block: ParagraphBlock, package: Package) -> Flowable:
    """One paragraph block."""
    return Paragraph(draft_markup(block.draft, package), ui.styles()[block.style])


def cell_style_name(block: TableBlock, column: int) -> str:
    """The paragraph style of a column."""
    align = block.align[column]
    suffix = "" if align == "left" else f"_{align}"
    return f"cell_{block.size}{suffix}"


def head_style_name(block: TableBlock, column: int) -> str:
    """The header style of a column (aligned like the column)."""
    align = block.align[column]
    suffix = "" if align == "left" else f"_{align}"
    return f"head_{block.size}{suffix}"


def table_flowables(block: TableBlock, package: Package) -> list[Flowable]:
    """Title, table and note of one table block."""
    styles = ui.styles()
    header = [
        Paragraph(escape(label), styles[head_style_name(block, column)]) for column, label in enumerate(block.header)
    ]
    rows = [header]
    for row in block.rows:
        rows.append(
            [
                Paragraph(cell_markup(cell, package), styles[cell_style_name(block, column)])
                for column, cell in enumerate(row)
            ]
        )
    total = sum(block.widths)
    widths = [CONTENT_WIDTH * width / total for width in block.widths]
    table = LongTable(rows, colWidths=widths, repeatRows=1, splitByRow=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ui.NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, ui.RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for position in range(1, len(rows)):
        if (position - 1) in block.group_rows:
            commands.append(("BACKGROUND", (0, position), (-1, position), ui.GROUP))
            commands.append(("SPAN", (0, position), (-1, position)))
        elif position % 2 == 0:
            commands.append(("BACKGROUND", (0, position), (-1, position), ui.STRIPE))
    table.setStyle(TableStyle(commands))
    parts: list[Flowable] = []
    if block.title:
        parts.append(Paragraph(escape(block.title), styles["table_title"]))
    parts.append(table)
    if block.note:
        parts.append(Paragraph(draft_markup(block.note, package), styles["table_note"]))
    else:
        parts.append(Spacer(1, 6))
    if len(block.rows) <= 14:
        return [KeepTogether(parts)]
    return parts


def key_facts_flowable(block: KeyFacts, package: Package) -> Flowable:
    """A grid of labelled figures, three per row."""
    styles = ui.styles()
    per_row = 3
    cells = []
    for fact in block.items:
        value = cell_markup(Cell(kind="claim", ref=fact.ref), package)
        cells.append([Paragraph(escape(fact.label), styles["fact_label"]), Paragraph(value, styles["fact_value"])])
    rows = [cells[start : start + per_row] for start in range(0, len(cells), per_row)]
    while rows and len(rows[-1]) < per_row:
        rows[-1].append("")
    width = CONTENT_WIDTH / per_row
    table = Table(rows, colWidths=[width] * per_row)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ui.PANEL),
                ("LINEBELOW", (0, 0), (-1, -2), 0.6, ui.WHITE),
                ("LINEAFTER", (0, 0), (-2, -1), 0.6, ui.WHITE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    title = Paragraph(escape(block.title), styles["table_title"]) if block.title else Spacer(1, 2)
    return KeepTogether([title, table, Spacer(1, 8)])


def image_flowable(png: bytes, height_in: float) -> Image:
    """A PNG scaled to the content width (or to height_in, whichever is smaller)."""
    image = Image(io.BytesIO(png))
    ratio = image.imageHeight / image.imageWidth
    width = min(CONTENT_WIDTH, height_in * inch / ratio)
    return Image(io.BytesIO(png), width=width, height=width * ratio)


def figure_flowables(block: Figure, package: Package) -> list[Flowable]:
    """A figure drawn from the package's data, with its caption."""
    if block.figure == "map":
        if package.map is None:
            return []
        png = pdf_figures.draw_map(package.map)
    elif block.figure == "cash_band":
        png = pdf_figures.draw_cash_band(package.ledger, block.labels)
    else:
        png = pdf_figures.draw_tornado(package.ledger, block.refs)
    caption = Paragraph(draft_markup(block.caption, package), ui.styles()["caption"])
    return [KeepTogether([image_flowable(png, block.height_in), caption])]


def block_flowables(block: Block, package: Package) -> list[Flowable]:
    """The flowables of one block."""
    if block.kind == "paragraph":
        return [paragraph_flowable(block, package)]
    if block.kind == "table":
        return table_flowables(block, package)
    if block.kind == "key_facts":
        return [key_facts_flowable(block, package)]
    if block.kind == "figure":
        return figure_flowables(block, package)
    styles = ui.styles()
    return [Paragraph("&bull; " + draft_markup(item, package), styles["body"]) for item in block.items]


class SectionHeading(Paragraph):
    """A section heading that adds a PDF bookmark (outline entry) where it is drawn."""

    def __init__(self, section: Section) -> None:
        self.outline_title = f"{section.number}. {section.title}"
        self.outline_key = f"section-{section.key}"
        markup = f'<a name="{self.outline_key}"/>{section.number}. {escape(section.title)}'
        super().__init__(markup, ui.styles()["section"])


def section_flowables(section: Section, package: Package) -> list[Flowable]:
    """A section: page break or conditional break, heading, SBA line, blocks."""
    styles = ui.styles()
    parts: list[Flowable] = [PageBreak()] if section.key in SECTION_BREAK_BEFORE else [CondPageBreak(2.6 * inch)]
    parts.append(SectionHeading(section))
    covers = ", ".join(section.sba_sections) if section.sba_sections else "none (a package addition)"
    parts.append(Paragraph(f"SBA business plan section covered: {escape(covers)}", styles["section_sba"]))
    for block in section.blocks:
        parts.extend(block_flowables(block, package))
    return parts


# --------------------------------------------------------------------------------------------------------------
# Cover
# --------------------------------------------------------------------------------------------------------------


def boxed(markup: str, style_name: str, border: object, background: object) -> Table:
    """One paragraph in a colored box."""
    table = Table([[Paragraph(markup, ui.styles()[style_name])]], colWidths=[CONTENT_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.0, border),
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def contents_table(package: Package) -> list[Flowable]:
    """The contents with the SBA sections each package section covers."""
    styles = ui.styles()
    rows = [[Paragraph(label, styles["head_small"]) for label in ("", "Section", "SBA business plan section covered")]]
    for number, title, sba in contents_rows(package):
        rows.append(
            [
                Paragraph(number, styles["cell_small"]),
                Paragraph(
                    f'<a href="#section-{key_for(package, title)}" color="#1F3A5F">{escape(title)}</a>',
                    styles["cell_small"],
                ),
                Paragraph(escape(sba), styles["cell_small"]),
            ]
        )
    table = Table(rows, colWidths=[0.35 * inch, 3.4 * inch, CONTENT_WIDTH - 3.75 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ui.NAVY),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, ui.RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    return [
        Paragraph("Contents", styles["table_title"]),
        table,
        Paragraph(escape(contents_note()), styles["table_note"]),
    ]


def key_for(package: Package, title: str) -> str:
    """The section key of a section title."""
    return next((section.key for section in package.sections if section.title == title), "")


def legend_flowable() -> Table:
    """The six statuses, how they are colored in tables, and what they mean."""
    styles = ui.styles()
    rows = []
    for key, meaning in STATUS_MEANINGS.items():
        color = STATUS_COLORS[key].hexval()
        rows.append(
            [
                Paragraph(f'<font color="{color}"><b>{STATUS_WORDS[key]}</b></font>', styles["cell_small"]),
                Paragraph(escape(meaning), styles["cell_small"]),
            ]
        )
    table = Table(rows, colWidths=[1.3 * inch, CONTENT_WIDTH - 1.3 * inch])
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, ui.RULE), ("TOPPADDING", (0, 0), (-1, -1), 1.5)]))
    return table


def cover_flowables(package: Package) -> list[Flowable]:
    """The cover page (cover.py words) and the contents."""
    styles = ui.styles()
    lines = cover_lines(package)
    parts: list[Flowable] = [
        Spacer(1, 0.15 * inch),
        Paragraph(escape(lines["product"]), styles["cover_product"]),
        Paragraph(escape(lines["title"]), styles["cover_title"]),
        Paragraph(escape(lines["business"]), styles["cover_business"]),
        Paragraph(escape(lines["subtitle"]), styles["cover_subtitle"]),
        Paragraph(f"{escape(lines['prepared'])}  &middot;  {escape(lines['mode'])}", styles["cover_meta"]),
        Paragraph(escape(lines["package_id"]), styles["cover_meta"]),
        Spacer(1, 0.18 * inch),
    ]
    if "banner" in lines:
        parts += [boxed(escape(lines["banner"]), "banner", ui.WARN, ui.WARN_BG), Spacer(1, 0.12 * inch)]
    parts += [
        boxed(escape(lines["disclaimer"]), "disclaimer", ui.NAVY, ui.PANEL),
        Spacer(1, 0.08 * inch),
        Paragraph(escape(lines["simulation"]) + " " + escape(lines["no_decision"]), styles["note"]),
        *contents_table(package),
        Paragraph("How to read the statuses", styles["table_title"]),
        legend_flowable(),
    ]
    return parts


# --------------------------------------------------------------------------------------------------------------
# Pages: header, footer, page x/y
# --------------------------------------------------------------------------------------------------------------


def canvas_class(package: Package) -> type[pdf_canvas.Canvas]:
    """A canvas that keeps every page and draws headers and footers (with the page total) when saved."""
    synthetic = is_synthetic(package)
    business = cover_lines(package)["business"]

    class PackageCanvas(pdf_canvas.Canvas):
        """Deferred headers and footers (page x/y needs the total)."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.saved_pages: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 - reportlab's method name
            self.saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self.saved_pages)
            for state in self.saved_pages:
                self.__dict__.update(state)
                if self._pageNumber > 1:
                    self.draw_header()
                self.draw_footer(total)
                super().showPage()
            super().save()

        def draw_header(self) -> None:
            y = PAGE_HEIGHT - 0.5 * inch
            self.setFont(ui.FONT_BOLD, 7.5)
            self.setFillColor(ui.NAVY)
            self.drawString(MARGIN_X, y, package.product)
            offset = self.stringWidth(package.product, ui.FONT_BOLD, 7.5) + 8
            self.setFont(ui.FONT, 7.5)
            self.setFillColor(ui.MUTED)
            self.drawString(MARGIN_X + offset, y, package.title)
            right = PAGE_WIDTH - MARGIN_X
            shown = business if len(business) <= 60 else business[:57] + "..."
            self.drawRightString(right, y, shown)
            if synthetic:
                tag_width = 0.72 * inch
                x = right - self.stringWidth(shown, ui.FONT, 7.5) - tag_width - 6
                self.setFillColor(ui.WARN_BG)
                self.setStrokeColor(ui.WARN)
                self.roundRect(x, y - 2.5, tag_width, 10.5, 2.5, stroke=1, fill=1)
                self.setFillColor(ui.WARN)
                self.setFont(ui.FONT_BOLD, 6.6)
                self.drawCentredString(x + tag_width / 2, y + 0.4, "SYNTHETIC")
            self.setStrokeColor(ui.RULE)
            self.setLineWidth(0.6)
            self.line(MARGIN_X, y - 6, PAGE_WIDTH - MARGIN_X, y - 6)

        def draw_footer(self, total: int) -> None:
            y = 0.45 * inch
            self.setStrokeColor(ui.RULE)
            self.setLineWidth(0.6)
            self.line(MARGIN_X, y + 11, PAGE_WIDTH - MARGIN_X, y + 11)
            self.setFont(ui.FONT_BOLD, 6.8)
            self.setFillColor(ui.NAVY)
            self.drawString(MARGIN_X, y, package.product)
            self.setFont(ui.FONT, 6.4)
            self.setFillColor(ui.MUTED)
            self.drawCentredString(PAGE_WIDTH / 2, y, package.disclaimer)
            self.setFont(ui.FONT_BOLD, 6.8)
            self.setFillColor(ui.NAVY)
            self.drawRightString(PAGE_WIDTH - MARGIN_X, y, f"Page {self._pageNumber}/{total}")

    return PackageCanvas


class PackageDocument(BaseDocTemplate):
    """The document template: one frame per page; section headings become PDF bookmarks."""

    def afterFlowable(self, flowable: Flowable) -> None:  # noqa: N802 - reportlab's hook name
        """Add a bookmark for every section heading."""
        if isinstance(flowable, SectionHeading):
            self.canv.bookmarkPage(flowable.outline_key)
            self.canv.addOutlineEntry(flowable.outline_title, flowable.outline_key, level=0)


def story(package: Package) -> list[Flowable]:
    """Every flowable of the document, in order."""
    parts = cover_flowables(package)
    for section in package.sections:
        parts.extend(section_flowables(section, package))
    return parts


def render_pdf(package: Package) -> bytes:
    """The package as PDF bytes (module docstring). Raises PackageNotAudited when the audit found untraced numbers."""
    if package.audit.untraced:
        raise PackageNotAudited(f"untraced numbers in the package text: {package.audit.untraced[:10]}")
    ui.register_fonts()
    buffer = io.BytesIO()
    document = PackageDocument(
        buffer,
        pagesize=LETTER,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=f"{package.product}: {package.title}",
        author=f"{package.product} (data label {package.data_label})",
        subject=package.disclaimer,
        creator=f"{package.product} {package.builder_version}",
        invariant=1,
    )
    frame = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_WIDTH, PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM, id="body")
    document.addPageTemplates([PageTemplate(id="page", frames=[frame])])
    document.build(story(package), canvasmaker=canvas_class(package))
    return buffer.getvalue()
