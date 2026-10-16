"""Rules extraction of the SYNTHETIC property schedule / statement of values (insurance packets).

Each location takes two printed lines: the values row, then "Address: ...". A values row is any line after the
column header with at least four table amounts (building, contents, business income and TIV are its last four
amounts; values are never negative, so a minus sign read by OCR is dropped); "Total TIV" lines are not rows. In a
values row:
- location id: the first word when it reads "L<n>". On an OCR line a bare number "<n>" is read as "L<n>" (the
  small "L" is often lost in a scan). A row whose id cannot be read keeps its other values, without an id;
- the numeric cells between the text and the amounts (year built, roof year, stories, square feet, sprinklered
  Yes/No, protection class) are matched to the header's column titles: each word goes to the title whose centre is
  nearest its own centre and is kept only if it parses as that column's type (a year between 1800 and 2100, a
  count, Yes/No, a protection class 1-10), so a stray OCR word is ignored. When the header titles cannot all be
  read, the cells are read from the right in printed order, each word going to the first remaining column whose
  type it fits (a column whose cell was lost stays missing);
- construction class: the longest construction label of agent.applications.ConstructionClass that the text words
  start with (as a code); occupancy: the rest of the text words.
Total TIV is a label/value line and the signature line's date after "Date signed" is the signed date.
"""

import re
from collections.abc import Callable
from typing import Any, get_args

from uwcopilot.agent.applications import ConstructionClass
from uwcopilot.docintel.classify import Segment, normalize_for_match
from uwcopilot.docintel.layout import Word
from uwcopilot.docintel.parsing import (
    code_from_label,
    fix_ocr_digits,
    parse_amount,
    parse_bool,
    parse_date,
    parse_int,
    parse_text,
    strip_edges,
)
from uwcopilot.docintel.rules_common import (
    LineRef,
    amount_words,
    cite_words,
    document_lines,
    find_line,
    find_value,
    header_index,
    nearest_column,
    title_box,
    value_after_label,
    words_text,
)
from uwcopilot.docintel.schemas import Cited, PropertySchedule, ScheduleLocation

LOCATION_ID = re.compile(r"^L\d{1,2}$")
CONSTRUCTION_LABELS = sorted(
    (code.replace("_", " ") for code in get_args(ConstructionClass)), key=lambda label: len(label), reverse=True
)
SCHEDULE_TITLES = ("Loc", "Construction", "Occupancy", "Built", "Roof", "Stories", "Sprk", "Building", "TIV")
SCHEDULE_AMOUNTS = ("building_value", "contents_value", "business_income_value", "tiv")
YEAR_RANGE = (1800, 2100)
PROTECTION_CLASSES = (1, 10)
TEXT_MARGIN = 15.0  # a text word's centre lies at least this far left of the first numeric column's centre


def parse_year(text: str) -> int | None:
    """A year between 1800 and 2100, or None."""
    value = parse_int(text)
    return value if value is not None and YEAR_RANGE[0] <= value <= YEAR_RANGE[1] else None


def parse_count(text: str) -> int | None:
    """A positive whole number (stories, square feet), or None."""
    value = parse_int(text)
    return value if value is not None and value > 0 else None


def parse_protection_class(text: str) -> int | None:
    """A protection class 1-10, or None."""
    value = parse_int(text)
    return value if value is not None and PROTECTION_CLASSES[0] <= value <= PROTECTION_CLASSES[1] else None


# (field, header title, parser) in printed order, left to right.
NUMERIC_COLUMNS: tuple[tuple[str, str, Callable[[str], Any]], ...] = (
    ("year_built", "Built", parse_year),
    ("roof_year", "Roof", parse_year),
    ("stories", "Stories", parse_count),
    ("square_feet", "Sq ft", parse_count),
    ("sprinklered", "Sprk", parse_bool),
    ("protection_class", "PC", parse_protection_class),
)


def location_id_of(word: Word, fuzzy: bool) -> str | None:
    """ "L1" from a location-id word ("Ll" or "LI" are OCR-fixed; a bare "1" counts on an OCR line), or None."""
    text = strip_edges(word.text)
    if text[:1] == "L":
        fixed = "L" + fix_ocr_digits(text[1:])
        return fixed if LOCATION_ID.match(fixed) else None
    digits = fix_ocr_digits(text)
    if fuzzy and digits.isdigit() and len(digits) <= 2:
        return f"L{int(digits)}"
    return None


def center(word: Word) -> float:
    """Horizontal centre of a word."""
    return (word.x0 + word.x1) / 2.0


def column_centers(header: LineRef | None) -> dict[str, float]:
    """Centre x of each numeric column title found on the header line."""
    centers: dict[str, float] = {}
    if header is None:
        return centers
    for name, title, _ in NUMERIC_COLUMNS:
        box = title_box(header, title)
        if box is not None:
            centers[name] = (box[0] + box[1]) / 2.0
    return centers


def cells_by_column(words: list[Word], centers: dict[str, float]) -> dict[str, tuple[Any, Word]]:
    """Numeric cells matched to the nearest column title; a word that does not parse as that column is ignored."""
    parsers = {name: parser for name, _, parser in NUMERIC_COLUMNS}
    cells: dict[str, tuple[Any, Word]] = {}
    for word in words:
        name = nearest_column(center(word), centers)
        value = None if name is None else parsers[name](word.text)
        if name is not None and value is not None and name not in cells:
            cells[name] = (value, word)
    return cells


def cells_from_the_right(words: list[Word]) -> dict[str, tuple[Any, Word]]:
    """Numeric cells read right to left, each word going to the first remaining column whose type it fits."""
    columns = list(reversed(NUMERIC_COLUMNS))
    cells: dict[str, tuple[Any, Word]] = {}
    position = 0
    for word in reversed(words):
        for index in range(position, len(columns)):
            name, _, parser = columns[index]
            value = parser(word.text)
            if value is not None:
                cells[name] = (value, word)
                position = index + 1
                break
        if position >= len(columns):
            break
    return cells


def split_text_and_numbers(words: list[Word], centers: dict[str, float]) -> tuple[list[Word], list[Word]]:
    """(text words, numeric-cell words) of the words between the location id and the amounts."""
    if len(centers) == len(NUMERIC_COLUMNS):
        first_numeric = min(centers.values()) - TEXT_MARGIN
        text = [word for word in words if center(word) < first_numeric]
        return text, [word for word in words if center(word) >= first_numeric]
    cells = cells_from_the_right(words)
    numeric_ids = {id(word) for _, word in cells.values()}
    first = min((index for index, word in enumerate(words) if id(word) in numeric_ids), default=len(words))
    return words[:first], words[first:]


def construction_split(words: list[Word]) -> tuple[list[Word], list[Word]]:
    """(construction words, occupancy words): the longest construction label the words start with."""
    text = normalize_for_match(words_text(words))
    for label in CONSTRUCTION_LABELS:
        size = len(label.split())
        if text.startswith(label) and len(words) > size:
            return words[:size], words[size:]
    return words[:1], words[1:]


def parse_location(ref: LineRef, doc_id: str, centers: dict[str, float] | None = None) -> ScheduleLocation | None:
    """One location values row (see the module docstring), or None when the line is not one."""
    words = ref.words
    amounts = amount_words(words)
    if len(amounts) < len(SCHEDULE_AMOUNTS) or normalize_for_match(ref.text).startswith("total"):
        return None
    amounts = amounts[-len(SCHEDULE_AMOUNTS) :]
    location = ScheduleLocation()
    for name, word in zip(SCHEDULE_AMOUNTS, amounts, strict=True):
        setattr(location, name, cite_words(ref, [word], abs(parse_amount(word.text) or 0.0), doc_id))
    before = [word for word in words if word.x1 < amounts[0].x0]
    location_id = location_id_of(before[0], ref.fuzzy) if before else None
    if location_id is not None:
        location.location_id = cite_words(ref, [before[0]], location_id, doc_id)
        before = before[1:]
    text_words, numeric_words = split_text_and_numbers(before, centers or {})
    if centers is not None and len(centers) == len(NUMERIC_COLUMNS):
        cells = cells_by_column(numeric_words, centers)
    else:
        cells = cells_from_the_right(numeric_words)
    for name, (value, word) in cells.items():
        setattr(location, name, cite_words(ref, [word], value, doc_id))
    construction, occupancy = construction_split(text_words) if text_words else ([], [])
    if construction:
        code = code_from_label(words_text(construction))
        location.construction_class = cite_words(ref, construction, code, doc_id)
    if occupancy:
        location.occupancy = cite_words(ref, occupancy, parse_text(words_text(occupancy)), doc_id)
    return location


def signed_date(refs: list[LineRef], doc_id: str) -> Cited:
    """The date printed after "Date signed" on the signature line (an empty Cited when not found)."""
    signed = find_line(refs, "Date signed")
    if signed is None:
        return Cited()
    ref = refs[signed]
    for word in reversed(ref.words):
        value = parse_date(word.text)
        if value is not None:
            return cite_words(ref, [word], value, doc_id)
    return Cited()


def extract_property_schedule(segment: Segment) -> PropertySchedule:
    """The property schedule / statement of values."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    schedule = PropertySchedule(
        doc_id=doc_id,
        insured_name=find_value(refs, ("Insured",), parse_text, doc_id),
        total_tiv=find_value(refs, ("Total TIV (all locations)", "Total TIV"), parse_amount, doc_id),
        signed_date=signed_date(refs, doc_id),
    )
    start = header_index(refs, SCHEDULE_TITLES, 5)
    centers = column_centers(None if start is None else refs[start])
    first_row = 0 if start is None else start + 1
    for index in range(first_row, len(refs)):
        location = parse_location(refs[index], doc_id, centers)
        if location is None:
            continue
        if index + 1 < len(refs):
            address_ref = refs[index + 1]
            address = value_after_label(address_ref.line, "Address", address_ref.fuzzy)
            if address:
                location.address = cite_words(address_ref, address, parse_text(words_text(address)), doc_id)
        schedule.locations.append(location)
    return schedule
