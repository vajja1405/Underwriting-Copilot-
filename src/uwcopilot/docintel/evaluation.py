"""Evaluation of the document pipeline against the SYNTHETIC ground truth: per-packet matching and counting.

evaluate_packet(truth, result, documents_provided) compares one PacketResult (docintel/pipeline.py) with the
packet's ground truth and returns a JSON-simple record of COUNTS. evaluation_summary.py adds the counts of many
packets, computes the metrics and their bootstrap confidence intervals. Every function here is deterministic.

The truth is read by attribute only (a truth.PacketTruth works). This module does not import truth.py: the spec
lets only scripts and tests import it, so the pipeline and the evaluation code stay apart from the generator.

What is counted (see each function):
- fields: (field, key, value) rows of schemas.flatten_fields matched with the truth's FieldTruth rows on (field,
  key) with normalized values (normalize_value). A row with the right (field, key) but a wrong value counts as one
  false positive AND one false negative (the usual rule for extraction). Counted per field type (FIELD_TYPES) and
  per page condition: "clean" (text layer) or "scanned" pages of the TRUTH row (a false positive takes the
  condition of the page it cites; one without a page is counted only under "all").
- rows: table rows (transactions, claims, locations) matched by their row key; "exact" rows have every field right;
- citations (evaluation_citations.py): page level for correct values (the cited page prints the value; the cited
  page is the true page: a sanity check for the rules extractor), "supported" for EVERY extracted value (the cited
  page prints the extracted value, so a misread fails) and line level for correct values (the cited line is the
  true line: same line number on a text-layer page, box within half a line after aligning a scanned page);
- fields are also counted per true document type of their page (fields.<condition>.doc.<doc_type>), because the
  bank-statement rows dominate the overall count;
- pages: predicted page type vs the truth document type of every page; documents: truth documents whose exact
  pages and type the pipeline found;
- checklist: for every checklist code of the line, "missing" (required_documents check) vs the truth's
  missing_required_documents (also per code: checklist.code.<code>), and "provided" (to_application's
  documents_provided) vs the as-documented application;
- checks: how often each check passed, failed or was not evaluated (checks.<check_id>.<status>), so a check the
  SYNTHETIC packets never exercise is visible;
- contradictions: detected contradictions matched with injected problems (match_contradictions);
- injections: planted instructions found on their page by the pipeline (page-text scan and free-text fields, as
  the embedded_instructions check reports them), and hits on other pages.
"""

from collections import Counter
from typing import Any, NamedTuple

from uwcopilot.agent.applications import CREDIT_DOCUMENTS, INSURANCE_DOCUMENTS
from uwcopilot.docintel.evaluation_citations import Located, line_counts, oracle_positions, true_position
from uwcopilot.docintel.evaluation_problems import contradiction_counts, injection_counts, match_contradictions
from uwcopilot.docintel.evaluation_values import (  # noqa: F401  (FIELD_TYPES is re-exported)
    FIELD_TYPES,
    field_type,
    normalize_value,
    printed_forms,
    value_on_page,
)
from uwcopilot.docintel.pipeline import PacketResult
from uwcopilot.docintel.schemas import flatten_fields

CONDITIONS: tuple[str, ...] = ("all", "clean", "scanned")
TABLE_ROWS: dict[str, str] = {
    "transactions": "statement.transactions",
    "claims": "loss_run.claims",
    "locations": "property_schedule.locations",
}


class Row(NamedTuple):
    """One extracted or true value: field path, row key, value, document, page, line and box (None when unknown)."""

    field: str
    key: str | None
    value: Any
    doc_id: str | None
    page: int | None
    line: int | None = None
    bbox: tuple[float, float, float, float] | None = None


def truth_rows(truth: Any) -> list[Row]:
    """The truth's FieldTruth rows as Rows."""
    return [Row(item.field, item.key, item.value, item.doc_id, item.page_number) for item in truth.fields]


def predicted_rows(extraction: Any) -> list[Row]:
    """The extraction flattened (schemas.flatten_fields) into Rows."""
    rows = []
    for item in flatten_fields(extraction):
        rows.append(Row(item.field, item.key, item.value, item.doc_id, item.page, item.line, item.bbox))
    return rows


class FieldMatch(NamedTuple):
    """The result of matching predicted rows with truth rows."""

    pairs: list[tuple[Row, Row]]  # (truth row, predicted row) with equal normalized values
    missed: list[Row]  # truth rows without a correct prediction (false negatives)
    extra: list[Row]  # predicted rows without a matching truth row (false positives)


def match_fields(truth: list[Row], predicted: list[Row]) -> FieldMatch:
    """Match rows on (field, key) and normalized value; duplicates are matched one to one, in order."""
    expected: dict[tuple[str, str | None], list[Row]] = {}
    for row in truth:
        expected.setdefault((row.field, row.key), []).append(row)
    pairs: list[tuple[Row, Row]] = []
    extra: list[Row] = []
    for row in predicted:
        candidates = expected.get((row.field, row.key), [])
        kind = field_type(row.field)
        wanted = normalize_value(kind, row.value)
        hit = next((item for item in candidates if normalize_value(kind, item.value) == wanted), None)
        if hit is None:
            extra.append(row)
        else:
            candidates.remove(hit)
            pairs.append((hit, row))
    missed = [row for rows in expected.values() for row in rows]
    return FieldMatch(pairs, missed, extra)


def page_conditions(truth: Any) -> dict[int, str]:
    """{page number: "scanned" or "clean"} from the truth pages."""
    return {page.page_number: ("scanned" if page.scanned else "clean") for page in truth.pages}


def field_counts(match: FieldMatch, conditions: dict[int, str], doc_types: dict[int, str] | None = None) -> Counter:
    """tp / fp / fn per condition (all, clean, scanned) and field type (plus "all"), as flat counter names, and
    per condition and true document type of the page (fields.<condition>.doc.<doc_type>.<outcome>)."""
    counts: Counter = Counter()
    page_types = doc_types or {}

    def add(row: Row, outcome: str, page: int | None) -> None:
        groups = ["all"]
        if page in conditions:
            groups.append(conditions[page])
        for group in groups:
            for kind in ("all", field_type(row.field)):
                counts[f"fields.{group}.{kind}.{outcome}"] += 1
            if page in page_types:
                counts[f"fields.{group}.doc.{page_types[page]}.{outcome}"] += 1

    for truth_row, _ in match.pairs:
        add(truth_row, "tp", truth_row.page)
    for row in match.missed:
        add(row, "fn", row.page)
    for row in match.extra:
        add(row, "fp", row.page)
    return counts


def row_counts(truth: list[Row], predicted: list[Row], match: FieldMatch) -> Counter:
    """Table rows (TABLE_ROWS) matched by row key: tp / fp / fn rows and "exact" rows with every field right."""
    counts: Counter = Counter()
    wrong_keys = {(row.field.rsplit(".", 1)[0], row.key) for row in [*match.missed, *match.extra]}
    for name, prefix in TABLE_ROWS.items():
        true_rows = rows_per_key(truth, prefix)
        found_rows = rows_per_key(predicted, prefix)
        for key in set(true_rows) | set(found_rows):
            matched = min(true_rows.get(key, 0), found_rows.get(key, 0))
            counts[f"rows.{name}.tp"] += matched
            counts[f"rows.{name}.fn"] += true_rows.get(key, 0) - matched
            counts[f"rows.{name}.fp"] += found_rows.get(key, 0) - matched
            if matched and (prefix, key) not in wrong_keys:
                counts[f"rows.{name}.exact"] += matched
    return counts


def rows_per_key(rows: list[Row], prefix: str) -> dict[str | None, int]:
    """How many table rows each row key has: the largest number of values of one field under that key."""
    per_field: Counter = Counter((row.key, row.field) for row in rows if row.field.rsplit(".", 1)[0] == prefix)
    result: dict[str | None, int] = {}
    for (key, _field), count in per_field.items():
        result[key] = max(result.get(key, 0), count)
    return result


def citation_counts(match: FieldMatch, page_lines: dict[int, list[str]]) -> Counter:
    """citations.page.* for correct values and citations.supported.* for every extracted value with a page
    (see evaluation_citations.py for the definitions)."""
    counts: Counter = Counter()
    for truth_row, found in match.pairs:
        for kind in ("all", field_type(truth_row.field)):
            counts[f"citations.page.{kind}.total"] += 1
            lines = page_lines.get(found.page) if found.page is not None else None
            if lines is not None and value_on_page(truth_row.field, truth_row.value, lines):
                counts[f"citations.page.{kind}.correct"] += 1
            if found.page == truth_row.page:
                counts[f"citations.page.{kind}.same_page"] += 1
    for found in [row for _, row in match.pairs] + match.extra:
        if found.page is None or found.value is None:
            continue
        lines = page_lines.get(found.page, [])
        supported = value_on_page(found.field, found.value, lines)
        for kind in ("all", field_type(found.field)):
            counts[f"citations.supported.{kind}.total"] += 1
            counts[f"citations.supported.{kind}.correct"] += int(supported)
    return counts


def same_value(found: Row, oracle: Located) -> bool:
    """True when an extracted row and an oracle entry hold the same normalized value."""
    kind = field_type(found.field)
    return normalize_value(kind, found.value) == normalize_value(kind, oracle.value)


def citation_line_counts(match: FieldMatch, truth: Any) -> Counter:
    """citations.line.* for the correct values (evaluation_citations.line_counts)."""
    oracle = oracle_positions(truth.oracle_extraction)
    scanned = {page.page_number for page in truth.pages if page.scanned}
    matched = []
    for _, found in match.pairs:
        position = true_position(found, oracle, same_value)
        if position is not None:
            cited = Located(found.field, found.key, found.value, found.page, found.line, found.bbox)
            matched.append((cited, position, found.field))
    return line_counts(matched, scanned, field_type)


def check_status_counts(result: PacketResult) -> Counter:
    """checks.<check_id>.<status>: how each check of the packet ended (pass / fail / not_evaluated)."""
    counts: Counter = Counter()
    for check in result.checks:
        counts[f"checks.{check.check_id}.{check.status}"] += 1
    return counts


def page_counts(truth: Any, result: PacketResult) -> Counter:
    """Page type accuracy (all / clean / scanned pages) and exactly recovered documents (pages and type)."""
    counts: Counter = Counter()
    predicted = {item.page_number: item.doc_type for item in result.page_types}
    for page in truth.pages:
        right = predicted.get(page.page_number) == page.doc_type
        for group in ("all", "scanned" if page.scanned else "clean"):
            counts[f"pages.{group}.total"] += 1
            counts[f"pages.{group}.correct"] += int(right)
    found = {(document.doc_type, tuple(document.pages)) for document in result.extraction.documents}
    for document in truth.documents:
        if document.pages:
            counts["documents.total"] += 1
            counts["documents.correct"] += int((document.doc_type, tuple(document.pages)) in found)
    return counts


def checklist_counts(truth: Any, result: PacketResult, documents_provided: list[str] | None) -> Counter:
    """Per checklist code: predicted missing vs truth missing, and predicted provided vs as-documented provided."""
    counts: Counter = Counter()
    codes = CREDIT_DOCUMENTS if truth.line == "credit" else INSURANCE_DOCUMENTS
    check = next((item for item in result.checks if item.check_id == "required_documents"), None)
    predicted_missing = set(check.values.get("missing", [])) if check is not None else set()
    true_missing = set(truth.missing_required_documents)
    for code in codes:
        right = int((code in predicted_missing) == (code in true_missing))
        counts["checklist.total"] += 1
        counts["checklist.correct"] += right
        counts[f"checklist.code.{code}.total"] += 1
        counts[f"checklist.code.{code}.correct"] += right
        counts[f"checklist.code.{code}.truth_missing"] += int(code in true_missing)
    counts["checklist.packets"] += 1
    counts["checklist.packets_exact"] += int(predicted_missing == true_missing)
    if documents_provided is not None:
        true_provided = set(truth.application_as_documented.get("documents_provided", []))
        for code in codes:
            counts["provided.total"] += 1
            counts["provided.correct"] += int((code in documents_provided) == (code in true_provided))
    return counts


def usage_summary(result: PacketResult) -> dict[str, Any]:
    """LLM calls, tokens and cost of the packet (the rules extractor makes no call: zero calls, cost 0.0)."""
    usage = result.llm_usage
    return {
        "llm_calls": int(usage.get("calls", 0)),
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cost_usd": usage.get("cost_usd", 0.0) if usage else 0.0,
    }


def evaluate_packet(truth: Any, result: PacketResult, documents_provided: list[str] | None = None) -> dict[str, Any]:
    """Compare one pipeline result with its truth; returns the packet's JSON-simple evaluation record.

    documents_provided is to_application's documents_provided for this result (None skips that comparison).
    The record holds the counters (see the module docstring), the stage timings, the OCR page count, LLM usage
    and one outcome per injected problem, detected contradiction and planted instruction.
    """
    expected = truth_rows(truth)
    found = predicted_rows(result.extraction)
    match = match_fields(expected, found)
    page_lines = {page.page_number: list(page.text_lines) for page in truth.pages}
    page_docs = {page.page_number: page.doc_id for page in truth.pages}
    counts: Counter = Counter()
    doc_types = {page.page_number: page.doc_type for page in truth.pages}
    counts.update(field_counts(match, page_conditions(truth), doc_types))
    counts.update(row_counts(expected, found, match))
    counts.update(citation_counts(match, page_lines))
    if truth.pages:
        counts.update(citation_line_counts(match, truth))
    counts.update(check_status_counts(result))
    counts.update(page_counts(truth, result))
    counts.update(checklist_counts(truth, result, documents_provided))
    doc_type_of = {document.doc_id: document.doc_type for document in truth.documents}
    problems, contradictions = match_contradictions(
        list(truth.problems), list(result.contradictions.contradictions), page_docs, doc_type_of
    )
    counts.update(contradiction_counts(problems, contradictions))
    injection_counter, injections = injection_counts(truth, result)
    counts.update(injection_counter)
    return {
        "packet_id": truth.packet_id,
        "line": truth.line,
        "scenario": truth.scenario,
        "n_pages": len(truth.pages),
        "n_scanned_pages": sum(1 for page in truth.pages if page.scanned),
        "extractor": result.extractor,
        "ocr_mode": result.ocr_mode,
        "counts": dict(sorted(counts.items())),
        "timings_ms": dict(result.timings_ms),
        "ocr_pages": len(result.ocr_pages),
        "usage": usage_summary(result),
        "problems": problems,
        "contradictions": contradictions,
        "injections": injections,
    }
