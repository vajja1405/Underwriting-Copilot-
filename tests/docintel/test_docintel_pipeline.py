"""Tests for run_packet (docintel/pipeline.py): stages, result contents, OCR modes and the hybrid extension point."""

import sys
from typing import Any

import pytest
from docintel_pipeline_fixtures import built_packet, pipeline_result

from uwcopilot.docintel import pipeline
from uwcopilot.docintel.checks import CREDIT_CHECK_IDS, INSURANCE_CHECK_IDS
from uwcopilot.docintel.classify import Segment
from uwcopilot.docintel.layout import PageText
from uwcopilot.docintel.pdftext import read_pdf_text
from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket, PacketExtraction


def test_credit_packet_result_holds_every_stage() -> None:
    _, truth = built_packet("PKT-C-015", "credit")
    result = pipeline_result("PKT-C-015", "credit")
    assert isinstance(result.extraction, LoanPacket)
    assert result.extraction.packet_id == "PKT-C-015"
    assert [check.check_id for check in result.checks] == list(CREDIT_CHECK_IDS)
    assert len(result.page_texts) == len(truth.pages)
    assert [item.doc_type for item in result.page_types] == [page.doc_type for page in truth.pages]
    assert result.ocr_pages == [] and result.tesseract_version is None
    assert result.llm_usage == {}
    assert set(pipeline.STAGES) | {"total"} == set(result.timings_ms)
    assert all(value >= 0 for value in result.timings_ms.values())
    assert result.timings_ms["total"] >= result.timings_ms["text_layer"]
    assert "nodes" in result.contradictions.graph_json
    assert result.model_dump(mode="json")["packet_id"] == "PKT-C-015"


def test_an_embedded_instruction_is_recorded_on_its_page_and_never_followed() -> None:
    _, truth = built_packet("PKT-I-013", "insurance")
    result = pipeline_result("PKT-I-013", "insurance")
    assert isinstance(result.extraction, InsuranceSubmission)
    assert [check.check_id for check in result.checks] == list(INSURANCE_CHECK_IDS)
    planted = {(item.doc_id, item.page_number) for item in truth.injections}
    found = {(hit.doc_id, hit.page) for hit in result.injection_hits}
    assert planted and planted <= found
    embedded = next(check for check in result.checks if check.check_id == "embedded_instructions")
    assert embedded.status == "fail"
    assert "prompt_injection" in {item.type for item in result.contradictions.contradictions}
    # The extraction still holds the printed values; the remarks text is kept as data.
    assert result.extraction.application is not None
    assert result.extraction.application.named_insured.found


def test_forced_ocr_sends_every_page_to_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    path, truth = built_packet("PKT-I-013", "insurance")
    text_layer = read_pdf_text(path)
    seen: list[int] = []

    def fake_ocr(pdf_path: Any, page_number: int, width: float, height: float) -> PageText:
        seen.append(page_number)
        page = text_layer[page_number - 1]
        assert page is not None
        return page.model_copy(update={"source": "ocr", "ocr_psm": 6})

    monkeypatch.setattr(pipeline, "ocr_page", fake_ocr)
    result = pipeline.run_packet(path, "PKT-I-013", "insurance", ocr="force")
    assert seen == list(range(1, len(truth.pages) + 1))
    assert result.ocr_pages == seen
    assert result.tesseract_version is not None
    assert all(item.ocr_used for item in result.extraction.documents)


def test_hybrid_extractor_receives_the_rules_extraction_and_the_documents() -> None:
    path, _ = built_packet("PKT-I-013", "insurance")
    calls: list[tuple[str, int]] = []

    def fake_hybrid(extraction: PacketExtraction, segments: list[Segment]) -> tuple[PacketExtraction, dict]:
        calls.append((extraction.packet_id, len(segments)))
        return extraction, {"calls": 1, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}

    result = pipeline.run_packet(path, "PKT-I-013", "insurance", extractor="hybrid", hybrid_extractor=fake_hybrid)
    assert calls == [("PKT-I-013", len(result.extraction.documents))]
    assert result.extractor == "hybrid"
    assert result.llm_usage["calls"] == 1


def test_hybrid_without_an_llm_extractor_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = built_packet("PKT-I-013", "insurance")
    monkeypatch.setitem(sys.modules, "uwcopilot.docintel.extract_llm", None)
    with pytest.raises(pipeline.HybridUnavailable, match="extract_llm"):
        pipeline.run_packet(path, "PKT-I-013", "insurance", extractor="hybrid")


@pytest.mark.parametrize(("extractor", "ocr"), [("llm", "auto"), ("rules", "never")])
def test_unknown_options_are_rejected(extractor: str, ocr: str) -> None:
    path, _ = built_packet("PKT-I-013", "insurance")
    with pytest.raises(ValueError, match="unknown"):
        pipeline.run_packet(path, "PKT-I-013", "insurance", extractor=extractor, ocr=ocr)  # type: ignore[arg-type]
