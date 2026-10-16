"""The report sections of the Phase 2 evaluation, from the per-packet records of evaluation.py.

The statistics (summed counters, ratios, P/R/F1, the packet-level percentile bootstrap with 1,000 resamples and seed
42, and the exact intervals) are in evaluation_stats.py and evaluation_intervals.py; this module re-exports them.
summarize() builds every section of one pipeline run: fields (overall, per field type, per page condition, per
document type), table rows, citations, page types, checklist, check statuses, contradictions, benign vs unsupported
explanations, injections, latency and cost. Each section that is easy to misread carries a "note" with its scope.

Paired comparisons (clean vs scanned pages, text layer vs forced OCR, rules vs hybrid) put both conditions of the
same packet into one row, so every resample uses the same packets for both and the interval of the difference is
not widened by packet-to-packet variation.
"""

from collections import Counter
from typing import Any

from uwcopilot.agent.evaluation import latency_summary
from uwcopilot.docintel.contradictions import CONTRADICTION_TYPES
from uwcopilot.docintel.evaluation import CONDITIONS, TABLE_ROWS
from uwcopilot.docintel.evaluation_problems import EXPLAINED_CLASSES, PREDICTED_CLASSES
from uwcopilot.docintel.evaluation_stats import (  # noqa: F401  (re-exported for scripts and tests)
    BOOTSTRAP_SEED,
    N_RESAMPLES,
    bootstrap,
    counter_suffixes,
    counts_of,
    f1_of,
    paired_f1_difference,
    precision_of,
    prf_metric,
    ratio_metric,
    recall_of,
    safe_ratio,
    totals,
)
from uwcopilot.docintel.evaluation_values import FIELD_TYPES
from uwcopilot.docintel.pipeline import STAGES

FIELD_MIX_NOTE = (
    "Micro-averaged over every value: bank-statement transaction rows are most of the values, so the overall F1 is "
    "mostly a transaction-table metric. See by_document_type, and the share of truth values per document type."
)


def doc_types_in(records: list[dict[str, Any]], condition: str) -> list[str]:
    """The document types that have field counters for one page condition."""
    prefix = f"fields.{condition}.doc."
    found = set()
    for record in records:
        for name in record["counts"]:
            if name.startswith(prefix):
                found.add(name[len(prefix) :].rsplit(".", 1)[0])
    return sorted(found)


def by_document_type(records: list[dict[str, Any]], condition: str) -> dict[str, Any]:
    """Field P/R/F1 per true document type for one page condition, with each type's share of the truth values."""
    summed = totals(counts_of(records))
    truth_total = summed.get(f"fields.{condition}.all.tp", 0) + summed.get(f"fields.{condition}.all.fn", 0)
    result = {}
    for doc_type in doc_types_in(records, condition):
        prefix = f"fields.{condition}.doc.{doc_type}"
        metric = prf_metric(records, prefix)
        share = (metric["tp"] + metric["fn"]) / truth_total if truth_total else None
        metric["share_of_truth_values"] = None if share is None else round(share, 4)
        result[doc_type] = metric
    return result


def field_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Field P/R/F1 overall, per field type, per page condition (clean = text layer, scanned) and per document type."""
    result: dict[str, Any] = {"note": FIELD_MIX_NOTE, "overall": prf_metric(records, "fields.all.all")}
    result["by_type"] = {kind: prf_metric(records, f"fields.all.{kind}") for kind in FIELD_TYPES}
    result["by_document_type"] = by_document_type(records, "all")
    result["by_condition"] = {}
    for condition in CONDITIONS[1:]:
        result["by_condition"][condition] = {
            "overall": prf_metric(records, f"fields.{condition}.all"),
            "by_type": {kind: prf_metric(records, f"fields.{condition}.{kind}") for kind in FIELD_TYPES},
            "by_document_type": by_document_type(records, condition),
        }
    return result


def row_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Row-level P/R for transactions, claims and locations, and the share of found rows with every field right."""
    result = {}
    for name in TABLE_ROWS:
        metric = prf_metric(records, f"rows.{name}")
        metric["exact_share_of_found"] = ratio_metric(records, f"rows.{name}.exact", f"rows.{name}.tp")
        result[name] = metric
    return result


CONTRADICTION_NOTE = (
    "A contradiction matches an injected problem by type and a shared document or field. The *_evidence_correct "
    "metrics count a match only when its cited values on the problem's documents equal the true values (an OCR "
    "misread fails). duplicate_matches counts matches beyond the first for one problem; every matching "
    "contradiction counts as correct in precision."
)


def contradiction_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Contradiction recall and precision overall and per type (also evidence-correct only), and by condition."""
    summed = totals(counts_of(records))
    result: dict[str, Any] = {
        "note": CONTRADICTION_NOTE,
        "recall": ratio_metric(records, "problems.all.detected", "problems.all.total"),
        "precision": ratio_metric(records, "contradictions.all.tp", "contradictions.all.total"),
        "recall_evidence_correct": ratio_metric(
            records, "problems.all.detected_evidence_correct", "problems.all.total"
        ),
        "precision_evidence_correct": ratio_metric(
            records, "contradictions.all.tp_evidence_correct", "contradictions.all.total"
        ),
        "duplicate_matches": int(summed.get("problems.all.duplicate_matches", 0)),
        "by_type": {},
    }
    for kind in CONTRADICTION_TYPES:
        result["by_type"][kind] = {
            "recall": ratio_metric(records, f"problems.{kind}.detected", f"problems.{kind}.total"),
            "precision": ratio_metric(records, f"contradictions.{kind}.tp", f"contradictions.{kind}.total"),
            "recall_evidence_correct": ratio_metric(
                records, f"problems.{kind}.detected_evidence_correct", f"problems.{kind}.total"
            ),
        }
    clean = [record for record in records if record["n_scanned_pages"] == 0]
    scanned = [record for record in records if record["n_scanned_pages"] > 0]
    result["precision_by_packet_condition"] = {
        "packets_without_scanned_pages": ratio_metric(clean, "contradictions.all.tp", "contradictions.all.total"),
        "packets_with_scanned_pages": ratio_metric(scanned, "contradictions.all.tp", "contradictions.all.total"),
    }
    return result


EXPLANATION_NOTE = (
    "Headline accuracy counts 'unknown' as wrong (spec). designed_abstentions are problems the rules answer "
    "'unknown' by design whatever the extraction (an unexplained TIV difference within the 15.0% HM-5.2 band); "
    "accuracy_excluding_designed_abstentions leaves them out. unknown_by_code says why each 'unknown' was given "
    "(possible_ocr_misread = the OCR review downgraded it). One matching contradiction represents each problem: "
    "evidence-correct first, then not 'unknown', then report order."
)


def explanation_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Benign-vs-unsupported accuracy on detected problems, with the confusion, per type and abstention views."""
    summed = totals(counts_of(records))
    result: dict[str, Any] = {
        "note": EXPLANATION_NOTE,
        "accuracy": ratio_metric(records, "explanations.all.correct", "explanations.all.total"),
        "unknown_share": ratio_metric(records, "explanations.all.unknown", "explanations.all.total"),
        "accuracy_excluding_designed_abstentions": ratio_metric(
            records, "explanations.decidable.correct", "explanations.decidable.total"
        ),
        "designed_abstentions": int(summed.get("explanations.designed_abstention.total", 0)),
        "confusion_matrix": {
            truth: {
                predicted: int(summed.get(f"explanations.confusion.{truth}.{predicted}", 0))
                for predicted in PREDICTED_CLASSES
            }
            for truth in EXPLAINED_CLASSES
        },
        "unknown_by_code": {
            code: int(summed.get(f"explanations.unknown_code.{code}", 0))
            for code in counter_suffixes(records, "explanations.unknown_code.")
        },
        "by_class": {},
        "by_problem_type": {},
    }
    for group in EXPLAINED_CLASSES:
        result["by_class"][group] = {
            "accuracy": ratio_metric(records, f"explanations.{group}.correct", f"explanations.{group}.total"),
            "unknown_share": ratio_metric(records, f"explanations.{group}.unknown", f"explanations.{group}.total"),
        }
    for kind in counter_suffixes(records, "explanations.type."):
        prefix = f"explanations.type.{kind}"
        result["by_problem_type"][kind] = {
            "accuracy": ratio_metric(records, f"{prefix}.correct", f"{prefix}.total"),
            "unknown": int(summed.get(f"{prefix}.unknown", 0)),
        }
    return result


def injection_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Injection recall (overall and by technique) and per-packet false-positive rates."""
    techniques: dict[str, Counter] = {}
    for record in records:
        for outcome in record["injections"]:
            counter = techniques.setdefault(outcome["technique"], Counter())
            counter["total"] += 1
            counter["detected"] += int(outcome["detected"])
    return {
        "recall": ratio_metric(records, "injections.detected", "injections.total"),
        "by_technique": {name: dict(counter) for name, counter in sorted(techniques.items())},
        "per_packet_false_positive_rate": ratio_metric(
            records, "injection_packets.with_false_hit", "injection_packets.total"
        ),
        "clean_packet_false_positive_rate": ratio_metric(
            records, "injection_packets.clean_with_hit", "injection_packets.clean"
        ),
        "false_hits": int(totals(counts_of(records)).get("injection_hits.false", 0)),
    }


def latency_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """p50 / p95 milliseconds per stage and in total (agent.evaluation.latency_summary), plus OCR page counts."""
    result: dict[str, Any] = {"per_stage_ms": {}}
    for stage in (*STAGES, "total"):
        result["per_stage_ms"][stage] = latency_summary(
            [float(record["timings_ms"].get(stage, 0.0)) for record in records]
        )
    ocr_records = [record for record in records if record["ocr_pages"] > 0]
    per_page = [record["timings_ms"].get("ocr", 0.0) / record["ocr_pages"] for record in ocr_records]
    result["ocr_pages_total"] = sum(record["ocr_pages"] for record in records)
    result["packets_with_ocr"] = len(ocr_records)
    result["ocr_ms_per_page"] = latency_summary(per_page)
    return result


def cost_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM calls, tokens and dollars per packet; a cost that is None for any packet makes the total None."""
    costs = [record["usage"]["cost_usd"] for record in records]
    known = None not in costs
    total = sum(costs) if known and costs else (0.0 if not costs else None)
    return {
        "llm_calls": sum(record["usage"]["llm_calls"] for record in records),
        "input_tokens": sum(record["usage"]["input_tokens"] for record in records),
        "output_tokens": sum(record["usage"]["output_tokens"] for record in records),
        "usd_total": None if total is None else round(total, 6),
        "usd_per_packet_mean": None if total is None or not records else round(total / len(records), 6),
    }


CITATION_NOTE = (
    "page_level_on_correct_values is a sanity check: the rules extractor cites the page it read, so it is 1.0 by "
    "construction unless a printed form is missed. supported_all_values checks every extracted value (correct or "
    "not) against the printed text of the cited page. line_level_on_correct_values checks the cited line: same line "
    "number on text-layer pages, box within half a line after aligning a scanned page (evaluation_citations.py)."
)


def citation_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Page-level, supported and line-level citation metrics (evaluation_citations.py), with their definitions."""
    summed = totals(counts_of(records))
    return {
        "note": CITATION_NOTE,
        "page_level_on_correct_values": ratio_metric(records, "citations.page.all.correct", "citations.page.all.total"),
        "cited_page_is_truth_page": ratio_metric(records, "citations.page.all.same_page", "citations.page.all.total"),
        "supported_all_values": ratio_metric(
            records, "citations.supported.all.correct", "citations.supported.all.total"
        ),
        "line_level_on_correct_values": ratio_metric(records, "citations.line.all.correct", "citations.line.all.total"),
        "line_level_unscored_values": int(summed.get("citations.line_unscored", 0)),
        "by_type": {
            kind: {
                "page_level": ratio_metric(records, f"citations.page.{kind}.correct", f"citations.page.{kind}.total"),
                "supported": ratio_metric(
                    records, f"citations.supported.{kind}.correct", f"citations.supported.{kind}.total"
                ),
                "line_level": ratio_metric(records, f"citations.line.{kind}.correct", f"citations.line.{kind}.total"),
            }
            for kind in FIELD_TYPES
        },
    }


def checklist_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Checklist accuracy overall, per packet, per checklist code, and documents_provided accuracy."""
    summed = totals(counts_of(records))
    by_code = {}
    for code in counter_suffixes(records, "checklist.code."):
        prefix = f"checklist.code.{code}"
        metric = ratio_metric(records, f"{prefix}.correct", f"{prefix}.total")
        metric["truth_missing"] = int(summed.get(f"{prefix}.truth_missing", 0))
        by_code[code] = metric
    return {
        "code_accuracy": ratio_metric(records, "checklist.correct", "checklist.total"),
        "packet_exact_match": ratio_metric(records, "checklist.packets_exact", "checklist.packets"),
        "documents_provided_accuracy": ratio_metric(records, "provided.correct", "provided.total"),
        "by_code": by_code,
    }


def check_status_metrics(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For every check id: in how many packets it passed, failed or was not evaluated."""
    summed = totals(counts_of(records))
    result: dict[str, dict[str, int]] = {}
    for check_id in counter_suffixes(records, "checks."):
        result[check_id] = {
            status: int(summed.get(f"checks.{check_id}.{status}", 0)) for status in ("pass", "fail", "not_evaluated")
        }
    return result


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every metric of one pipeline run (one extractor and OCR mode) over its packets."""
    lines = Counter(record["line"] for record in records)
    return {
        "n_packets": len(records),
        "packets_by_line": dict(sorted(lines.items())),
        "fields": field_metrics(records),
        "fields_by_line": {
            line: prf_metric([record for record in records if record["line"] == line], "fields.all.all")
            for line in sorted(lines)
        },
        "rows": row_metrics(records),
        "citations": citation_metrics(records),
        "page_classification": {
            condition: ratio_metric(records, f"pages.{condition}.correct", f"pages.{condition}.total")
            for condition in CONDITIONS
        },
        "document_segmentation": ratio_metric(records, "documents.correct", "documents.total"),
        "checklist": checklist_metrics(records),
        "check_status_counts": check_status_metrics(records),
        "contradictions": contradiction_metrics(records),
        "benign_vs_unsupported": explanation_metrics(records),
        "injections": injection_metrics(records),
        "latency": latency_metrics(records),
        "cost": cost_metrics(records),
    }
