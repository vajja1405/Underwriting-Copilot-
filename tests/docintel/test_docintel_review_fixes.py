"""Tests for the Phase 2 review fixes (verify-and-fix role): each test pins down one reviewed defect.

Hand-built pages and extractions only (no network, no model, no evaluation packets). The findings they cover:
OCR tokens glued into one number, the lost minus of a header balance, the column-vs-balance side of a row, the
order of a closed claim's amounts, the "observed monthly" MCA figure and the NBC-4.4 window, loss runs counted as
provided when a carrier's run is missing, values filled in silently by to_application, and OCR misreads turned
into unsupported or tampering explanations.
"""

from datetime import date, timedelta

from docintel_check_fixtures import (
    credit_packet_data,
    insurance_submission_data,
    loan,
    set_value,
    submission,
)

from uwcopilot.docintel.check_models import CheckIssue, Evidence
from uwcopilot.docintel.checks import results_by_id, run_checks
from uwcopilot.docintel.classify import Segment
from uwcopilot.docintel.contradiction_ocr import LOW_CONFIDENCE, review
from uwcopilot.docintel.contradiction_rules import Verdict
from uwcopilot.docintel.contradictions import build_contradictions
from uwcopilot.docintel.deposits import average_monthly_mca_remittance, last_period_end, new_loans_last_90_days
from uwcopilot.docintel.layout import PageText, Word, make_line
from uwcopilot.docintel.parsing import parse_int
from uwcopilot.docintel.rules_common import document_lines, find_value
from uwcopilot.docintel.rules_loss_runs import is_title_word, order_by_arithmetic
from uwcopilot.docintel.rules_statements import extract_statement
from uwcopilot.docintel.schemas import BankStatement, Cited, Transaction
from uwcopilot.docintel.to_application import to_application

Cell = tuple[str, float, float] | tuple[str, float, float, float]


def page_of(rows: list[list[Cell]], source: str = "ocr", number: int = 1) -> PageText:
    """A page whose rows hold words at (text, x0, x1[, conf]) positions, 13 points apart."""
    lines = []
    for index, cells in enumerate(rows, start=1):
        top = 50.0 + 13.0 * index
        words = []
        for cell in cells:
            conf = cell[3] if len(cell) == 4 else 0.95
            words.append(Word(text=cell[0], x0=cell[1], top=top, x1=cell[2], bottom=top + 9.0, conf=conf))
        lines.append(make_line(index, words))
    return PageText(page_number=number, width=612.0, height=792.0, source=source, lines=lines)


def one_document(page: PageText, doc_type: str = "bank_statement") -> Segment:
    """A one-page document segment D02."""
    return Segment(doc_id="D02", doc_type=doc_type, pages=[page], confidences=[1.0])


# --- parsing: separate OCR tokens are not one number (finding: clean_number_text glued "4 1" into 41) ----------


def test_a_speck_before_a_value_does_not_join_it_and_the_confident_word_wins() -> None:
    page = page_of(
        [[("Number", 54, 90), ("of", 92, 100), ("claims", 102, 130), ("4", 230, 236, 0.33), ("1", 240, 246, 0.97)]]
    )
    refs = document_lines(one_document(page, "loss_run"))
    assert parse_int("4 1") is None
    assert find_value(refs, ("Number of claims",), parse_int, "D02").value == 1


def test_on_the_text_layer_the_first_word_still_wins_a_tie() -> None:
    page = page_of([[("Term", 54, 80), ("60", 230, 240, 1.0), ("12", 250, 260, 1.0)]], source="text_layer")
    assert find_value(document_lines(one_document(page)), ("Term",), parse_int, "D02").value == 60


# --- statements: header sign and row side (findings: lost minus on "Closing balance", column beats balance) -----


def statement_rows(closing_text: str, rows: list[list[Cell]]) -> PageText:
    """A statement page with a header, the given rows and the closing balance as printed (OCR page)."""
    header = [
        [("Larkspur", 54, 100), ("Valley", 102, 130), ("Bank", 132, 160)],
        [
            ("Statement", 54, 97),
            ("period", 100, 127),
            ("2025-09-01", 230, 276),
            ("to", 278, 286),
            ("2025-09-30", 288, 334),
        ],
        [("Opening", 54, 90), ("balance", 93, 126), ("$1,000.00", 230, 270)],
        [("Closing", 54, 90), ("balance", 93, 126), (closing_text, 230, 280)],
        [
            ("Date", 54, 73),
            ("Description", 122, 171),
            ("Withdrawals", 367, 420),
            ("Deposits", 452, 490),
            ("Balance", 523, 558),
        ],
    ]
    return page_of(header + rows)


def test_a_header_closing_balance_gets_its_minus_back_when_the_rows_prove_it() -> None:
    rows = [[("09/03/2025", 54, 96), ("PAYROLL", 122, 160), ("2,295.52", 382, 420), ("-1,295.52", 515, 558)]]
    statement = extract_statement(one_document(statement_rows('"$1,295.52', rows)))
    assert statement.closing_balance.value == -1295.52  # OCR read the minus as '"', which was stripped


def test_an_unclear_sign_without_arithmetic_evidence_is_left_out() -> None:
    statement = extract_statement(one_document(statement_rows('"$1,295.52', [])))
    assert not statement.closing_balance.found  # '"' right before "$": the sign is unknown, so it is not guessed
    plain = extract_statement(one_document(statement_rows("$1,295.52", [])))
    assert plain.closing_balance.value == 1295.52


def test_the_running_balance_beats_a_widened_ocr_box_for_the_row_side() -> None:
    # The amount's box (x1=461) sits nearer the Deposits title (490) than Withdrawals (420), but the balance
    # goes 1,000.00 -> 700.00: it is a withdrawal.
    rows = [
        [("09/03/2025", 54, 96), ("ACH", 122, 140), ("DEBIT", 142, 170), ("300.00", 382, 461), ("700.00", 515, 558)]
    ]
    statement = extract_statement(one_document(statement_rows("$700.00", rows)))
    row = statement.transactions[0]
    assert row.withdrawal.value == 300.0 and not row.deposit.found


# --- loss runs: a closed claim's amounts (finding: paid became 0 and reserve the paid amount) -------------------


def amount_words(texts: list[str]) -> list[Word]:
    """Words for amounts, left to right."""
    return [Word(text=text, x0=100.0 * i, top=0, x1=100.0 * i + 50, bottom=9) for i, text in enumerate(texts)]


def test_a_closed_claim_keeps_paid_equal_to_incurred_without_a_header() -> None:
    assert order_by_arithmetic(amount_words(["10,258.98", "0.00", "10,258.98"])) == ["paid", "reserve", "incurred"]
    assert order_by_arithmetic(amount_words(["10,258.98", "10,258.98", "0.00"])) == ["paid", "incurred", "reserve"]
    assert order_by_arithmetic(amount_words(["0.00", "500.00", "500.00"]), "Open") == ["paid", "reserve", "incurred"]
    assert order_by_arithmetic(amount_words(["150.00", "100.00", "50.00"])) == ["incurred", "paid", "reserve"]


def test_a_header_word_glued_by_ocr_still_names_its_column() -> None:
    assert is_title_word("paidloss", "paid") and is_title_word("osreserve", "reserve")
    assert not is_title_word("id", "paid")


# --- deposits: observed monthly figures and the stacking window (findings: MCA diluted, window anchored) --------


def month_statement(month: int, rows: list[tuple[date, str, float, str]]) -> BankStatement:
    """A statement of 2026-<month> with (date, description, amount, "deposit"/"withdrawal") rows."""
    start = date(2026, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    transactions = []
    for posted, description, amount, side in rows:
        transaction = Transaction(posted_date=Cited(value=posted), description=Cited(value=description))
        setattr(transaction, side, Cited(value=amount))
        transactions.append(transaction)
    return BankStatement(
        doc_id=f"D{month:02d}", period_start=Cited(value=start), period_end=Cited(value=end), transactions=transactions
    )


def test_an_mca_is_averaged_over_the_months_it_is_remitted() -> None:
    statements = [month_statement(month, []) for month in range(1, 10)]
    statements += [
        month_statement(month, [(date(2026, month, 5), "MCA REMIT RAPIDLEAF", 33_134.06, "withdrawal")])
        for month in (10, 11, 12)
    ]
    assert average_monthly_mca_remittance(statements) == 33_134.06  # not 33,134.06 x 3 / 12


def test_the_stacking_window_is_the_last_90_days_of_statements() -> None:
    statements = [month_statement(month, []) for month in range(1, 9)]
    statements.append(month_statement(9, [(date(2026, 7, 3), "LOAN PROCEEDS EXAMPLE LENDER", 50_000.0, "deposit")]))
    statements.append(month_statement(10, [(date(2026, 7, 2), "MCA FUNDING RAPIDLEAF", 40_000.0, "deposit")]))
    as_of = last_period_end(statements)
    assert as_of == date(2026, 10, 31)
    # 90 days ending 2026-10-31 start on 2026-08-03: neither July funding is inside.
    assert new_loans_last_90_days(statements, as_of) == 0
    assert new_loans_last_90_days(statements, date(2026, 9, 30)) == 1  # 2026-07-03 is the 90th day; 07-02 is not


# --- required documents: a missing carrier loss run (finding: the check said "all documents are in the packet") --


def test_a_missing_prior_carrier_loss_run_fails_required_documents_and_to_application_agrees() -> None:
    data = insurance_submission_data()
    complete = results_by_id(run_checks(submission(data)))["required_documents"]
    assert "loss_runs" in complete.values["provided"]
    data["loss_runs"] = data["loss_runs"][:1]
    reduced = submission(data)
    check = results_by_id(run_checks(reduced))["required_documents"]
    assert check.status == "fail" and "loss_runs" in check.values["missing"]
    application, _, _ = to_application(reduced)
    assert "loss_runs" not in application["documents_provided"]


# --- to_application: nothing is filled in silently (finding: 0.0 / False with no unknowns entry) -----------------


def test_unread_debt_values_and_a_missing_schedule_are_listed_as_unknown() -> None:
    data = credit_packet_data()
    extraction = loan(data)
    _, _, unknowns = to_application(extraction)
    assert not any(path.startswith("debt.existing") for path in unknowns)
    assert extraction.debt_schedule is not None and extraction.debt_schedule.items
    extraction.debt_schedule.items[0].monthly_payment = Cited()
    _, _, unknowns = to_application(extraction)
    assert "debt.existing_monthly_payment_schedule" in unknowns
    extraction.debt_schedule = None
    application, _, unknowns = to_application(extraction)
    assert application["debt"]["existing_monthly_payment_schedule"] == 0.0
    assert {"debt.existing_monthly_payment_schedule", "debt.existing_total_debt_balance"} <= set(unknowns)


def test_a_principal_without_a_credit_report_row_is_unknown_not_clean() -> None:
    extraction = loan(credit_packet_data())
    extraction.credit_report = None
    application, _, unknowns = to_application(extraction)
    principals = [
        index
        for index, owner in enumerate(application["owners"])
        if owner["entity_type"] == "person" and (owner["ownership_pct"] >= 20 or owner["is_control_person"])
    ]
    assert principals
    for index in principals:
        assert application["owners"][index]["credit_score"] is None
        assert application["owners"][index]["bankruptcy_within_7_years"] is False
        assert {f"owners[{index}].credit_score", f"owners[{index}].bankruptcy_within_7_years"} <= set(unknowns)


def test_an_unread_control_person_cell_and_a_month_that_does_not_add_up_are_unknown() -> None:
    data = credit_packet_data()
    data["application"]["owners"][0]["is_control_person"] = {"value": None}
    first = data["statements"][0]
    set_value(first["total_deposits"], float(first["total_deposits"]["value"]) + 500.0)
    _, _, unknowns = to_application(loan(data))
    assert "owners[0].is_control_person" in unknowns
    assert "bank_statements.months[0]" in unknowns


def test_a_loss_run_whose_summary_disagrees_with_its_rows_marks_its_years_unknown() -> None:
    data = insurance_submission_data()
    summary = data["loss_runs"][0]["summary"]
    set_value(summary["claim_count"], int(summary["claim_count"]["value"]) + 1)
    _, _, unknowns = to_application(submission(data))
    assert any(path.startswith("loss_runs.years[") for path in unknowns)


# --- explanations: a claim missing from the lines, and the OCR review (findings: by-design unknown, OCR misreads) --


def test_a_summary_that_counts_a_claim_the_lines_leave_out_is_unsupported() -> None:
    data = insurance_submission_data()
    run = data["loss_runs"][0]
    lines_incurred = sum(float(claim["incurred"]["value"]) for claim in run["claims"])
    set_value(run["summary"]["claim_count"], len(run["claims"]) + 1)
    set_value(run["summary"]["total_incurred"], lines_incurred + 9_000.0)
    set_value(run["summary"]["total_paid"], float(run["summary"]["total_paid"]["value"]) + 9_000.0)
    extraction = submission(data)
    report = build_contradictions(extraction, run_checks(extraction))
    totals = [item for item in report.contradictions if item.type == "loss_run_total_mismatch"]
    assert [(item.explanation, item.explanation_code) for item in totals] == [
        ("unsupported", "claim_missing_from_lines")
    ]


def evidence(doc_id: str, value: float, confidence: float, field: str = "application.stated_tiv") -> Evidence:
    """One side value."""
    return Evidence(doc_id=doc_id, page=1, line=3, field=field, value=value, confidence=confidence)


def tiv_issue(confidence: float) -> CheckIssue:
    """An application-vs-schedule TIV issue whose schedule value has the given confidence."""
    return CheckIssue(
        issue="tiv_differs",
        entity="business",
        fields=["application.stated_tiv", "property_schedule.total_tiv"],
        side_a=[evidence("D01", 3_000_000.0, 1.0)],
        side_b=[evidence("D06", 4_200_000.0, confidence, "property_schedule.total_tiv")],
        message="TIV differs.",
    )


def test_a_low_confidence_ocr_value_turns_an_unsupported_verdict_into_a_possible_misread() -> None:
    data = insurance_submission_data()
    for info in data["documents"]:
        info["ocr_used"] = info["doc_id"] == "D06"
    extraction = submission(data)
    verdict = Verdict("unsupported", "no_supporting_document", "Differs by 40%.")
    reviewed, severity = review("tiv_consistency", tiv_issue(LOW_CONFIDENCE - 0.2), verdict, "high", extraction)
    assert (reviewed.explanation, reviewed.code, severity) == ("unknown", "possible_ocr_misread", "medium")
    assert "Without it, the rule would say unsupported" in reviewed.detail
    kept, severity = review("tiv_consistency", tiv_issue(0.97), verdict, "high", extraction)
    assert (kept, severity) == (verdict, "high")
    # The same low confidence on a document that was not read by OCR changes nothing.
    data["documents"] = [dict(info, ocr_used=False) for info in data["documents"]]
    unchanged, _ = review("tiv_consistency", tiv_issue(0.1), verdict, "high", submission(data))
    assert unchanged == verdict


def test_an_owner_missing_from_an_ocr_table_that_does_not_add_up_is_a_possible_misread() -> None:
    data = credit_packet_data()
    for info in data["documents"]:
        info["ocr_used"] = info["doc_id"] == "D01"
    data["application"]["owners"] = data["application"]["owners"][:1]  # OCR lost the other rows
    extraction = loan(data)
    owners = [item for item in build_contradictions(extraction, run_checks(extraction)).contradictions]
    owners = [item for item in owners if item.type == "owner_mismatch" and item.issue == "missing_from_application"]
    assert owners and all(item.explanation_code == "possible_ocr_misread" for item in owners)


def test_an_expired_photo_id_does_not_count_for_principal_photo_ids() -> None:
    data = credit_packet_data()
    extraction = loan(data)
    assert "principal_photo_ids" in results_by_id(run_checks(extraction))["required_documents"].values["provided"]
    applied = extraction.application.application_date.value
    for photo_id in extraction.photo_ids:
        photo_id.expiry_date = Cited(value=applied - timedelta(days=1))
    check = results_by_id(run_checks(extraction))["required_documents"]
    assert "principal_photo_ids" in check.values["missing"]
    assert check.values["expired_photo_ids"] == [item.doc_id for item in extraction.photo_ids]


def test_an_owner_name_with_an_ocr_word_added_is_a_possible_misread_not_a_missing_owner() -> None:
    data = credit_packet_data()
    for info in data["documents"]:
        info["ocr_used"] = info["doc_id"] == data["application"]["doc_id"]
    first = data["application"]["owners"][0]["name"]
    set_value(first, f"{first['value']} Ros")  # a speck read as a word at the end of the name
    extraction = loan(data)
    report = build_contradictions(extraction, run_checks(extraction))
    owners = [item for item in report.contradictions if item.type == "owner_mismatch"]
    assert owners and all(item.explanation_code == "possible_ocr_misread" for item in owners)
    # The same names on text-layer documents are a real disagreement.
    for info in data["documents"]:
        info["ocr_used"] = False
    plain = loan(data)
    owners = [
        item for item in build_contradictions(plain, run_checks(plain)).contradictions if item.type == "owner_mismatch"
    ]
    assert owners and not any(item.explanation_code == "possible_ocr_misread" for item in owners)


def test_ocr_misread_names_still_find_their_loss_run_and_photo_id() -> None:
    from uwcopilot.docintel.checks_credit import id_names_match
    from uwcopilot.docintel.checks_loss_runs import has_loss_run

    runs = {"quillfeather mutual insurance co", "ostrander bay assurance"}
    assert has_loss_run("quilifeather mutual insurance co", runs)  # one misread letter
    assert has_loss_run("ostrander bay assurance sis", runs)  # an OCR word at the end
    assert not has_loss_run("thistlecombe fire and casualty", runs)  # a carrier really missing
    assert id_names_match("melissa palmer", "melissa paimer") and id_names_match("connor hall", "connor hall 7")
    assert not id_names_match("melissa palmer", "steven terry")
