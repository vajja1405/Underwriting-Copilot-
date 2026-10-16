"""Unit tests for the docintel evaluation (evaluation.py, evaluation_problems.py, evaluation_summary.py).

Every case is hand-built: tiny truths (SimpleNamespace objects with the attributes of truth.PacketTruth) and tiny
extractions, so each metric can be checked by hand: missed rows, extra rows, an OCR-misread amount, a wrong page
citation, duplicate transactions, contradiction matching rules, injection hits and the bootstrap.
"""

import typing
from collections import Counter
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from uwcopilot.docintel import evaluation as ev
from uwcopilot.docintel import evaluation_problems as problems_eval
from uwcopilot.docintel import evaluation_summary as summary
from uwcopilot.docintel.check_models import Evidence
from uwcopilot.docintel.contradictions import Contradiction
from uwcopilot.docintel.schemas import (
    BankStatement,
    Cited,
    DocumentInfo,
    InsuranceSubmission,
    LoanApplicationForm,
    LoanPacket,
    Transaction,
)


def cited(value: object, page: int, doc_id: str = "D01", line: int = 1) -> Cited:
    """A cited value on a page."""
    return Cited(value=value, doc_id=doc_id, page=page, line=line, confidence=1.0)


def truth_row(field: str, value: object, page: int, key: str | None = None, doc_id: str = "D01") -> SimpleNamespace:
    """A FieldTruth-like row."""
    return SimpleNamespace(field=field, key=key, value=value, doc_id=doc_id, page_number=page)


def page(number: int, lines: list[str], scanned: bool = False, doc_type: str = "loan_application") -> SimpleNamespace:
    """A PageTruth-like page."""
    return SimpleNamespace(page_number=number, doc_id="D01", doc_type=doc_type, scanned=scanned, text_lines=lines)


# --------------------------------------------------------------------------------------------------------------
# Field types and normalization
# --------------------------------------------------------------------------------------------------------------


def schema_leaves(model: type[BaseModel]) -> set[str]:
    """Every Cited attribute name reachable from a schema model."""
    leaves: set[str] = set()
    for name, info in model.model_fields.items():
        annotation = info.annotation
        candidates = [annotation, *typing.get_args(annotation)]
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, Cited):
                leaves.add(name)
            elif isinstance(candidate, type) and issubclass(candidate, BaseModel):
                leaves |= schema_leaves(candidate)
    return leaves


def test_every_schema_field_has_exactly_one_type() -> None:
    leaves = schema_leaves(LoanPacket) | schema_leaves(InsuranceSubmission)
    typed = [leaf for kinds in ev.FIELD_TYPES.values() for leaf in kinds]
    assert len(typed) == len(set(typed)), "a field name is listed under two types"
    assert leaves == set(typed)


@pytest.mark.parametrize(
    ("field", "kind"),
    [
        ("statement.transactions.deposit", "money"),
        ("application.owners.ownership_pct", "percent"),
        ("application.fein", "identifier"),
        ("loss_run.claims.status", "code"),
        ("credit_report.entries.credit_score", "integer"),
        ("application.remarks", "free_text"),
        ("statement.period_start", "date"),
    ],
)
def test_field_type_uses_the_last_path_part(field: str, kind: str) -> None:
    assert ev.field_type(field) == kind


def test_normalize_value_rules() -> None:
    assert ev.normalize_value("money", 1234.5) == ev.normalize_value("money", 1234.504)
    assert ev.normalize_value("money", 1234.5) != ev.normalize_value("money", 1234.56)
    assert ev.normalize_value("name", "  Acme   Tools LLC ") == "acme tools llc"
    assert ev.normalize_value("identifier", "BWC 20-1") == ev.normalize_value("identifier", "bwc20-1")
    assert ev.normalize_value("boolean", 1) is True
    assert ev.normalize_value("date", "2026-03-14") == "2026-03-14"


# --------------------------------------------------------------------------------------------------------------
# Field matching
# --------------------------------------------------------------------------------------------------------------


def statement_packet(rows: list[tuple[date, str, float, int]]) -> LoanPacket:
    """A loan packet with an application (FEIN, revenue) and one statement with the given transactions."""
    application = LoanApplicationForm(
        doc_id="D01", fein=cited("00-1234567", 1), stated_annual_revenue=cited(500000.0, 1, line=2)
    )
    transactions = [
        Transaction(posted_date=cited(day, page_number, "D02"), deposit=cited(amount, page_number, "D02"))
        for day, _kind, amount, page_number in rows
    ]
    statement = BankStatement(doc_id="D02", transactions=transactions)
    return LoanPacket(packet_id="PKT-C-900", application=application, statements=[statement])


def deposit_rows(day: str, amount: float, page_number: int) -> list[SimpleNamespace]:
    """The truth rows of one deposit transaction (date and amount)."""
    key = f"{day}|D|{amount:.2f}"
    return [
        truth_row("statement.transactions.posted_date", day, page_number, key, "D02"),
        truth_row("statement.transactions.deposit", amount, page_number, key, "D02"),
    ]


def hand_truth() -> SimpleNamespace:
    """Truth for: application on page 1 (clean), statement on page 2 (scanned) with three deposits."""
    fields = [
        truth_row("application.fein", "00-1234567", 1),
        truth_row("application.stated_annual_revenue", 500000.0, 1),
        *deposit_rows("2026-03-02", 100.0, 2),
        *deposit_rows("2026-03-02", 100.0, 2),  # two identical deposits on the same day
        *deposit_rows("2026-03-09", 2500.0, 2),
    ]
    pages = [
        page(1, ["FEIN 00-1234567", "Stated annual revenue $500,000.00"]),
        page(2, ["03/02/2026 CASH 100.00", "03/02/2026 CASH 100.00", "03/09/2026 ACH 2,500.00"], scanned=True),
    ]
    return SimpleNamespace(fields=fields, pages=pages)


def counts_for(extraction: LoanPacket) -> Counter:
    """Field, row and citation counters of an extraction against hand_truth()."""
    truth = hand_truth()
    expected = ev.truth_rows(truth)
    found = ev.predicted_rows(extraction)
    match = ev.match_fields(expected, found)
    counts = ev.field_counts(match, ev.page_conditions(truth))
    counts.update(ev.row_counts(expected, found, match))
    counts.update(ev.citation_counts(match, {item.page_number: item.text_lines for item in truth.pages}))
    return counts


def test_perfect_extraction_scores_every_value() -> None:
    extraction = statement_packet(
        [(date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 9), "D", 2500.0, 2)]
    )
    counts = counts_for(extraction)
    assert (counts["fields.all.all.tp"], counts["fields.all.all.fp"], counts["fields.all.all.fn"]) == (8, 0, 0)
    assert counts["fields.clean.all.tp"] == 2 and counts["fields.scanned.all.tp"] == 6
    assert (counts["rows.transactions.tp"], counts["rows.transactions.exact"]) == (3, 3)
    assert counts["citations.page.all.correct"] == counts["citations.page.all.total"] == 8


def test_missed_and_extra_rows() -> None:
    # One of the two identical deposits is missed, and a deposit that is not in the truth is added.
    extraction = statement_packet(
        [(date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 9), "D", 2500.0, 2), (date(2026, 3, 20), "D", 75.0, 2)]
    )
    counts = counts_for(extraction)
    assert (counts["rows.transactions.tp"], counts["rows.transactions.fn"], counts["rows.transactions.fp"]) == (2, 1, 1)
    assert (counts["fields.all.all.tp"], counts["fields.all.all.fp"], counts["fields.all.all.fn"]) == (6, 2, 2)
    assert counts["fields.scanned.money.fn"] == 1 and counts["fields.scanned.money.fp"] == 1


def test_an_ocr_misread_amount_is_one_false_positive_and_one_false_negative() -> None:
    # 2,500.00 read as 2,800.00: the row key changes too, so the whole row is missed and an extra row appears.
    extraction = statement_packet(
        [(date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 9), "D", 2800.0, 2)]
    )
    counts = counts_for(extraction)
    assert (counts["fields.all.money.tp"], counts["fields.all.money.fp"], counts["fields.all.money.fn"]) == (3, 1, 1)
    assert (counts["rows.transactions.fp"], counts["rows.transactions.fn"]) == (1, 1)


def test_a_wrong_value_under_the_right_key_is_both_fp_and_fn_and_the_row_is_not_exact() -> None:
    truth_rows = [truth_row("application.fein", "00-1234567", 1)]
    found = [ev.Row("application.fein", None, "00-1234561", "D01", 1)]
    match = ev.match_fields([ev.Row(r.field, r.key, r.value, r.doc_id, r.page_number) for r in truth_rows], found)
    assert (len(match.pairs), len(match.missed), len(match.extra)) == (0, 1, 1)


def test_a_wrong_page_citation_counts_against_citation_accuracy_only() -> None:
    extraction = statement_packet(
        [(date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 2), "D", 100.0, 2), (date(2026, 3, 9), "D", 2500.0, 2)]
    )
    extraction.application.fein = cited("00-1234567", 2)  # right value, cited on the statement page
    counts = counts_for(extraction)
    assert counts["fields.all.all.tp"] == 8
    assert counts["citations.page.all.total"] == 8
    assert counts["citations.page.all.correct"] == 7
    assert counts["citations.page.identifier.same_page"] == 0
    # A false positive takes the condition of the page it cites: none here, every value is right.
    assert counts["fields.scanned.identifier.fp"] == 0


def test_value_on_page_matches_whole_tokens_and_printed_forms() -> None:
    assert ev.value_on_page("application.owners.country", "US", ["Country US"])
    assert not ev.value_on_page("application.owners.country", "US", ["BUSINESS INFORMATION"])
    assert ev.value_on_page("statement.period_start", "2026-03-14", ["Period 03/14/2026 to 04/13/2026"])
    assert ev.value_on_page("loss_run.valued_as_of", "2026-03-14", ["Valued as of 14-Mar-2026"])
    assert ev.value_on_page("statement.opening_balance", -1234.5, ["Opening balance -$1,234.50"])
    assert ev.value_on_page("application.product", "line_of_credit", ["Product Line of credit"])
    assert ev.value_on_page("application.note_rate_pct", 8.75, ["Note rate 8.75%"])
    assert ev.value_on_page("application.remarks", "two line remark", ["two line", "remark"])
    assert not ev.value_on_page("statement.opening_balance", 1234.5, ["Opening balance $1,234.56"])


# --------------------------------------------------------------------------------------------------------------
# Pages, documents and checklist
# --------------------------------------------------------------------------------------------------------------


def test_page_and_document_counts() -> None:
    truth = SimpleNamespace(
        pages=[
            page(1, []),
            page(2, [], scanned=True, doc_type="bank_statement"),
            page(3, [], doc_type="bank_statement"),
        ],
        documents=[
            SimpleNamespace(doc_type="loan_application", pages=[1]),
            SimpleNamespace(doc_type="bank_statement", pages=[2, 3]),
            SimpleNamespace(doc_type="photo_id", pages=[]),  # removed document: not counted
        ],
    )
    page_types = [
        SimpleNamespace(page_number=1, doc_type="loan_application"),
        SimpleNamespace(page_number=2, doc_type="profit_and_loss"),
        SimpleNamespace(page_number=3, doc_type="bank_statement"),
    ]
    documents = [
        DocumentInfo(
            doc_id="D01", doc_type="loan_application", pages=[1], ocr_used=False, classification_confidence=1.0
        ),
        DocumentInfo(doc_id="D02", doc_type="profit_and_loss", pages=[2], ocr_used=True, classification_confidence=0.5),
        DocumentInfo(doc_id="D03", doc_type="bank_statement", pages=[3], ocr_used=False, classification_confidence=1.0),
    ]
    result = SimpleNamespace(page_types=page_types, extraction=SimpleNamespace(documents=documents))
    counts = ev.page_counts(truth, result)
    assert (counts["pages.all.correct"], counts["pages.all.total"]) == (2, 3)
    assert (counts["pages.scanned.correct"], counts["pages.scanned.total"]) == (0, 1)
    assert (counts["documents.correct"], counts["documents.total"]) == (1, 2)


def test_checklist_counts_compare_missing_and_provided_codes() -> None:
    truth = SimpleNamespace(
        line="credit",
        missing_required_documents=["formation_documents"],
        application_as_documented={"documents_provided": ["signed_application", "debt_schedule"]},
    )
    check = SimpleNamespace(check_id="required_documents", values={"missing": ["formation_documents", "debt_schedule"]})
    counts = ev.checklist_counts(truth, SimpleNamespace(checks=[check]), ["signed_application"])
    assert counts["checklist.total"] == 11 and counts["checklist.correct"] == 10  # debt_schedule is wrong
    assert counts["checklist.packets_exact"] == 0
    assert counts["provided.total"] == 11 and counts["provided.correct"] == 10


# --------------------------------------------------------------------------------------------------------------
# Contradictions and injections
# --------------------------------------------------------------------------------------------------------------


def contradiction(
    number: int, kind: str, pages: list[int], fields: list[str], explanation: str = "benign"
) -> Contradiction:
    """A Contradiction whose evidence cites the given pages."""
    sides = [Evidence(doc_id="D99", page=item, line=1, field=fields[0], value=1.0) for item in pages]
    return Contradiction(
        contradiction_id=f"CT-{number:02d}",
        type=kind,
        severity="medium",
        check_id="check",
        issue="issue",
        entity="business",
        fields=fields,
        doc_ids=["D99"],
        sides=sides,
        side_a_count=1,
        explanation=explanation,
        explanation_code="code",
        explanation_detail="detail",
        guideline_refs=[],
        message="message",
    )


def problem(problem_id: str, kind: str, doc_ids: list[str], fields: list[str], classification: str) -> SimpleNamespace:
    """An InjectedProblem-like problem."""
    return SimpleNamespace(
        problem_id=problem_id, problem_type=kind, doc_ids=doc_ids, fields=fields, classification=classification
    )


def test_contradictions_match_by_type_and_document_or_field() -> None:
    page_docs = {1: "D01", 5: "D05", 9: "D09"}
    problems = [
        problem("PB-01", "revenue_gap", ["D01", "D05"], ["application.stated_annual_revenue"], "benign"),
        problem("PB-02", "prompt_injection", ["D09"], ["profit_and_loss"], "integrity"),
        problem("PB-03", "tiv_mismatch", ["D02"], ["application.stated_tiv"], "unsupported"),
        problem("PB-04", "missing_document", [], ["documents_provided"], "missing"),  # not scorable here
    ]
    found = [
        contradiction(1, "revenue_gap", [5], ["statement.total_deposits"], "unsupported"),  # shares D05
        contradiction(2, "prompt_injection", [3], ["profit_and_loss.notes"], "unknown"),  # field inside a path
        contradiction(3, "name_mismatch", [1], ["application.legal_name"]),  # nothing planted: false positive
        contradiction(4, "tiv_mismatch", [9], ["property_schedule.total_tiv"], "unknown"),  # no shared doc or field
    ]
    problem_outcomes, contradiction_outcomes = problems_eval.match_contradictions(problems, found, page_docs)
    assert [item["detected"] for item in problem_outcomes] == [True, True, False]
    assert [bool(item["matched_problem_ids"]) for item in contradiction_outcomes] == [True, True, False, False]
    counts = problems_eval.contradiction_counts(problem_outcomes, contradiction_outcomes)
    assert (counts["problems.all.detected"], counts["problems.all.total"]) == (2, 3)
    assert (counts["contradictions.all.tp"], counts["contradictions.all.total"]) == (2, 4)
    # Only the benign revenue gap is scored for benign-vs-unsupported (integrity is not), and it is wrong.
    assert (counts["explanations.all.correct"], counts["explanations.all.total"]) == (0, 1)


def problem_outcome(classification: str, explanation: str | None, **extra: object) -> dict:
    """A problem outcome as match_contradictions writes it (tiv_mismatch, one match when detected)."""
    detected = explanation is not None
    outcome = {
        "type": "tiv_mismatch",
        "classification": classification,
        "detected": detected,
        "detected_with_correct_evidence": detected,
        "matches": int(detected),
        "explanation": explanation,
        "explanation_code": "insufficient_evidence" if explanation == "unknown" else "x",
        "evidence_correct": True if detected else None,
        "designed_abstention": False,
    }
    outcome.update(extra)
    return outcome


def test_unknown_explanation_is_wrong_and_counted() -> None:
    outcomes = [
        problem_outcome("unsupported", "unknown", designed_abstention=True),
        problem_outcome("benign", "benign"),
        problem_outcome("benign", None),
        problem_outcome("unsupported", "benign", matches=3),
    ]
    counts = problems_eval.contradiction_counts(outcomes, [])
    assert (counts["explanations.all.correct"], counts["explanations.all.total"]) == (1, 3)
    assert counts["explanations.all.unknown"] == 1
    assert counts["explanations.unsupported.unknown"] == 1
    # The 2 x 3 confusion, the designed abstention and the duplicate matches are counted too.
    assert counts["explanations.confusion.unsupported.unknown"] == 1
    assert counts["explanations.confusion.unsupported.benign"] == 1
    assert counts["explanations.confusion.benign.benign"] == 1
    assert (counts["explanations.designed_abstention.total"], counts["explanations.decidable.total"]) == (1, 2)
    assert counts["explanations.unknown_code.insufficient_evidence"] == 1
    assert counts["problems.all.duplicate_matches"] == 2


def injection_result(hits: list[tuple[str | None, int | None]], checks: list[object] | None = None) -> SimpleNamespace:
    """A result with page-scan hits (doc_id, page), optional checks, and one document D04 on pages 4-5."""
    documents = [
        DocumentInfo(doc_id="D04", doc_type="cover_email", pages=[4, 5], ocr_used=False, classification_confidence=1.0)
    ]
    return SimpleNamespace(
        injection_hits=[SimpleNamespace(doc_id=doc_id, page=number) for doc_id, number in hits],
        checks=checks or [],
        extraction=SimpleNamespace(documents=documents),
    )


def embedded_instructions_check(places: list[tuple[str, int | None, str]]) -> SimpleNamespace:
    """An embedded_instructions check result with one issue per (doc_id, page, field) hit."""
    issues = [
        SimpleNamespace(side_a=[Evidence(doc_id=doc_id, page=number, line=1, field=field, value="x")])
        for doc_id, number, field in places
    ]
    return SimpleNamespace(check_id="embedded_instructions", issues=issues)


def test_injection_counts_recall_and_false_hits() -> None:
    truth = SimpleNamespace(injections=[SimpleNamespace(page_number=4, technique="role_spoof")])
    counts, outcomes = problems_eval.injection_counts(truth, injection_result([("D04", 4), ("D07", 7)]))
    assert outcomes == [{"page": 4, "technique": "role_spoof", "detected": True}]
    assert (counts["injection_hits.false"], counts["injection_packets.with_false_hit"]) == (1, 1)
    # A hit without a page takes its document's first page.
    counts, _ = problems_eval.injection_counts(truth, injection_result([("D04", None)]))
    assert counts["injections.detected"] == 1 and counts["injection_hits.false"] == 0


def test_field_hits_reported_by_the_check_count_as_detections() -> None:
    # The page scan found nothing; the free-text field scan (inside the embedded_instructions check) found the
    # planted remark on page 4 and also flagged page 5, which has no planted instruction.
    truth = SimpleNamespace(injections=[SimpleNamespace(page_number=4, technique="role_spoof")])
    check = embedded_instructions_check([("D04", 4, "cover_email.body"), ("D04", 5, "cover_email.body")])
    counts, outcomes = problems_eval.injection_counts(truth, injection_result([], [check]))
    assert outcomes == [{"page": 4, "technique": "role_spoof", "detected": True}]
    assert (counts["injection_hits.total"], counts["injection_hits.false"]) == (2, 1)
    # When the check exists, its issues are the reported hits (it already holds the page-scan hits).
    counts, _ = problems_eval.injection_counts(truth, injection_result([("D04", 4)], [embedded_instructions_check([])]))
    assert counts["injections.detected"] == 0 and counts["injection_hits.total"] == 0


def test_a_clean_packet_with_a_hit_is_a_false_positive_packet() -> None:
    truth = SimpleNamespace(injections=[])
    counts, outcomes = problems_eval.injection_counts(truth, injection_result([("D04", 5)]))
    assert outcomes == []
    assert (counts["injection_packets.clean"], counts["injection_packets.clean_with_hit"]) == (1, 1)


# --------------------------------------------------------------------------------------------------------------
# Summary and bootstrap
# --------------------------------------------------------------------------------------------------------------


def record(packet_id: str, counts: dict[str, int], scanned: int = 0, line: str = "credit") -> dict:
    """A minimal evaluation record."""
    return {"packet_id": packet_id, "line": line, "n_scanned_pages": scanned, "counts": counts, "injections": []}


def test_prf_from_summed_counts() -> None:
    counts = {"x.tp": 8, "x.fp": 2, "x.fn": 0}
    assert summary.precision_of(counts, "x") == 0.8
    assert summary.recall_of(counts, "x") == 1.0
    assert summary.f1_of(counts, "x") == pytest.approx(16 / 18)
    assert summary.f1_of({}, "x") is None


def test_bootstrap_is_deterministic_and_brackets_the_value() -> None:
    records = [record(f"P{i}", {"a.tp": i % 3, "a.fp": 1, "a.fn": i % 2}) for i in range(20)]
    first = summary.prf_metric(records, "a")
    second = summary.prf_metric(records, "a")
    assert first == second
    f1 = first["f1"]
    assert f1["ci_low"] <= f1["value"] <= f1["ci_high"]
    assert f1["n_packets"] == 20 and f1["n_resamples_used"] == 1000
    assert (first["tp"], first["fp"], first["fn"]) == (19, 20, 10)


def test_ratio_metric_with_no_denominator_is_none() -> None:
    metric = summary.ratio_metric([record("P1", {"k.n": 0})], "k.n", "k.d")
    assert metric["value"] is None and metric["ci_low"] is None and metric["denominator"] == 0


def test_paired_difference_uses_the_same_packets() -> None:
    better = [record(f"P{i}", {"f.tp": 10, "f.fp": 0, "f.fn": 0}) for i in range(5)]
    worse = [record(f"P{i}", {"f.tp": 5, "f.fp": 5, "f.fn": 5}) for i in range(5)]
    difference = summary.paired_f1_difference(better, worse, "f", "f")
    assert difference["value"] == pytest.approx(0.5)
    assert difference["ci_low"] == difference["ci_high"] == pytest.approx(0.5)


def test_contradiction_precision_is_split_by_packets_with_scanned_pages() -> None:
    records = [
        record("P1", {"contradictions.all.tp": 1, "contradictions.all.total": 1}, scanned=0),
        record("P2", {"contradictions.all.tp": 0, "contradictions.all.total": 2}, scanned=3),
    ]
    split = summary.contradiction_metrics(records)["precision_by_packet_condition"]
    assert split["packets_without_scanned_pages"]["value"] == 1.0
    assert split["packets_with_scanned_pages"]["value"] == 0.0
