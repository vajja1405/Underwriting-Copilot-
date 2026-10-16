"""Value parsers for printed document text: money, dates, percentages, integers, yes/no, FEINs and codes.

Every parser takes the printed text (one word or a few words) and returns the value, or None when the text is not
a value of that kind. Parsers never guess: "12,5O0" is fixed to 12500 only because the OCR digit fix below applies,
and anything still not matching the pattern returns None.

OCR digit fix (fix_ocr_digits): Tesseract sometimes reads a digit as a similar letter. Inside a NUMERIC token only
(a token with at least one digit whose other characters are digits, number punctuation or these letters), the
letters are replaced: O/o -> 0, l/I/| -> 1, S/\u00a7 -> 5, B -> 8. A token such as "SPC119411619" (an ID number) or
"LIG-5203550" (a policy number) has other letters, so it is left alone.

Spaces inside a number: OCR sometimes reads a thousands comma as a space ("$2,806 792.54"): pieces after a space
that start with three digits are joined back with a comma (join_thousands_groups). Any other space between two
digits is KEPT, so the text is not one number: "4 1" (a speck read as "4" before the printed "1") is not 41, and
"4 15,364.94" is not 415,364.94. The rules extractor then tries each word alone (rules_common.parse_value_words).
Spaces next to punctuation ("$ 1,234.56", "50.0 %") are removed.

Leading "S" or "\u00a7" in a money value: OCR reads both a "$" and a "5" as "S" (or "\u00a7"). "S1,234.56" can be
"$1,234.56" or "51,234.56", so it is NOT read (None). Only when exactly one reading is a well-formed amount is it
used: "\u00a70,685.02" is 50,685.02 (no amount is printed "$0,685.02") and "S123,456.78" is $123,456.78 (no amount
is printed "5123,456.78"). A well-formed amount has no leading zero before a thousands group.

Formats the parsers accept:
- money: "$1,234.56", "1,234.56", "-$1,234.56", "$-1,234.56", "(1,234.56)", "1234.56", "$1,234" (stray quote or
  bracket characters that OCR puts around a number are stripped first; see unclear_sign for a mark before "$");
- printed amounts (parse_amount): money printed with exactly two decimals, as every amount on these documents is,
  so a fragment such as "$2,806" is never taken for a whole amount;
- dates: "03/14/2026" (month first), "2026-03-14", "14-Mar-2026" (also "14 Mar 2026"). OCR junk glued to the front
  of a date ("=\u2014ss\u00ab03/17/2024.") is cut off when the rest is a numeric date (strip_date_junk);
- percent: "8.75%", "50.0 %", "8.75";
- integers: "48", "10,311";
- yes/no: Yes / No / Y / N / X (a ticked box) / True / False;
- FEIN: 9 digits, printed "NN-NNNNNNN";
- identifiers (policy, claim, ID numbers): only letters, digits and inner hyphens are kept;
- codes: a printed label back to its code by the generator's rule (casefold, spaces and punctuation to "_"):
  "Line of credit" -> "line_of_credit", "LLC" -> "llc".
"""

import re
from datetime import date

MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
OCR_DIGIT_FIXES: dict[str, str] = {"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "\u00a7": "5", "B": "8"}
NUMBER_PUNCTUATION = set("$,.-()%/+")
# Characters OCR often leaves around a number (quotes, guillemets, brackets, stray marks).
STRAY_EDGE_CHARACTERS = "\"'`‘’“”«»[]{}|:;*~_^!?"
STRAY_BEFORE_DOLLAR = re.compile("[" + re.escape(STRAY_EDGE_CHARACTERS) + r"]\$")

MONEY_PATTERN = re.compile(r"^(?P<number>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<cents>\d{1,2}))?$")
# A money value that starts with "S" or the section sign right before a digit (optionally after a sign).
LEADING_S_PATTERN = re.compile(r"^(?P<sign>[-(]?)[S\u00a7](?=\d)")
# Junk OCR glues to the front of a numeric date: the non-digits before a text that is otherwise exactly a date.
DATE_JUNK_PATTERN = re.compile(r"^\D+(?=\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$)")
TABLE_AMOUNT_PATTERN = re.compile(r"^-?\$?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
MDY_PATTERN = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
ISO_PATTERN = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# Day and year groups may hold OCR look-alike letters; they are fixed and checked after the match.
DMY_ABBREV_PATTERN = re.compile(r"^(\w{1,2})[- ]([A-Za-z]{3})[- ](\w{4})$")
PERCENT_PATTERN = re.compile(r"^(-?\d+(?:\.\d+)?)\s*%?$")
INT_PATTERN = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)$")
FEIN_DIGITS = 9
YES_WORDS = {"yes", "y", "x", "true"}
NO_WORDS = {"no", "n", "false"}


def normalize_spaces(text: str) -> str:
    """Collapse runs of whitespace to one space and strip the ends."""
    return " ".join(text.split())


def is_numeric_token(token: str) -> bool:
    """True when the token holds a digit and otherwise only digits, number punctuation or OCR look-alike letters."""
    if not any(character.isdigit() for character in token):
        return False
    for character in token:
        if character.isdigit() or character in NUMBER_PUNCTUATION or character in OCR_DIGIT_FIXES:
            continue
        return False
    return True


def fix_ocr_digits(token: str) -> str:
    """Replace look-alike letters with digits inside a numeric token (see the module docstring)."""
    if not is_numeric_token(token):
        return token
    return "".join(OCR_DIGIT_FIXES.get(character, character) for character in token)


def strip_edges(text: str) -> str:
    """Remove whitespace and stray OCR marks from both ends of a value."""
    return text.strip().strip(STRAY_EDGE_CHARACTERS).strip()


def is_digit_like(character: str) -> bool:
    """True for a digit or an OCR look-alike letter of a digit."""
    return character.isdigit() or character in OCR_DIGIT_FIXES


def remove_inner_spaces(text: str) -> str:
    """Drop the spaces inside a number, except a space between two digits (see the module docstring)."""
    kept = []
    for index, character in enumerate(text):
        if character != " ":
            kept.append(character)
            continue
        before = text[index - 1] if index > 0 else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        if is_digit_like(before) and is_digit_like(after):
            kept.append(character)
    return "".join(kept)


def unclear_sign(text: str) -> bool:
    """True when a stray OCR mark sits right before a "$" in the text ('"$1,295.52'): on these documents only a
    minus sign is ever printed there, so the sign of that amount is not known from the text alone."""
    return STRAY_BEFORE_DOLLAR.search(text) is not None


def clean_number_text(text: str) -> str:
    """Text of a number ready for a pattern.

    Edges are stripped, inner spaces removed except between two digits (module docstring), underscores removed
    (OCR reads a printed rule as "_"), dashes turned into a minus sign, a trailing comma or period dropped
    ("110,664.71," -> "110,664.71") and OCR look-alike letters fixed.
    """
    cleaned = remove_inner_spaces(" ".join(strip_edges(text).split())).replace("_", "")
    cleaned = cleaned.replace("\u2014", "-").replace("\u2013", "-")
    cleaned = cleaned.rstrip(",.")
    return fix_ocr_digits(cleaned)


def join_thousands_groups(text: str) -> str:
    """Rejoin a number that OCR split at a thousands separator ("$2,806 792.54" -> "$2,806,792.54")."""
    pieces = text.split()
    if len(pieces) < 2:
        return text
    rest = [piece.lstrip(",") for piece in pieces[1:]]
    if all(re.match(r"^\d{3}(?:\D|$)", piece) for piece in rest):
        return ",".join([pieces[0].rstrip(","), *rest])
    return text


def well_formed_money(cleaned: str) -> re.Match[str] | None:
    """The MONEY_PATTERN match of a cleaned number, or None (also None for a leading zero: "0,685.02", "0685.02")."""
    match = MONEY_PATTERN.match(cleaned)
    if match is None:
        return None
    number = match.group("number")
    if len(number) > 1 and number.startswith("0"):
        return None
    return match


def leading_s_readings(text: str) -> list[float]:
    """The well-formed readings of a money value that starts with "S" or "\u00a7", as "$" and as "5" (module
    docstring)."""
    readings = []
    for replacement in ("$", "5"):
        value = parse_plain_money(LEADING_S_PATTERN.sub(rf"\g<sign>{replacement}", text, count=1))
        if value is not None and value not in readings:
            readings.append(value)
    return readings


def parse_money(text: str | None) -> float | None:
    """A dollar amount (see the module docstring for the accepted forms), or None."""
    if text is None:
        return None
    stripped = strip_edges(text)
    if LEADING_S_PATTERN.match(stripped):
        readings = leading_s_readings(stripped)
        return readings[0] if len(readings) == 1 else None
    return parse_plain_money(stripped)


def parse_plain_money(text: str) -> float | None:
    """A dollar amount whose text has no ambiguous leading "S" (parse_money does that check), or None."""
    cleaned = clean_number_text(join_thousands_groups(strip_edges(text)))
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    cleaned = cleaned.removeprefix("$")
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    match = well_formed_money(cleaned)
    if match is None:
        return None
    whole = int(match.group("number").replace(",", ""))
    cents_text = match.group("cents") or "0"
    cents = int(cents_text) * (10 if len(cents_text) == 1 else 1)
    value = round(whole + cents / 100.0, 2)
    return -value if negative else value


def is_table_amount(text: str) -> bool:
    """True for an amount printed with two decimals ("1,234.56", "-$12.00"): how every table amount is printed.

    Invoice and check numbers in descriptions ("INV 14955", "CHECK 4235") have no decimals, so they are not amounts.
    """
    cleaned = clean_number_text(text)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return TABLE_AMOUNT_PATTERN.match(cleaned) is not None


def parse_amount(text: str | None) -> float | None:
    """A printed amount with exactly two decimals ("$70,000.00"), or None (see the module docstring)."""
    if text is None:
        return None
    joined = join_thousands_groups(strip_edges(text))
    # The two-decimals check reads a leading "S" as "$"; parse_money then decides between "$" and "5".
    if not is_table_amount(LEADING_S_PATTERN.sub(r"\g<sign>$", joined, count=1)):
        return None
    return parse_money(joined)


def valid_date(year: int, month: int, day: int) -> date | None:
    """The date, or None when the numbers are not a real calendar date."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def abbreviated_month_date(match: re.Match[str]) -> date | None:
    """The date of a "14-Mar-2026" match (day and year OCR-fixed), or None."""
    month = MONTHS.get(match.group(2).casefold())
    day_text = fix_ocr_digits(match.group(1))
    year_text = fix_ocr_digits(match.group(3))
    if month is None or not day_text.isdigit() or not year_text.isdigit():
        return None
    return valid_date(int(year_text), month, int(day_text))


def parse_date(text: str | None) -> date | None:
    """A date in one of the printed formats (see the module docstring), or None."""
    if text is None:
        return None
    cleaned = strip_edges(text).rstrip(".,")
    abbreviated = DMY_ABBREV_PATTERN.match(cleaned)
    if abbreviated is not None:
        return abbreviated_month_date(abbreviated)
    parsed = numeric_date(cleaned)
    if parsed is not None:
        return parsed
    trimmed = strip_date_junk(cleaned)
    return None if trimmed == cleaned else numeric_date(trimmed)


def numeric_date(text: str) -> date | None:
    """A "03/14/2026" or "2026-03-14" date (OCR digit fix and inner spaces removed), or None."""
    numeric = fix_ocr_digits(text.replace(" ", ""))
    match = MDY_PATTERN.match(numeric)
    if match is not None:
        return valid_date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
    match = ISO_PATTERN.match(numeric)
    if match is not None:
        return valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def strip_date_junk(text: str) -> str:
    """The text without the OCR junk before a numeric date ("=\u2014ss\u00ab03/17/2024" -> "03/17/2024")."""
    match = DATE_JUNK_PATTERN.match(text)
    return text if match is None else text[match.end() :]


def parse_percent(text: str | None) -> float | None:
    """A percentage as a number ("8.75%" -> 8.75), or None."""
    if text is None:
        return None
    match = PERCENT_PATTERN.match(clean_number_text(text))
    return None if match is None else float(match.group(1))


def parse_int(text: str | None) -> int | None:
    """An integer, with or without thousands separators, or None."""
    if text is None:
        return None
    cleaned = clean_number_text(join_thousands_groups(strip_edges(text)))
    if INT_PATTERN.match(cleaned) is None:
        return None
    return int(cleaned.replace(",", ""))


def parse_bool(text: str | None) -> bool | None:
    """Yes / No style answers (see the module docstring), or None."""
    if text is None:
        return None
    word = strip_edges(text).rstrip(".").casefold()
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    return None


def normalize_fein(text: str | None) -> str | None:
    """A FEIN as "NN-NNNNNNN" when the text holds exactly 9 digits (after the OCR digit fix), else None."""
    if text is None:
        return None
    fixed = "".join(fix_ocr_digits(token) for token in strip_edges(text).split())
    digits = "".join(character for character in fixed if character.isdigit())
    if len(digits) != FEIN_DIGITS:
        return None
    return f"{digits[:2]}-{digits[2:]}"


def code_from_label(text: str | None) -> str | None:
    """A printed label back to its code: casefolded, runs of spaces and punctuation become "_" (None if empty)."""
    if text is None:
        return None
    code = re.sub(r"[^0-9a-z]+", "_", strip_edges(text).casefold()).strip("_")
    return code or None


def parse_identifier(text: str | None) -> str | None:
    """An identifier (policy, claim, ID or account number) keeps only letters, digits and inner hyphens: OCR marks
    such as "SPC.'137849984" or "BWC-3962642\u00b0" lose their stray characters. None if nothing is left."""
    if text is None:
        return None
    kept = "".join(character for character in text if character.isalnum() or character == "-")
    return kept.strip("-") or None


def parse_text(text: str | None) -> str | None:
    """Free text with whitespace collapsed and stray OCR edge marks removed (None if nothing is left)."""
    if text is None:
        return None
    cleaned = normalize_spaces(strip_edges(text))
    return cleaned or None
