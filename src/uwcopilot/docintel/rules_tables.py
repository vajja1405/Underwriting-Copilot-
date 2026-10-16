"""Table-row parsers of the rules extractor that more than one document type uses.

Rows are parsed mostly from their word types (dates, amounts, percentages, Yes/No, Person/Organization) rather
than from fixed column positions, helped by the header's column positions when the header was read, so the
parsers survive small OCR shifts and different layouts:
- owner tables (owner_table): the rows after the header (one line, or two adjacent lines when OCR split it), or
  every line of the owners section when no header can be read;
- owner rows (loan application, insurance application, ownership statement): "Name | Type | Title | Ownership % |
  Control person | Country". The Type word (Person / Organization, under the header's Type title when the header
  shows it; spelled exactly, or approximately on an OCR line) splits the name from the title; the first word with
  a "%" after it is the ownership share; the next word is the control-person Yes/No; the rest is the country.
- two-slot premium rows (prior carriers on the insurance application, earned premium by policy year on a loss
  run): the words after the date are the two premium cells; a cell is an amount or "n/a" (no premium, value
  None; a misread "n/a" is not an amount either, so it also gives None). When the header shows both column titles,
  each amount goes to the column whose right edge is nearest; otherwise the cells are taken in order when there
  are exactly two.
"""

from rapidfuzz import fuzz

from uwcopilot.docintel.layout import Word
from uwcopilot.docintel.parsing import parse_amount, parse_bool, parse_percent, parse_text
from uwcopilot.docintel.rules_common import (
    LineRef,
    cite_words,
    header_lines,
    lines_between,
    lines_until,
    nearest_column,
    title_box,
    words_text,
)
from uwcopilot.docintel.schemas import Cited, ExtractedOwner

OWNER_TITLES = ("Name", "Type", "Title", "Ownership", "Control person", "Country")
OWNER_TITLES_NEEDED = 4
ENTITY_TYPES = ("person", "organization")
TYPE_MIN_SCORE = 80.0
TYPE_COLUMN_TOLERANCE = 15.0  # points between a Type cell and its column title (both are left-aligned)


def entity_type_of(word: Word, fuzzy: bool) -> str | None:
    """ "person" / "organization" when the word spells one (approximately only on an OCR line), else None."""
    text = word.text.strip(".,:;").casefold()
    for entity_type in ENTITY_TYPES:
        if text == entity_type:
            return entity_type
    if not fuzzy:
        return None
    for entity_type in ENTITY_TYPES:
        if fuzz.ratio(text, entity_type) >= TYPE_MIN_SCORE:
            return entity_type
    return None


def type_word_index(ref: LineRef, type_x: float | None) -> int | None:
    """Index of the Type word of an owner row (see the module docstring), or None.

    An exact "Person" / "Organization" wins; on an OCR line an approximate spelling counts too. When the header's
    Type column position is known, only words that start near it are considered, so a surname such as "Peterson"
    is never taken for "Person".
    """
    candidates = []
    for index, word in enumerate(ref.words):
        if index == 0:
            continue
        if type_x is None or abs(word.x0 - type_x) <= TYPE_COLUMN_TOLERANCE:
            candidates.append(index)
    for fuzzy in (False, ref.fuzzy):
        for index in candidates:
            if entity_type_of(ref.words[index], fuzzy) is not None:
                return index
    return None


def percent_index(words: list[Word], start: int) -> int | None:
    """Index of the first word at or after start that is a percentage printed with "%"."""
    for index in range(start, len(words)):
        if "%" in words[index].text and parse_percent(words[index].text) is not None:
            return index
    return None


def parse_owner_row(ref: LineRef, doc_id: str, type_x: float | None = None) -> ExtractedOwner | None:
    """An owners-table row (see the module docstring), or None when the line is not one."""
    words = ref.words
    type_index = type_word_index(ref, type_x)
    if type_index is None:
        return None
    pct_at = percent_index(words, type_index + 1)
    if pct_at is None:
        return None
    owner = ExtractedOwner(
        name=cite_words(ref, words[:type_index], parse_text(words_text(words[:type_index])), doc_id),
        entity_type=cite_words(ref, [words[type_index]], entity_type_of(words[type_index], ref.fuzzy), doc_id),
        ownership_pct=cite_words(ref, [words[pct_at]], parse_percent(words[pct_at].text), doc_id),
    )
    title_words = words[type_index + 1 : pct_at]
    if title_words:
        owner.title = cite_words(ref, title_words, parse_text(words_text(title_words)), doc_id)
    rest = words[pct_at + 1 :]
    if rest and parse_bool(rest[0].text) is not None:
        owner.is_control_person = cite_words(ref, [rest[0]], parse_bool(rest[0].text), doc_id)
        rest = rest[1:]
    if rest:
        owner.country = cite_words(ref, rest, parse_text(words_text(rest)), doc_id)
    return owner


def owner_rows(headers: list[LineRef], refs: list[LineRef], doc_id: str) -> list[ExtractedOwner]:
    """Every owners-table row among the lines, in order (the header lines give the Type column position)."""
    type_x = None
    for header in headers:
        box = title_box(header, "Type")
        if box is not None:
            type_x = box[0]
            break
    owners = []
    for ref in refs:
        owner = parse_owner_row(ref, doc_id, type_x)
        if owner is not None:
            owners.append(owner)
    return owners


def owner_table(
    refs: list[LineRef], section_phrase: str, end_phrases: tuple[str, ...], doc_id: str
) -> list[ExtractedOwner]:
    """The owners table of a document: the rows after its header (one line, or two when OCR split it) up to an end
    phrase; when no header can be read, every line between the section heading and an end phrase is tried."""
    header = header_lines(refs, OWNER_TITLES, OWNER_TITLES_NEEDED)
    if header:
        rows = lines_until(refs[header[-1] + 1 :], end_phrases)
    else:
        rows = lines_between(refs, section_phrase, end_phrases)
    return owner_rows([refs[index] for index in header], rows, doc_id)


def premium_cells(
    ref: LineRef, cells: list[Word], names: tuple[str, str], columns: dict[str, float], doc_id: str
) -> dict[str, Cited]:
    """Column name -> cited premium for the cells after the date (see the module docstring).

    names are the two column names in printed order; columns holds the right edge of each column title found on
    the header line.
    """
    if all(name in columns for name in names):
        pairs = [(nearest_column(word.x1, columns), word) for word in cells]
    elif len(cells) == len(names):
        pairs = list(zip(names, cells, strict=True))
    else:
        pairs = []
    result: dict[str, Cited] = {}
    for name, word in pairs:
        value = parse_amount(word.text)
        if name is not None and value is not None and name not in result:
            result[name] = cite_words(ref, [word], value, doc_id)
    return result
