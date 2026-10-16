"""Shared helpers of the rules extractor (extract_rules.py, rules_credit.py, rules_insurance.py, rules_tables.py).

The rules extractor reads label/value lines and tables from a document's lines (docintel/layout.py) and returns
Cited values (docintel/schemas.py) that point at the page, line and words the value came from.

Label/value lines ("Requested amount  $70,000.00"): value_after_label() checks whether a line STARTS with a label.
The words of the line are joined one at a time and compared with the label after normalization (casefold,
punctuation to spaces). On a text-layer page the comparison is exact; on an OCR page a prefix whose rapidfuzz
ratio with the label is at least LABEL_MIN_SCORE also counts, so a misread letter does not lose the field. The
words after the label are the value words.

find_value() tries every label synonym (longest first, so "Paid to date" is tried before "Paid") on every line in
scope and returns the first value its parser accepts. The parser sees the value words joined; when that fails
(OCR may leave a stray mark or a speck read as a digit next to a number: "Number of claims 4 1"), each value word
is tried alone. When several words parse alone, the word with the highest OCR confidence wins (the earliest one
on a tie, so a text-layer line, where every word has confidence 1.0, keeps its first value).

Trailing OCR noise (trim_noise): a scan's specks are sometimes read as a short word at the end of a line ("Larkspur
Valley Bank elec"). On an OCR line, trailing words with confidence below TRAILING_NOISE_CONF that are a single
character, or at most four characters with a lower-case letter, are dropped from a value (never its last word).

Citations (cite_words): page and line of the line, bbox = union of the value words' boxes, confidence = the line's
confidence (1.0 for a text-layer line, the mean Tesseract word confidence for an OCR line), source_text = the
line's text.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from uwcopilot.docintel.classify import Segment, normalize_for_match
from uwcopilot.docintel.layout import Line, PageText, Word, union_box
from uwcopilot.docintel.parsing import is_table_amount
from uwcopilot.docintel.schemas import Cited

LABEL_MIN_SCORE = 85.0
TRAILING_NOISE_CONF = 0.45
FOOTER_TEXT = "synthetic for demonstration only"

Parser = Callable[[str], Any]


@dataclass(frozen=True)
class LineRef:
    """One line of a document together with the page it is on."""

    page: PageText
    line: Line

    @property
    def fuzzy(self) -> bool:
        """True for an OCR line, where labels are matched approximately."""
        return self.page.source == "ocr"

    @property
    def text(self) -> str:
        """The line's text."""
        return self.line.text

    @property
    def words(self) -> list[Word]:
        """The line's words, left to right."""
        return self.line.words


def document_lines(segment: Segment) -> list[LineRef]:
    """Every line of a document in page order, without the SYNTHETIC footer lines."""
    refs = []
    for page in segment.pages:
        for line in page.lines:
            ref = LineRef(page, line)
            if not is_footer(ref):
                refs.append(ref)
    return refs


def is_footer(ref: LineRef) -> bool:
    """True for the page footer ("SYNTHETIC - for demonstration only  Page i of n") or its OCR pieces."""
    text = normalize_for_match(ref.text)
    if text.startswith(FOOTER_TEXT):
        return True
    words = text.split()
    return len(words) == 4 and words[0] == "page" and words[2] == "of"


def cite_words(ref: LineRef, words: list[Word], value: Any, doc_id: str) -> Cited:
    """A Cited value for words of one line (see the module docstring)."""
    boxes = [word.bbox for word in words] if words else [ref.line.bbox]
    return Cited(
        value=value,
        doc_id=doc_id,
        page=ref.page.page_number,
        line=ref.line.line_number,
        bbox=union_box(boxes),
        confidence=round(ref.line.confidence, 4),
        source_text=ref.text,
    )


def cite_lines(refs: list[LineRef], value: Any, doc_id: str) -> Cited:
    """A Cited value for free text over several lines: the first line, the union box and the joined text."""
    if not refs:
        return Cited()
    confidence = sum(ref.line.confidence for ref in refs) / len(refs)
    return Cited(
        value=value,
        doc_id=doc_id,
        page=refs[0].page.page_number,
        line=refs[0].line.line_number,
        bbox=union_box([ref.line.bbox for ref in refs]),
        confidence=round(confidence, 4),
        source_text=" ".join(ref.text for ref in refs),
    )


def words_text(words: list[Word]) -> str:
    """The words joined with single spaces."""
    return " ".join(word.text for word in words)


def value_after_label(line: Line, label: str, fuzzy: bool) -> list[Word] | None:
    """The words after `label` when the line starts with it, else None (see the module docstring)."""
    target = normalize_for_match(label)
    best_count = 0
    best_score = 0.0
    for count in range(1, len(line.words) + 1):
        prefix = normalize_for_match(words_text(line.words[:count]))
        if prefix == target:
            return line.words[count:]
        if fuzzy:
            score = fuzz.ratio(prefix, target)
            if score > best_score:
                best_count, best_score = count, score
        if len(prefix) > len(target) + 6:
            break
    if fuzzy and best_score >= LABEL_MIN_SCORE:
        return line.words[best_count:]
    return None


def is_trailing_noise(word: Word) -> bool:
    """True for a low-confidence short word that is most likely a speck read as text (see the module docstring)."""
    if word.conf >= TRAILING_NOISE_CONF:
        return False
    alphanumeric = [character for character in word.text if character.isalnum()]
    has_lower = any(character.islower() for character in alphanumeric)
    return len(alphanumeric) <= 1 or (len(alphanumeric) <= 4 and has_lower)


def trim_noise(words: list[Word]) -> list[Word]:
    """The words without trailing OCR noise words (at least one word is kept)."""
    trimmed = list(words)
    while len(trimmed) > 1 and is_trailing_noise(trimmed[-1]):
        trimmed.pop()
    return trimmed


def parse_value_words(words: list[Word], parser: Parser) -> tuple[Any, list[Word]]:
    """(value, words used): the joined words first, then each word alone, preferring the most confident word
    (see the module docstring); (None, []) when nothing parses."""
    words = trim_noise(words)
    if not words:
        return None, []
    value = parser(words_text(words))
    if value is not None:
        return value, words
    best: tuple[Any, Word] | None = None
    for word in words:
        value = parser(word.text)
        if value is not None and (best is None or word.conf > best[1].conf):
            best = (value, word)
    if best is None:
        return None, []
    return best[0], [best[1]]


def find_value(refs: list[LineRef], labels: tuple[str, ...], parser: Parser, doc_id: str) -> Cited:
    """The first value of a labelled field in the lines (see the module docstring); an empty Cited if not found."""
    for label in sorted(labels, key=len, reverse=True):
        for ref in refs:
            words = value_after_label(ref.line, label, ref.fuzzy)
            if not words:
                continue
            value, used = parse_value_words(words, parser)
            if value is not None:
                return cite_words(ref, used, value, doc_id)
    return Cited()


def find_line(refs: list[LineRef], phrase: str) -> int | None:
    """Index of the first line whose normalized text contains the phrase (approximately on OCR lines), or None."""
    target = normalize_for_match(phrase)
    for index, ref in enumerate(refs):
        text = normalize_for_match(ref.text)
        if target in text:
            return index
        if ref.fuzzy and len(text) >= len(target) and fuzz.partial_ratio(target, text) >= LABEL_MIN_SCORE:
            return index
    return None


def lines_until(refs: list[LineRef], end_phrases: tuple[str, ...]) -> list[LineRef]:
    """The lines up to (not including) the first line holding one of the end phrases."""
    targets = [normalize_for_match(phrase) for phrase in end_phrases]
    result = []
    for ref in refs:
        text = normalize_for_match(ref.text)
        if any(target in text for target in targets):
            break
        result.append(ref)
    return result


def lines_between(refs: list[LineRef], start_phrase: str, end_phrases: tuple[str, ...]) -> list[LineRef]:
    """The lines after the line holding start_phrase, up to (not including) the first line holding an end phrase."""
    start = find_line(refs, start_phrase)
    if start is None:
        return []
    return lines_until(refs[start + 1 :], end_phrases)


def amount_words(words: list[Word]) -> list[Word]:
    """The words printed as table amounts (two decimals), left to right."""
    return [word for word in words if is_table_amount(word.text)]


def titles_on(ref: LineRef, titles: tuple[str, ...]) -> set[str]:
    """The column titles a line holds (approximately on an OCR line)."""
    text = normalize_for_match(ref.text)
    found = set()
    for title in titles:
        target = normalize_for_match(title)
        if target in text or (ref.fuzzy and fuzz.partial_ratio(target, text) >= LABEL_MIN_SCORE):
            found.add(title)
    return found


def header_index(refs: list[LineRef], titles: tuple[str, ...], min_found: int) -> int | None:
    """Index of the first line holding at least min_found of the column titles (approximately on OCR lines)."""
    for index, ref in enumerate(refs):
        if len(titles_on(ref, titles)) >= min_found:
            return index
    return None


def header_lines(refs: list[LineRef], titles: tuple[str, ...], min_found: int) -> list[int]:
    """Indexes of the table header: one line holding min_found titles, or else two adjacent lines that hold them
    together (OCR sometimes splits a header row in two). [] when there is no header."""
    single = header_index(refs, titles, min_found)
    if single is not None:
        return [single]
    for index in range(len(refs) - 1):
        if len(titles_on(refs[index], titles) | titles_on(refs[index + 1], titles)) >= min_found:
            return [index, index + 1]
    return []


def title_box(ref: LineRef, title: str) -> tuple[float, float] | None:
    """(x0, x1) of the words of a header line that spell a column title, or None when they are not found."""
    target = normalize_for_match(title)
    size = len(target.split())
    words = ref.words
    for start in range(len(words)):
        for count in (size, size + 1, size - 1):
            if count < 1 or start + count > len(words):
                continue
            text = normalize_for_match(words_text(words[start : start + count]))
            if text == target or (ref.fuzzy and fuzz.ratio(text, target) >= LABEL_MIN_SCORE):
                return words[start].x0, words[start + count - 1].x1
    return None


def nearest_column(x: float, columns: dict[str, float]) -> str | None:
    """The column name whose x position is closest to x (None when there are no columns)."""
    if not columns:
        return None
    return min(columns, key=lambda name: abs(columns[name] - x))
