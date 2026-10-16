"""Render a list of planned documents into one SYNTHETIC packet: doc ids, page numbers, page truth, merged PDF.

Each document is described by a DocumentSpec (its type, title, checklist item and a render function). Documents
get ids D01, D02, ... in page order and their pages are numbered 1..n across the packet. Every document is laid
out twice: a counting pass to learn its page count (for the "Page i of n" footers) and the real pass. A document
marked scanned is replaced by its image-only scanned version (scan.py). The merged PDF has fixed metadata and no
time-dependent fields, so the same seed gives the same bytes.
"""

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader, PdfWriter

from uwcopilot.docintel.synthetic.common import sub_seed
from uwcopilot.docintel.synthetic.render_base import PORTRAIT, DocumentWriter, PageRecord
from uwcopilot.docintel.synthetic.scan import scan_document
from uwcopilot.docintel.truth import DocumentTruth, PageTruth


@dataclass
class DocumentSpec:
    """One document to render. key names it for problem records ("bank_statement:2026-03", "loss_run:<carrier>")."""

    key: str
    doc_type: str
    title: str
    render: Callable[[DocumentWriter], Any]
    required_document: str | None = None
    month: str | None = None
    carrier: str | None = None
    page_size: tuple[float, float] = PORTRAIT
    scanned: bool = False


@dataclass
class RenderedDocument:
    """A rendered document: its id, page records, extraction part and PDF bytes (None in layout-only mode)."""

    spec: DocumentSpec
    doc_id: str
    records: list[PageRecord]
    part: Any
    pdf: bytes | None = None
    page_numbers: list[int] = field(default_factory=list)


def count_pages(spec: DocumentSpec) -> int:
    """Lay the document out without drawing and count its pages."""
    writer = DocumentWriter(doc_id="D00", first_page=1, n_pages=None, draw=False, page_size=spec.page_size)
    spec.render(writer)
    writer.close()
    return len(writer.records)


def render_one(spec: DocumentSpec, doc_id: str, first_page: int, draw: bool, packet_seed: int) -> RenderedDocument:
    """Render one document (scanned when its spec says so and drawing is on)."""
    pages = count_pages(spec)
    writer = DocumentWriter(doc_id=doc_id, first_page=first_page, n_pages=pages, draw=draw, page_size=spec.page_size)
    part = spec.render(writer)
    pdf = writer.close()
    if pdf is not None and spec.scanned:
        pdf = scan_document(pdf, sub_seed(packet_seed, f"scan|{doc_id}"))
    numbers = [record.page_number for record in writer.records]
    return RenderedDocument(spec=spec, doc_id=doc_id, records=writer.records, part=part, pdf=pdf, page_numbers=numbers)


def render_documents(specs: list[DocumentSpec], draw: bool, packet_seed: int) -> list[RenderedDocument]:
    """Render every document in order; doc ids and page numbers follow the order."""
    rendered = []
    next_page = 1
    for index, spec in enumerate(specs, start=1):
        document = render_one(spec, f"D{index:02d}", next_page, draw, packet_seed)
        rendered.append(document)
        next_page += len(document.records)
    return rendered


def doc_id_map(rendered: list[RenderedDocument]) -> dict[str, str]:
    """Document key -> doc id."""
    return {document.spec.key: document.doc_id for document in rendered}


def page_truths(rendered: list[RenderedDocument]) -> list[PageTruth]:
    """PageTruth for every page, in packet order."""
    pages = []
    for document in rendered:
        for record in document.records:
            pages.append(
                PageTruth(
                    page_number=record.page_number,
                    doc_id=document.doc_id,
                    doc_type=document.spec.doc_type,
                    page_in_doc=record.page_in_doc,
                    scanned=document.spec.scanned,
                    text_lines=record.text_lines,
                )
            )
    return pages


def document_truths(rendered: list[RenderedDocument]) -> list[DocumentTruth]:
    """DocumentTruth for every document, in packet order."""
    return [
        DocumentTruth(
            doc_id=document.doc_id,
            doc_type=document.spec.doc_type,
            title=document.spec.title,
            pages=document.page_numbers,
            scanned=document.spec.scanned,
            required_document=document.spec.required_document,
            month=document.spec.month,
            carrier=document.spec.carrier,
        )
        for document in rendered
    ]


def merge_pdf(rendered: list[RenderedDocument]) -> bytes:
    """One PDF with every document's pages in order, with fixed metadata."""
    writer = PdfWriter()
    for document in rendered:
        if document.pdf is None:
            raise ValueError("cannot merge a document rendered without drawing")
        for page in PdfReader(io.BytesIO(document.pdf)).pages:
            writer.add_page(page)
    writer.add_metadata(
        {
            "/Title": "SYNTHETIC document packet - for demonstration only",
            "/Author": "uwcopilot synthetic packet generator",
            "/Creator": "uwcopilot",
            "/Producer": "uwcopilot",
        }
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def sha256_hex(data: bytes) -> str:
    """SHA-256 of bytes as hex."""
    return hashlib.sha256(data).hexdigest()


def injection_page(rendered: list[RenderedDocument], doc_id: str, text: str) -> int:
    """The packet page of a document whose printed text contains the start of `text` (wrapped lines rejoined)."""
    probe = " ".join(text.split()[:6])
    for document in rendered:
        if document.doc_id != doc_id:
            continue
        for record in document.records:
            joined = " ".join(record.text_lines)
            if probe in joined or probe in joined.replace("Memo: ", ""):
                return record.page_number
    raise ValueError(f"embedded text not found on {doc_id}")
