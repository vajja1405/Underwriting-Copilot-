"""The package's round-trip audit: re-read every number the package prints and find it in the founder ledger.

    audit = audit_package(package)          # on the package text (what render_pdf prints)
    untraced = audit_pdf(pdf_bytes, ledger)  # on the text pypdf extracts from the PDF itself

Both run provenance.renderer.round_trip_audit (the S4 / SIG audit) on the text, against an AuditLedger: the founder
ledger plus two documented kinds of ledger data that are not a claim's own value:
- the low and high end of a claim's range (Claim.value_range), printed in the claim's display format;
- numbers inside a TEXT claim's value: the founder's own words (a business name such as "Route 9 Wash", a site
  label), printed as data. They trace to that claim.
Before the audit, two documented steps:
- money_sign is undone ("-$53,735" back to the renderer's "$-53,735"), so the audit reads exactly the renderer's
  format;
- LAYOUT_PATTERNS are blanked: structural labels that are not quantities: "Page 3/14" in the footer, the percentile
  column labels P10 / P50 / P90, period labels "Year 2" / "year-2" / "Month 3", ordinal labels "rank 1" and
  "Change-set 2" / "change 1", and the phrase "below zero" (a sign, not a figure). A label number is never followed
  by a decimal part and never reaches across a line break (except "below zero" and "year-" at a hyphen), so a value
  printed on the next line ("per year" then "9.25") is never blanked. The number extractor itself
  already skips section ids, dates, list markers and anything in square brackets (citations, claim ids, hashes).
Every other number must equal a claim value as the renderer prints it. The list returned is what does not trace.
"""

import io
import re
from collections.abc import Iterable

from pypdf import PdfReader

from uwcopilot.evidence.ledger_models import Assertion, Claim, Ledger
from uwcopilot.provenance.numbers import extract_numbers
from uwcopilot.provenance.renderer import RenderedMemo, SimpleClaim, round_trip_audit
from uwcopilot.studio.package.blocks import claim as claim_cell
from uwcopilot.studio.package.cover import contents_note, contents_rows, cover_lines
from uwcopilot.studio.package.models import Block, Package, PackageAudit, Section
from uwcopilot.studio.package.runs import RunIndex
from uwcopilot.studio.package.text import cell_text, render_draft

LAYOUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bPage[ \t]+\d+/\d+\b"),
    re.compile(r"\bP(?:10|50|90)\b(?![.,]\d)"),
    re.compile(r"\b[Yy]ear(?:-\s*|[ \t]+)\d{1,2}\b(?![.,]\d)"),
    re.compile(r"\b[Mm]onth[ \t]+\d{1,2}\b(?![.,]\d)"),
    re.compile(r"\b[Rr]ank[ \t]+\d{1,2}\b(?![.,]\d)"),
    re.compile(r"\b[Cc]hange(?:-set)?[ \t]+\d{1,2}\b(?![.,]\d)"),
    re.compile(r"\bbelow\s+zero\b"),
)
TEXT_DISPLAYS: dict[str, str] = {
    "USD": "usd2",
    "PERCENT": "pct1",
    "MONTHS": "months",
    "YEARS": "years",
    "RATIO": "ratio2",
    "COUNT": "int",
    "NONE": "ratio2",
}


def mask_layout(text: str) -> str:
    """The text with every LAYOUT_PATTERNS match replaced by spaces of the same length."""
    masked = text
    for pattern in LAYOUT_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def renderer_money(text: str) -> str:
    """'-$53,735' back to the renderer's '$-53,735' (text.money_sign undone)."""
    return text.replace("-$", "$-")


def prepare(text: str) -> str:
    """The text as the audit reads it (module docstring)."""
    return mask_layout(renderer_money(text))


class AuditLedger:
    """The founder ledger plus range bounds and numbers in text claims (a LedgerLike for round_trip_audit)."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.extra = list(self.range_bounds()) + list(self.text_numbers())

    def range_bounds(self) -> Iterable[SimpleClaim]:
        """Both ends of every claim's range, in the claim's display format."""
        for item in self.ledger.claims:
            if item.value_range is None or item.display == "text":
                continue
            for end, value in (("low", item.value_range.low), ("high", item.value_range.high)):
                yield SimpleClaim(id=f"{item.id}#{end}", label=item.label, value=value, display=item.display)

    def text_numbers(self) -> Iterable[SimpleClaim]:
        """Every number inside a text claim's value (the founder's own words, printed as data)."""
        for item in self.ledger.claims:
            if item.display != "text" or not isinstance(item.value, str):
                continue
            for index, mention in enumerate(extract_numbers(item.value)):
                display = TEXT_DISPLAYS.get(mention.unit)
                if display is not None:
                    yield SimpleClaim(
                        id=f"{item.id}#n{index}", label=item.label, value=float(mention.value), display=display
                    )

    def claim(self, claim_id: str) -> Claim | None:
        """A ledger claim by id."""
        return self.ledger.claim(claim_id)

    def assertion(self, assertion_id: str) -> Assertion | None:
        """A ledger assertion by id."""
        return self.ledger.assertion(assertion_id)

    def claim_values(self) -> list[Claim | SimpleClaim]:
        """Every claim, then the extra values."""
        return [*self.ledger.claims, *self.extra]


def block_lines(block: Block, ledger: Ledger, index: dict[str, int], runs: RunIndex | None = None) -> list[str]:
    """The printed text of one block, line by line."""
    if block.kind == "paragraph":
        return [render_draft(block.draft, ledger, index)[0]]
    if block.kind == "bullets":
        return [render_draft(item, ledger, index)[0] for item in block.items]
    if block.kind == "figure":
        return [render_draft(block.caption, ledger, index)[0]]
    if block.kind == "key_facts":
        lines = [block.title]
        for fact in block.items:
            item = ledger.claim(fact.ref)
            value = cell_text(claim_cell(fact.ref), ledger, index) if item is not None else ""
            lines.append(f"{fact.label}: {value}")
        return lines
    lines = [block.title, " | ".join(block.header)]
    for row in block.rows:
        lines.append(" | ".join(cell_text(cell, ledger, index, runs) for cell in row))
    if block.note:
        lines.append(render_draft(block.note, ledger, index)[0])
    return lines


def section_lines(section: Section, ledger: Ledger, index: dict[str, int], runs: RunIndex | None = None) -> list[str]:
    """The heading and every block of one section."""
    lines = [f"{section.number}. {section.title}"]
    for block in section.blocks:
        lines.extend(block_lines(block, ledger, index, runs))
    return lines


def package_text(package: Package) -> str:
    """Everything render_pdf prints except the page footers (cover, contents, sections), one line per item."""
    lines = list(cover_lines(package).values())
    lines += [f"{number} {title} {sba}" for number, title, sba in contents_rows(package)]
    lines.append(contents_note())
    for section in package.sections:
        lines.extend(section_lines(section, package.ledger, package.appendix_index, package.runs))
    return "\n".join(lines)


def untraced_numbers(text: str, ledger: Ledger) -> list[str]:
    """The numbers of a printed text that trace to no ledger value (module docstring)."""
    return round_trip_audit(RenderedMemo(body=prepare(text)), AuditLedger(ledger))


def count_numbers(text: str) -> int:
    """How many numbers the audit reads in a printed text."""
    return len(extract_numbers(prepare(text)))


def value_cell_count(sections: list[Section]) -> int:
    """How many value cells and key facts the sections print."""
    total = 0
    for section in sections:
        for block in section.blocks:
            if block.kind == "table":
                total += sum(1 for row in block.rows for cell in row if cell.kind in ("claim", "range"))
            elif block.kind == "key_facts":
                total += len(block.items)
    return total


def audit_package(package: Package) -> PackageAudit:
    """The audit of the package text (module docstring)."""
    text = package_text(package)
    return PackageAudit(
        numbers=count_numbers(text),
        untraced=untraced_numbers(text, package.ledger),
        value_cells=value_cell_count(package.sections),
        sections=len(package.sections),
    )


def pdf_text(pdf: bytes) -> str:
    """The text pypdf extracts from every page, pages separated by newlines."""
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def audit_pdf(pdf: bytes, ledger: Ledger) -> list[str]:
    """The numbers in the PDF's own text that trace to no ledger value (empty for a sound package)."""
    return untraced_numbers(pdf_text(pdf), ledger)


def pdf_page_count(pdf: bytes) -> int:
    """The number of pages of a PDF."""
    return len(PdfReader(io.BytesIO(pdf)).pages)


def page_texts(pdf: bytes) -> list[str]:
    """The extracted text of every page."""
    reader = PdfReader(io.BytesIO(pdf))
    return [page.extract_text() or "" for page in reader.pages]


def pages_missing_labels(pdf: bytes, package: Package) -> dict[str, list[int]]:
    """Pages (1-based) missing a label every page must carry: the product name, the disclaimer, "Page x/y" and,
    for a SYNTHETIC package, the SYNTHETIC tag. Empty lists when every page has them."""
    texts = page_texts(pdf)
    total = len(texts)
    flat = [" ".join(text.split()) for text in texts]
    missing: dict[str, list[int]] = {"product": [], "disclaimer": [], "page_number": [], "synthetic": []}
    for number, text in enumerate(flat, start=1):
        if package.product not in text:
            missing["product"].append(number)
        if " ".join(package.disclaimer.split()) not in text:
            missing["disclaimer"].append(number)
        if f"Page {number}/{total}" not in text:
            missing["page_number"].append(number)
        if package.data_label == "SYNTHETIC" and "SYNTHETIC" not in text:
            missing["synthetic"].append(number)
    return missing
