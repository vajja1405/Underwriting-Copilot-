"""The document-intelligence pipeline for one SYNTHETIC packet PDF: text -> documents -> extraction -> checks.

run_packet(pdf_path, packet_id, line, extractor="rules", ocr="auto") runs these stages and times each one:
1. text_layer: pdfplumber reads every page's text layer (pdftext.py);
2. ocr: pages without a text layer (fewer than 20 characters) are read with Tesseract (ocr.py). ocr="force" sends
   EVERY page to OCR (used to compare OCR with the text layer on the same clean pages);
3. classify: every page gets a document type and pages are grouped into documents D01, D02, ... (classify.py);
4. extract: the rules extractor (extract_rules.py) builds the LoanPacket / InsuranceSubmission. With
   extractor="hybrid" the rules extraction is then passed to an LLM extractor (see "Hybrid extension point");
5. injection_scan: every page's lines are scanned for instruction-like text (injection_scan.scan_pages; the page
   texts are passed directly: PageText has page_number and lines with text and line_number);
6. checks: the deterministic checks (checks.run_checks, with the page-text injection hits);
7. contradictions: the contradiction report and graph (contradictions.build_contradictions).

The result (PacketResult) holds the extraction, check results, contradictions, injection hits, the page texts,
timings in milliseconds per stage, the pages that were OCR'd, the Tesseract version and the LLM usage (empty for
the rules extractor).

Document text is untrusted data throughout: no stage follows anything written in a document. Injection hits are
recorded for review; they never change what the pipeline does.

Hybrid extension point (implemented by docintel/extract_llm.py, written separately): an LLM extractor is any
callable with the HybridExtractor signature
    (rules_extraction, segments) -> (extraction, llm_usage)
It receives the rules extraction and the segmented documents (classify.Segment, each with its PageText pages) and
returns the improved extraction (the same schema) and a JSON-simple usage dict (calls, tokens, cost). run_packet
uses the callable passed as hybrid_extractor; when none is passed it imports uwcopilot.docintel.extract_llm and
uses its extract_hybrid function. If that module does not exist yet, extractor="hybrid" raises
HybridUnavailable with an explanation. The rules extractor never needs it.
"""

import importlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.classify import PageClass, Segment, segment_pages
from uwcopilot.docintel.contradictions import ContradictionReport, build_contradictions
from uwcopilot.docintel.extract_rules import extract_with_rules
from uwcopilot.docintel.injection_scan import DocumentInjectionHit, scan_pages
from uwcopilot.docintel.layout import PageText
from uwcopilot.docintel.ocr import ocr_page, tesseract_version
from uwcopilot.docintel.pdftext import read_text_layer
from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket, PacketExtraction

ExtractorName = Literal["rules", "hybrid"]
OcrMode = Literal["auto", "force"]
HybridExtractor = Callable[[PacketExtraction, list[Segment]], tuple[PacketExtraction, dict[str, Any]]]
STAGES = ("text_layer", "ocr", "classify", "extract", "llm", "injection_scan", "checks", "contradictions")


class HybridUnavailable(RuntimeError):
    """extractor="hybrid" was requested but no LLM extractor is available."""


class PageType(BaseModel):
    """The predicted document type of one page and the classifier's confidence."""

    model_config = ConfigDict(extra="forbid")

    page_number: int
    doc_type: str
    confidence: float


class PacketResult(BaseModel):
    """Everything the pipeline produced for one packet (see the module docstring)."""

    model_config = ConfigDict(extra="forbid")

    packet_id: str
    line: str
    extractor: str
    ocr_mode: str
    extraction: Annotated[LoanPacket | InsuranceSubmission, Field(discriminator="line")]
    checks: list[CheckResult]
    contradictions: ContradictionReport
    injection_hits: list[DocumentInjectionHit]
    page_texts: list[PageText]
    page_types: list[PageType]
    timings_ms: dict[str, float]
    ocr_pages: list[int]
    tesseract_version: str | None
    llm_usage: dict[str, Any]


class StageTimer:
    """Wall-clock milliseconds per stage."""

    def __init__(self) -> None:
        self.timings: dict[str, float] = {stage: 0.0 for stage in STAGES}
        self.started = time.perf_counter()

    def add(self, stage: str, since: float) -> float:
        """Add the time since `since` to a stage; returns the current time for the next stage."""
        now = time.perf_counter()
        self.timings[stage] = round(self.timings.get(stage, 0.0) + (now - since) * 1000.0, 3)
        return now

    def finish(self) -> dict[str, float]:
        """The timings with the total."""
        result = dict(self.timings)
        result["total"] = round((time.perf_counter() - self.started) * 1000.0, 3)
        return result


def read_pages(pdf_path: Path, ocr: OcrMode, timer: StageTimer) -> tuple[list[PageText], list[int]]:
    """Every page's text (text layer, or OCR for pages without one / for every page with ocr="force")."""
    since = time.perf_counter()
    texts: list[PageText | None] = []
    sizes: list[tuple[float, float]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            sizes.append((float(page.width), float(page.height)))
            texts.append(None if ocr == "force" else read_text_layer(page, number))
    since = timer.add("text_layer", since)
    ocr_pages = [number for number, text in enumerate(texts, start=1) if text is None]
    pages: list[PageText] = []
    for number, text in enumerate(texts, start=1):
        if text is None:
            width, height = sizes[number - 1]
            text = ocr_page(pdf_path, number, width, height)
        pages.append(text)
    timer.add("ocr", since)
    return pages, ocr_pages


def default_hybrid_extractor() -> HybridExtractor:
    """extract_llm.extract_hybrid, or HybridUnavailable when that module is not available.

    importlib.import_module looks the module up in sys.modules first, so a module blocked there (set to None) is
    reported as unavailable even after another part of the program has imported it.
    """
    try:
        extract_llm = importlib.import_module("uwcopilot.docintel.extract_llm")  # optional, imported only here
    except ImportError as error:
        raise HybridUnavailable(
            "extractor='hybrid' needs uwcopilot.docintel.extract_llm (extract_hybrid); it is not available"
        ) from error
    return extract_llm.extract_hybrid


def page_types(classes: list[PageClass], segments: list[Segment]) -> list[PageType]:
    """The final type of every page (the type of the document it was put in) with the classifier's confidence."""
    final = {page.page_number: segment.doc_type for segment in segments for page in segment.pages}
    return [
        PageType(page_number=item.page_number, doc_type=final[item.page_number], confidence=item.confidence)
        for item in classes
    ]


def run_packet(
    pdf_path: Path,
    packet_id: str,
    line: str,
    extractor: ExtractorName = "rules",
    ocr: OcrMode = "auto",
    hybrid_extractor: HybridExtractor | None = None,
) -> PacketResult:
    """Run the whole pipeline on one packet PDF (see the module docstring)."""
    if extractor not in ("rules", "hybrid"):
        raise ValueError(f"unknown extractor {extractor!r} (expected 'rules' or 'hybrid')")
    if ocr not in ("auto", "force"):
        raise ValueError(f"unknown ocr mode {ocr!r} (expected 'auto' or 'force')")
    timer = StageTimer()
    pages, ocr_pages = read_pages(Path(pdf_path), ocr, timer)
    since = time.perf_counter()
    segments, classes = segment_pages(pages, line)
    since = timer.add("classify", since)
    extraction = extract_with_rules(packet_id, line, segments)
    since = timer.add("extract", since)
    llm_usage: dict[str, Any] = {}
    if extractor == "hybrid":
        refine = hybrid_extractor or default_hybrid_extractor()
        extraction, llm_usage = refine(extraction, segments)
        since = timer.add("llm", since)
    doc_ids = {page.page_number: segment.doc_id for segment in segments for page in segment.pages}
    hits = scan_pages(pages, doc_ids)
    since = timer.add("injection_scan", since)
    checks = run_checks(extraction, hits)
    since = timer.add("checks", since)
    report = build_contradictions(extraction, checks)
    timer.add("contradictions", since)
    return PacketResult(
        packet_id=packet_id,
        line=line,
        extractor=extractor,
        ocr_mode=ocr,
        extraction=extraction,
        checks=checks,
        contradictions=report,
        injection_hits=hits,
        page_texts=pages,
        page_types=page_types(classes, segments),
        timings_ms=timer.finish(),
        ocr_pages=ocr_pages,
        tesseract_version=tesseract_version() if ocr_pages else None,
        llm_usage=llm_usage,
    )
