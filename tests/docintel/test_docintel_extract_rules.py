"""Tests for the rules extractor (extract_rules.py and its readers rules_*.py).

Field accuracy is measured on four rendered DEV packets against the generator's truth.fields: two packets with a
text layer only (the floor is high: the text layer is exact) and two with one scanned page each (OCR; skipped when
tesseract is missing). The other tests build small pages by hand to pin down each parsing rule.
"""

from datetime import date

import pytest
from docintel_pipeline_fixtures import (
    SCANNED_PACKETS,
    TEXT_ONLY_PACKETS,
    built_packet,
    f1_score,
    field_counts,
    pipeline_result,
    tesseract_available,
)

from uwcopilot.docintel.classify import Segment
from uwcopilot.docintel.extract_rules import extract_with_rules
from uwcopilot.docintel.layout import PageText, Word, make_line
from uwcopilot.docintel.parsing import parse_amount, parse_int
from uwcopilot.docintel.rules_common import LineRef, document_lines, find_value, trim_noise
from uwcopilot.docintel.rules_loss_runs import extract_loss_run
from uwcopilot.docintel.rules_schedule import parse_location
from uwcopilot.docintel.rules_statements import extract_statement
from uwcopilot.docintel.rules_tables import owner_rows, parse_owner_row
from uwcopilot.docintel.schemas import flatten_fields

TEXT_LAYER_F1_FLOOR = 0.99
SCANNED_PAGE_F1_FLOOR = 0.85
SCANNED_PACKET_F1_FLOOR = 0.97


Cell = tuple[str, float, float]


def make_page(rows: list[list[Cell]], source: str = "text_layer", conf: float = 1.0, number: int = 1) -> PageText:
    """A page whose rows hold words at given (text, x0, x1) positions, 13 points apart."""
    lines = []
    for index, cells in enumerate(rows, start=1):
        top = 50.0 + 13.0 * index
        words = [Word(text=text, x0=x0, top=top, x1=x1, bottom=top + 9.0, conf=conf) for text, x0, x1 in cells]
        lines.append(make_line(index, words))
    return PageText(page_number=number, width=612.0, height=792.0, source=source, lines=lines)


def text_row(text: str, x0: float = 54.0, step: float = 30.0) -> list[Cell]:
    """Words of a text placed left to right from x0."""
    return [(token, x0 + step * index, x0 + step * index + 25.0) for index, token in enumerate(text.split())]


def segment(doc_type: str, pages: list[PageText], doc_id: str = "D02") -> Segment:
    """A one-document segment."""
    return Segment(doc_id=doc_id, doc_type=doc_type, pages=pages, confidences=[1.0] * len(pages))


@pytest.mark.parametrize(("packet_id", "line"), TEXT_ONLY_PACKETS)
def test_field_accuracy_on_text_layer_packets(packet_id: str, line: str) -> None:
    _, truth = built_packet(packet_id, line)
    result = pipeline_result(packet_id, line)
    assert result.ocr_pages == []
    assert f1_score(field_counts(result.extraction, truth)) >= TEXT_LAYER_F1_FLOOR


@pytest.mark.skipif(not tesseract_available(), reason="tesseract is not installed (apt package tesseract-ocr)")
@pytest.mark.parametrize(("packet_id", "line"), SCANNED_PACKETS)
def test_field_accuracy_on_packets_with_a_scanned_page(packet_id: str, line: str) -> None:
    _, truth = built_packet(packet_id, line)
    result = pipeline_result(packet_id, line)
    scanned = {page.page_number for page in truth.pages if page.scanned}
    assert set(result.ocr_pages) == scanned
    assert f1_score(field_counts(result.extraction, truth, scanned)) >= SCANNED_PAGE_F1_FLOOR
    assert f1_score(field_counts(result.extraction, truth)) >= SCANNED_PACKET_F1_FLOOR


@pytest.mark.parametrize(("packet_id", "line"), TEXT_ONLY_PACKETS)
def test_citations_point_into_their_documents_with_text_layer_confidence(packet_id: str, line: str) -> None:
    result = pipeline_result(packet_id, line)
    pages_of = {item.doc_id: set(item.pages) for item in result.extraction.documents}
    rows = flatten_fields(result.extraction)
    assert rows
    for row in rows:
        assert row.page in pages_of[row.doc_id]
        assert row.line is not None and row.line >= 1
        assert row.confidence == 1.0


def statement_page(layout: int, rows: list[list[Cell]], with_header: bool = True) -> PageText:
    """A bank statement page: letterhead, fields, optional table header, then the given rows."""
    first, second = ("Withdrawals", "Deposits") if layout == 0 else ("Deposits", "Withdrawals")
    lines = [
        text_row("Larkspur Valley Bank"),
        [("Account", 54, 90), ("number", 92, 125), ("99379121974", 230, 285)],
        [
            ("Statement", 54, 97),
            ("period", 100, 127),
            ("2025-09-01", 230, 276),
            ("to", 278, 286),
            ("2025-09-30", 288, 334),
        ],
        [("Opening", 54, 90), ("balance", 93, 126), ("$1,000.00", 230, 270)],
    ]
    if with_header:
        lines.append(
            [("Date", 54, 73), ("Description", 122, 171), (first, 367, 420), (second, 452, 490), ("Balance", 523, 558)]
        )
    return make_page(lines + rows)


def test_statement_rows_use_the_header_columns_in_both_layouts() -> None:
    withdrawal_x, deposit_x = (382.0, 420.0), (452.0, 490.0)
    rows = [
        [("09/02/2025", 54, 96), ("CASH", 122, 140), ("DEPOSIT", 142, 180), ("500.00", *deposit_x)],
        [("09/03/2025", 54, 96), ("ACH", 122, 140), ("PMT", 142, 160), ("200.00", *withdrawal_x)],
        text_row("Memo: a free-text note that is not a row", x0=130.0),
    ]
    rows[0].append(("1,500.00", 515, 558))
    rows[1].append(("1,300.00", 515, 558))
    statement = extract_statement(segment("bank_statement", [statement_page(0, rows)]))
    assert statement.bank_name.value == "Larkspur Valley Bank"
    assert statement.account_number.value == "99379121974"
    assert (statement.period_start.value, statement.period_end.value) == (date(2025, 9, 1), date(2025, 9, 30))
    assert len(statement.transactions) == 2
    deposit, withdrawal = statement.transactions
    assert deposit.deposit.value == 500.0 and not deposit.withdrawal.found
    assert deposit.description.value == "CASH DEPOSIT"
    assert withdrawal.withdrawal.value == 200.0 and not withdrawal.deposit.found
    # Layout 1 swaps the columns: the same x positions now mean the opposite (with balances that agree).
    swapped_rows = [list(row) for row in rows]
    swapped_rows[0][-1] = ("500.00", 515, 558)
    swapped_rows[1][-1] = ("700.00", 515, 558)
    swapped = extract_statement(segment("bank_statement", [statement_page(1, swapped_rows)]))
    assert swapped.transactions[0].withdrawal.value == 500.0
    assert swapped.transactions[1].deposit.value == 200.0


def test_without_a_header_the_running_balance_decides_and_a_lost_minus_is_restored() -> None:
    rows = [
        [("09/02/2025", 54, 96), ("PAYROLL", 122, 160), ("1,600.00", 382, 420), ("600.00", 515, 558)],
        [("09/03/2025", 54, 96), ("REFUND", 122, 160), ("100.00", 452, 490), ("500.00", 515, 558)],
        [("09/04/2025", 54, 96), ("UNKNOWN", 122, 160), ("50.00", 452, 490), ("999.00", 515, 558)],
    ]
    statement = extract_statement(segment("bank_statement", [statement_page(0, rows, with_header=False)]))
    first, second, third = statement.transactions
    assert first.withdrawal.value == 1600.0
    assert first.balance.value == -600.0  # 1,000 - 1,600: OCR lost the minus of "-600.00"
    assert second.deposit.value == 100.0  # -600 + 100 = -500, again printed "-500.00"
    assert second.balance.value == -500.0
    assert not third.deposit.found and not third.withdrawal.found  # the arithmetic does not decide: left out
    assert third.balance.value == 999.0


def test_a_row_date_with_an_unreadable_month_takes_the_statement_month() -> None:
    rows = [
        [("44/03/2025", 54, 96), ("CHECK", 122, 150), ("10.00", 382, 420), ("990.00", 515, 558)],
        [("44/99/2025", 54, 96), ("CHECK", 122, 150), ("10.00", 382, 420), ("980.00", 515, 558)],
    ]
    statement = extract_statement(segment("bank_statement", [statement_page(0, rows)]))
    assert [row.posted_date.value for row in statement.transactions] == [date(2025, 9, 3)]


def test_owner_rows_split_name_type_title_share_control_and_country() -> None:
    page = make_page([text_row("Harbor Holdings LLC Organization Member 40.0% No US")])
    owner = parse_owner_row(LineRef(page, page.lines[0]), "D01")
    assert owner is not None
    assert owner.name.value == "Harbor Holdings LLC"
    assert owner.entity_type.value == "organization"
    assert owner.title.value == "Member"
    assert owner.ownership_pct.value == 40.0
    assert owner.is_control_person.value is False
    assert owner.country.value == "US"
    not_a_row = make_page([text_row("Section E - Remarks")])
    assert parse_owner_row(LineRef(not_a_row, not_a_row.lines[0]), "D01") is None


def test_a_surname_close_to_person_is_not_the_type_word() -> None:
    header = [("Name", 54, 80), ("Type", 205, 225), ("Title", 275, 295), ("Ownership", 380, 420)]
    row = [
        ("Jon", 54, 70),
        ("Peterson", 72, 110),
        ("Person", 205, 232),
        ("Managing", 275, 310),
        ("Member", 312, 345),
        ("50.0%", 410, 435),
        ("Yes", 445, 460),
        ("US", 520, 532),
    ]
    for source in ("text_layer", "ocr"):
        page = make_page([header, row], source=source)
        owners = owner_rows([LineRef(page, page.lines[0])], [LineRef(page, page.lines[1])], "D01")
        assert owners[0].name.value == "Jon Peterson"
        assert owners[0].title.value == "Managing Member"
    ocr_page = make_page([row[:2] + [("Persen", 205, 232)] + row[3:]], source="ocr")
    misread = parse_owner_row(LineRef(ocr_page, ocr_page.lines[0]), "D01", type_x=205.0)
    assert misread is not None and misread.entity_type.value == "person"


def test_the_longest_label_synonym_wins_and_ocr_labels_match_approximately() -> None:
    page = make_page([text_row("Paid to date $91,878.81"), text_row("Paid $5.00")])
    refs = document_lines(segment("loss_run", [page]))
    assert find_value(refs, ("Paid", "Paid to date"), parse_amount, "D02").value == 91878.81
    ocr_page = make_page([text_row("Numbr of claims 3")], source="ocr", conf=0.9)
    cited = find_value(document_lines(segment("loss_run", [ocr_page])), ("Number of claims",), parse_int, "D02")
    assert cited.value == 3
    assert cited.confidence == 0.9


def test_trailing_ocr_noise_is_trimmed_but_the_last_word_is_kept() -> None:
    page = make_page([[("Larkspur", 54, 90), ("Bank", 92, 110), ("elec", 300, 320)]], source="ocr")
    words = page.lines[0].words
    noisy = [words[0], words[1], words[2].model_copy(update={"conf": 0.1})]
    assert [word.text for word in trim_noise(noisy)] == ["Larkspur", "Bank"]
    lonely = [words[2].model_copy(update={"conf": 0.1})]
    assert trim_noise(lonely) == lonely


def loss_run_page(header: list[str], rows: list[str]) -> PageText:
    """A loss run page with a claim table."""
    lines = [
        text_row("Bramblewood Casualty Company"),
        text_row("Carrier-produced loss run - fictional carrier - SYNTHETIC, for demonstration only"),
        text_row("Insured Coastal Group LLC"),
        text_row("Policy number BWC-8245751"),
        text_row("Valued as of 2026-08-08"),
        text_row(" ".join(header)),
        *[text_row(row) for row in rows],
        text_row("Loss summary"),
        text_row("Claims 2"),
        text_row("Incurred $45,771.88"),
    ]
    return make_page(lines)


def test_loss_run_claim_amounts_follow_the_header_order() -> None:
    header = ["Loss", "Date", "Claim", "No", "Line", "Status", "Incurred", "O/S", "Reserve", "Paid"]
    rows = ["2024-08-20 BWC23-93574 Property Open 5,108.86 3,186.92 1,921.94"]
    run = extract_loss_run(segment("loss_run", [loss_run_page(header, rows)]))
    assert run.carrier.value == "Bramblewood Casualty Company"
    assert run.carrier_produced.value is True
    assert run.policy_number.value == "BWC-8245751"
    claim = run.claims[0]
    assert (claim.paid.value, claim.reserve.value, claim.incurred.value) == (1921.94, 3186.92, 5108.86)
    assert claim.claim_number.value == "BWC23-93574"
    assert claim.line.value == "property"
    assert claim.status.value == "Open"
    assert run.summary.claim_count.value == 2
    assert run.summary.total_incurred.value == 45771.88


def test_without_a_readable_header_incurred_is_the_sum_of_the_other_two() -> None:
    rows = ["2025-09-12 BWC24-42947 General liability 40,000.00 663.02 40,663.02 Closed"]
    run = extract_loss_run(segment("loss_run", [loss_run_page(["unreadable"], rows)]))
    claim = run.claims[0]
    assert (claim.paid.value, claim.reserve.value, claim.incurred.value) == (40000.0, 663.02, 40663.02)
    assert claim.line.value == "general_liability"
    assert claim.status.value == "Closed"


def test_property_schedule_row_is_read_from_both_ends() -> None:
    text = (
        "L2 Masonry non combustible retail store with storage 1991 2020 2 15,506 Yes 7 "
        "4,232,079.00 797,792.00 1,167,412.00 6,197,283.00"
    )
    page = make_page([text_row(text, step=40.0)])
    location = parse_location(LineRef(page, page.lines[0]), "D07")
    assert location is not None
    assert location.location_id.value == "L2"
    assert location.construction_class.value == "masonry_non_combustible"
    assert location.occupancy.value == "retail store with storage"
    assert (location.year_built.value, location.roof_year.value, location.stories.value) == (1991, 2020, 2)
    assert (location.square_feet.value, location.sprinklered.value, location.protection_class.value) == (15506, True, 7)
    assert location.tiv.value == 6197283.0
    assert location.business_income_value.value == 1167412.0


def test_unknown_line_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown packet line"):
        extract_with_rules("PKT-C-001", "auto", [])
