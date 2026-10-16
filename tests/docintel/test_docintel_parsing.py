"""Table tests for the value parsers (docintel/parsing.py), including the OCR repairs."""

from datetime import date

import pytest

from uwcopilot.docintel.parsing import (
    code_from_label,
    fix_ocr_digits,
    is_table_amount,
    normalize_fein,
    parse_amount,
    parse_bool,
    parse_date,
    parse_identifier,
    parse_int,
    parse_money,
    parse_percent,
    parse_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", 1234.56),
        ("1,234.56", 1234.56),
        ("-$1,234.56", -1234.56),
        ("$-1,234.56", -1234.56),
        ("(1,234.56)", -1234.56),
        ("1234.56", 1234.56),
        ("$4,120,000.00", 4120000.0),
        ("$1,234", 1234.0),
        ("0.00", 0.0),
        ("«6,382.03", 6382.03),  # OCR guillemet before the number
        ('"65,182.43', 65182.43),
        ("110,664.71,", 110664.71),  # trailing comma read by OCR
        ("1O,5S3.00", 10553.0),  # O -> 0 and S -> 5 inside a number
        ("§0,685.02", 50685.02),  # section sign read for a 5
        ("$2,806 792.54", 2806792.54),  # thousands comma read as a space
        ("$4,309 ,836.74", 4309836.74),
        ("—14,214.53", -14214.53),  # em dash as a minus sign
        ("_7,471,139.00", 7471139.0),  # a printed rule read as an underscore
        ("4 15,364.94", None),  # two OCR tokens are not one number (a speck read as "4")
        ("3 0.00", None),
        ("$ 1,234.56", 1234.56),  # a space after the dollar sign is not between two digits
        ("S1,234.56", None),  # "$1,234.56" or "51,234.56": ambiguous, not read
        ("-S1,234.56", None),
        ("S123,456.78", 123456.78),  # "5123,456.78" is not a printed amount, so S is the dollar sign
        ("0,685.02", None),  # no amount is printed with a leading zero group
        ("TETAAN", None),
        ("abc", None),
        ("12,34,567.00", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_money(text: str | None, expected: float | None) -> None:
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$70,000.00", 70000.0),
        ("$2,806", None),
        ("2,806.5", None),
        ("$2,806 792.54", 2806792.54),
        ("n/a", None),
        ("4 15,364.94", None),
        ("§0,685.02", 50685.02),
        ("S1,234.56", None),
        ("S123,456.78", 123456.78),
    ],
)
def test_parse_amount_needs_two_decimals(text: str, expected: float | None) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("28,342.69", True), ("-1,234.56", True), ("(12.00)", True), ("14955", False), ("8081", False), ("50.0%", False)],
)
def test_is_table_amount(text: str, expected: bool) -> None:
    assert is_table_amount(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("09/02/2026", date(2026, 9, 2)),
        ("2026-09-02", date(2026, 9, 2)),
        ("01-Sep-2025", date(2025, 9, 1)),
        ("01 Sep 2025", date(2025, 9, 1)),
        ("O1-Sep-2O25", date(2025, 9, 1)),
        ("2O26-O9-O2", date(2026, 9, 2)),
        ("11/12/2025.", date(2025, 11, 12)),
        ("09/02/1 974", date(1974, 9, 2)),
        ("=\u2014ss\u00ab03/17/2024.", date(2024, 3, 17)),  # OCR junk glued before a numeric date
        ("x2026-03-04", date(2026, 3, 4)),
        ("OBA23-32837.", None),  # a claim number is not a date
        ("13/40/2026", None),
        ("40-Aug-2026", None),
        ("31-Xyz-2026", None),
        ("2026", None),
        (None, None),
    ],
)
def test_parse_date(text: str | None, expected: date | None) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("8.75%", 8.75), ("50.0 %", 50.0), ("8.75", 8.75), ("100.0%", 100.0), ("x%", None), ("3 0.00", None)],
)
def test_parse_percent(text: str, expected: float | None) -> None:
    assert parse_percent(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("48", 48),
        ("10,311", 10311),
        ("10 311", 10311),  # a thousands comma read as a space
        ("1O", 10),
        ("4.5", None),
        ("", None),
        ("4 1", None),  # two OCR tokens (a speck read as "4") are not 41
        ("2 5", None),
    ],
)
def test_parse_int(text: str, expected: int | None) -> None:
    assert parse_int(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Yes", True), ("No", False), ("Y", True), ("N", False), ("X", True), ("yes.", True), ("Maybe", None)],
)
def test_parse_bool(text: str, expected: bool | None) -> None:
    assert parse_bool(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("00-5083933", "00-5083933"), ("005083933", "00-5083933"), ("0O-5O83933", "00-5083933"), ("00-50839", None)],
)
def test_normalize_fein(text: str, expected: str | None) -> None:
    assert normalize_fein(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Line of credit", "line_of_credit"),
        ("LLC", "llc"),
        ("Owner occupied CRE", "owner_occupied_cre"),
        ("General liability", "general_liability"),
        ("  ", None),
    ],
)
def test_code_from_label(text: str, expected: str | None) -> None:
    assert code_from_label(text) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("2O26", "2026"),
        ("1l,5S3.B0", "11,553.80"),
        ("SPC119411619", "SPC119411619"),  # other letters: not a numeric token
        ("LIG-5203550", "LIG-5203550"),
        ("Loan", "Loan"),  # no digit: not a numeric token
    ],
)
def test_ocr_digit_fix_only_inside_numeric_tokens(token: str, expected: str) -> None:
    assert fix_ocr_digits(token) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("BWC-3962642°", "BWC-3962642"), ("_OBA-5404069", "OBA-5404069"), (" LIG20-54474 ", "LIG20-54474"), ("--", None)],
)
def test_parse_identifier(text: str, expected: str | None) -> None:
    assert parse_identifier(text) == expected


def test_parse_text_collapses_spaces_and_strips_stray_marks() -> None:
    assert parse_text('  "Hopkins   Machine Works Inc.  ') == "Hopkins Machine Works Inc."
    assert parse_text(" ; ") is None
