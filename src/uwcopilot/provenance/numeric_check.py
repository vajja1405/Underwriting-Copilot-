"""Deterministic numeric-provenance checker for free-form text (docs/innovations.md, SIG step 2).

Where the model must restate numbers (free-form RAG answers, KYC adverse-media memos), this checker asks one question
per number: does the same quantity appear in a source that the sentence cites and that was in the model's context?

check_numbers(text, sources) splits the text into sentences. For each numeric mention in a sentence:
- UNTRACEABLE_NUMBER: no cited source (among `sources`) contains the same quantity (numbers.same_quantity). A sentence
  that cites nothing makes all its numbers untraceable. A number the model computed itself (a derived number) is
  untraceable by design: the checker cannot verify arithmetic it did not see.
- COMPARATOR_MISMATCH: the number is traceable, but the comparator next to it in the answer (for example "at least")
  belongs to a different class than every comparator the source pairs with that value (for example "more than").
- comparator_unverifiable (not an error, counted): the source pairs the value with SEVERAL comparator classes
  (NBC-3.4 has both "10.0% or less" and "more than 10.0%"), so the comparator cannot be checked.

Comparator classes: GE (at least, or more, at or above, minimum, no less than), GT (more than, exceeds, above, greater
than), LE (at most, or less, up to, maximum, no more than, not more than), LT (below, less than, under, fewer than,
lower than).

Which number a comparator belongs to (the same rules for the answer and for the source):
- Each comparator phrase belongs to ONE number: the closest number in the same sentence, counted in tokens. On a tie,
  a phrase that starts with "or" ("10.0% or less", "3 or more") belongs to the number before it; every other phrase
  ("more than 55.0%", "at least 1.25") to the number after it. So in "the five-year loss ratio is more than 55.0%",
  "more than" belongs to 55.0%, not to "five-year".
- The comparator of a number is the closest phrase that belongs to it, within 5 tokens on either side.
- Coordinated values share one comparator. A number with no comparator of its own takes the comparator of the number
  right before it when both have the same unit and only a joiner stands between them: "/", "and", "or" or "and/or",
  optionally after a comma and after a "per <word>" basis of the first value. So in "limits above $2,000,000 /
  $4,000,000 are not offered" both values are GT, and in "up to $25,000,000 per account and $10,000,000 per location"
  both are LE. A bare comma does not join (in policy text it more often separates different items), and nothing
  flows backwards ("$2,000,000 / $4,000,000 or more" gives "or more" to $4,000,000 only).

Tables in a source (markdown tables, one row per line). A cell's comparator is, in this order:
1. a comparator inside the cell that belongs to the cell's number ("10.0% or less", "Above $1,000,000");
2. otherwise the comparator of its column header ("Minimum decision DSCR" binds GE to 1.25 and 1.35, "Maximum annual
   premium" binds LE to $150,000), however long the header is;
3. otherwise the comparator of its row label (first cell), for tables laid out the other way round
   ("| Minimum DSCR | 1.25 | 1.35 |").
A header or row label lends its comparator only when it holds exactly one comparator class and no number of its own:
in "Accounts receivable (aged 90 days or less)" the "or less" belongs to 90 days, not to the row's 70% advance rate.
A header comparator counts like any other: an answer must use the same class. "A premium of up to $150,000" matches
"Maximum annual premium"; "a premium above $150,000" (the other side of the same limit) is a COMPARATOR_MISMATCH,
because the checker cannot tell which side of a limit a sentence's consequence is attached to.
Tables in an ANSWER are not expanded: each row line is read as one sentence with the 5-token window.

Known blind spot (measured by the mutation test as `wrong_row`): when a section contains the same value in another
row of a table (the revolver's 1.35 attached to a term loan), the number looks traceable. The checker sees values,
not row bindings.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from uwcopilot.provenance.numbers import NumberMention, extract_numbers, same_quantity

ComparatorClass = Literal["GE", "GT", "LE", "LT"]
MentionStatus = Literal["traceable", "untraceable", "comparator_mismatch", "comparator_unverifiable"]

# Longest phrases first so "no more than" is not read as "more than".
COMPARATOR_LEXICON: tuple[tuple[str, ComparatorClass], ...] = (
    ("at or above", "GE"),
    ("at or below", "LE"),
    ("no less than", "GE"),
    ("not less than", "GE"),
    ("no more than", "LE"),
    ("not more than", "LE"),
    ("no fewer than", "GE"),
    ("greater than", "GT"),
    ("higher than", "GT"),
    ("larger than", "GT"),
    ("more than", "GT"),
    ("less than", "LT"),
    ("lower than", "LT"),
    ("fewer than", "LT"),
    ("smaller than", "LT"),
    ("at least", "GE"),
    ("at most", "LE"),
    ("or more", "GE"),
    ("or greater", "GE"),
    ("or higher", "GE"),
    ("or less", "LE"),
    ("or fewer", "LE"),
    ("or lower", "LE"),
    ("up to", "LE"),
    ("minimum", "GE"),
    ("min", "GE"),
    ("maximum", "LE"),
    ("max", "LE"),
    ("exceeds", "GT"),
    ("exceeding", "GT"),
    ("exceed", "GT"),
    ("above", "GT"),
    ("below", "LT"),
    ("under", "LT"),
)
# Words of a phrase may be separated by any whitespace, including a line break inside a markdown paragraph.
COMPARATOR_PATTERN = re.compile(
    r"\b(?:" + "|".join(r"\s+".join(map(re.escape, phrase.split())) for phrase, _ in COMPARATOR_LEXICON) + r")\b",
    re.IGNORECASE,
)
CLASS_OF: dict[str, ComparatorClass] = {phrase: cls for phrase, cls in COMPARATOR_LEXICON}
# Phrases written after their number ("10.0% or less"); on a distance tie they belong to the number before them.
SUFFIX_PHRASES: frozenset[str] = frozenset(phrase for phrase, _ in COMPARATOR_LEXICON if phrase.startswith("or "))
WINDOW_TOKENS = 5
# The text between two coordinated values (module docstring): an optional "per <word>" basis of the first value
# ("$1,000,000 per occurrence / $2,000,000"), an optional comma, then "/", "and", "or" or "and/or".
COORDINATED = re.compile(r"^(?:\s+per\s+[A-Za-z]+)?\s*,?\s*(?:/|and/or|and|or)\s*$", re.IGNORECASE)
CITATION_IDS = re.compile(r"\[([^\[\]]+)\]")
# Split after a full stop before a capital letter; never before "[" so a trailing citation stays with its sentence.
# A soft line wrap inside a markdown paragraph is not a break; a blank line, a bullet or a table row is.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z{\"'(])|\n\s*\n|\n(?=\s*(?:[-*|]|\d+[.)])\s?)")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|\s*:?-{2,}")


@dataclass(frozen=True)
class MentionCheck:
    """The verdict on one number in the checked text."""

    sentence_index: int
    mention: NumberMention
    status: MentionStatus
    comparator: ComparatorClass | None
    matched_source: str | None
    cited: tuple[str, ...]
    detail: str


@dataclass
class NumericCheck:
    """The verdict on a whole text."""

    mentions: list[MentionCheck] = field(default_factory=list)

    @property
    def untraceable(self) -> list[MentionCheck]:
        """Numbers no cited source contains."""
        return [item for item in self.mentions if item.status == "untraceable"]

    @property
    def comparator_mismatches(self) -> list[MentionCheck]:
        """Numbers whose comparator disagrees with every comparator the source uses for that value."""
        return [item for item in self.mentions if item.status == "comparator_mismatch"]

    @property
    def comparator_unverifiable(self) -> list[MentionCheck]:
        """Numbers whose source uses the value with several comparator classes."""
        return [item for item in self.mentions if item.status == "comparator_unverifiable"]

    @property
    def flagged(self) -> bool:
        """True when any number is untraceable or has a mismatched comparator."""
        return bool(self.untraceable or self.comparator_mismatches)


# "1.25. [NBC-4.2] Next" -> "1.25 [NBC-4.2]. Next": a citation written after the full stop belongs to that sentence.
TRAILING_CITATION = re.compile(r"([.!?])\s*((?:\[[^\[\]]+\]\s*)+)(?=\s|$)")


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Sentences with their start offsets (in the text after trailing citations are moved before the full stop).

    A citation written right after a sentence's full stop stays with that sentence, so the next sentence cannot
    borrow it.
    """
    text = TRAILING_CITATION.sub(lambda match: f" {match.group(2).strip()}{match.group(1)} ", text)
    sentences = []
    position = 0
    for part in SENTENCE_SPLIT.split(text):
        start = text.find(part, position)
        if part.strip():
            sentences.append((start, part))
        position = start + len(part)
    return sentences


def cited_ids(sentence: str) -> tuple[str, ...]:
    """Ids cited in a sentence: '[NBC-4.2]', '[NBC-4.2, NBC-4.5]', '[S1][S3]' -> ('NBC-4.2', ...)."""
    ids: list[str] = []
    for block in CITATION_IDS.findall(sentence):
        for item in re.split(r"[,;]\s*", block):
            item = item.strip()
            if item and not item.startswith("^"):
                ids.append(item)
    return tuple(dict.fromkeys(ids))


def token_distance(text: str, start: int, end: int) -> int:
    """Number of whitespace-separated tokens strictly between two character positions."""
    between = text[min(start, end) : max(start, end)]
    return len(between.split())


def phrase_of(match: re.Match[str]) -> str:
    """The lexicon phrase of a comparator match, lower case with single spaces ('More   than' -> 'more than')."""
    return " ".join(match.group(0).lower().split())


def comparator_distance(text: str, match: re.Match[str], mention: NumberMention) -> int:
    """Tokens between a comparator phrase and a number's first token (so "3 or more months" has distance 0)."""
    first_token = mention.text.split()[0] if mention.text.split() else mention.text
    anchor_start, anchor_end = mention.start, mention.start + len(first_token)
    if match.end() <= anchor_start:
        return token_distance(text, match.end(), anchor_start)
    if match.start() >= anchor_end:
        return token_distance(text, anchor_end, match.start())
    return 0


def owner_of(text: str, match: re.Match[str], mentions: list[NumberMention]) -> NumberMention | None:
    """The number a comparator phrase belongs to: the closest one; on a tie, the side the phrase reads towards.

    A suffix phrase ("or less") reads towards the number before it, every other phrase towards the number after it.
    """
    suffix = phrase_of(match) in SUFFIX_PHRASES

    def rank(mention: NumberMention) -> tuple[int, int]:
        before = mention.start < match.start()
        on_its_side = before if suffix else not before
        return comparator_distance(text, match, mention), 0 if on_its_side else 1

    return min(mentions, key=rank) if mentions else None


def owned_comparator(text: str, mention: NumberMention, numbers: list[NumberMention]) -> ComparatorClass | None:
    """The class of the closest comparator phrase within WINDOW_TOKENS tokens that belongs to this number, or None.

    numbers are all the numbers of text; a phrase that belongs to another of them is skipped (module docstring).
    """
    others = [item for item in numbers if (item.start, item.end) != (mention.start, mention.end)]
    candidates = [mention, *others]
    best: tuple[int, ComparatorClass] | None = None
    for match in COMPARATOR_PATTERN.finditer(text):
        distance = comparator_distance(text, match, mention)
        if distance > WINDOW_TOKENS or owner_of(text, match, candidates) is not mention:
            continue
        if best is None or distance < best[0]:
            best = (distance, CLASS_OF[phrase_of(match)])
    return None if best is None else best[1]


def coordinated_previous(text: str, mention: NumberMention, numbers: list[NumberMention]) -> NumberMention | None:
    """The number right before this one when the two are coordinated ('$2,000,000 / $4,000,000'), else None.

    Coordinated means: the same unit, and only a joiner between them (COORDINATED, module docstring).
    """
    before = [item for item in numbers if item.end <= mention.start]
    if not before:
        return None
    previous = max(before, key=lambda item: item.end)
    if previous.unit != mention.unit or not COORDINATED.match(text[previous.end : mention.start]):
        return None
    return previous


def comparator_near(
    text: str, mention: NumberMention, mentions: list[NumberMention] | None = None
) -> ComparatorClass | None:
    """The comparator class of one number of text, or None (module docstring, "Which number a comparator belongs to").

    The number's own comparator if it has one; otherwise the comparator of the number it is coordinated with.
    mentions are the numbers of text (extracted when not given).
    """
    numbers = mentions or extract_numbers(text)
    own = owned_comparator(text, mention, numbers)
    if own is not None:
        return own
    previous = coordinated_previous(text, mention, numbers)
    return None if previous is None else comparator_near(text, previous, numbers)


def split_cells(line: str) -> list[str]:
    """'| a | b |' -> ['a', 'b']."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


@dataclass(frozen=True)
class TableCell:
    """One cell of a markdown table, with the column header and row label it sits under ('' when there is none)."""

    text: str
    header: str
    row_label: str


def table_blocks(source_text: str) -> list[list[str]]:
    """Every markdown table in the text, as its list of lines (header line first, rule lines included)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in source_text.splitlines():
        if TABLE_ROW.match(line):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def table_cells(source_text: str) -> list[TableCell]:
    """Every cell of every markdown table, bound to its column header and row label.

    Header cells are returned too (with no header of their own), so a number written in a header ("5-year loss
    ratio") is still found. A body row whose cell count differs from the header's keeps its cells, unbound.
    """
    cells: list[TableCell] = []
    for block in table_blocks(source_text):
        header = split_cells(block[0])
        cells.extend(TableCell(cell, "", "") for cell in header)
        for line in block[1:]:
            if TABLE_RULE.match(line):
                continue
            row = split_cells(line)
            if len(row) != len(header):
                cells.extend(TableCell(cell, "", "") for cell in row)
                continue
            for column, cell in enumerate(row):
                row_label = row[0] if column > 0 else ""
                cells.append(TableCell(cell, header[column], row_label))
    return cells


def prose_only(source_text: str) -> str:
    """The text with every table line replaced by an empty line (tables are read cell by cell instead)."""
    return "\n".join("" if TABLE_ROW.match(line) else line for line in source_text.splitlines())


def label_comparator(label: str) -> ComparatorClass | None:
    """The comparator a column header or row label lends to its cells: its only class, if it has no number."""
    if not label or extract_numbers(label):
        return None
    classes = {CLASS_OF[phrase_of(match)] for match in COMPARATOR_PATTERN.finditer(label)}
    return classes.pop() if len(classes) == 1 else None


def cell_comparator(cell: TableCell, mention: NumberMention, mentions: list[NumberMention]) -> ComparatorClass | None:
    """A cell's comparator for one of its numbers: in the cell, else from the column header, else the row label."""
    return (
        comparator_near(cell.text, mention, mentions)
        or label_comparator(cell.header)
        or label_comparator(cell.row_label)
    )


def source_comparators(source_text: str, mention: NumberMention) -> set[ComparatorClass]:
    """Comparator classes the source pairs with the same quantity (empty when it uses none).

    Prose: each comparator is looked for only inside the sentence (or line) of the value. Tables: each cell is read
    on its own, with its column header and row label (module docstring), so a comparator never leaks across cells.
    """
    classes: set[ComparatorClass] = set()
    for _, sentence in split_sentences(prose_only(source_text)):
        sentence_mentions = extract_numbers(sentence)
        for source_mention in sentence_mentions:
            if same_quantity(source_mention, mention):
                found = comparator_near(sentence, source_mention, sentence_mentions)
                if found is not None:
                    classes.add(found)
    for cell in table_cells(source_text):
        cell_mentions = extract_numbers(cell.text)
        for source_mention in cell_mentions:
            if same_quantity(source_mention, mention):
                found = cell_comparator(cell, source_mention, cell_mentions)
                if found is not None:
                    classes.add(found)
    return classes


def check_mention(
    sentence: str, mention: NumberMention, cited: tuple[str, ...], sources: dict[str, str]
) -> tuple[MentionStatus, ComparatorClass | None, str | None, str]:
    """Status, answer comparator, matching source id and a short detail for one number.

    Comparator rule (docs/innovations.md SIG step 2): if the source never pairs the value with the answer's
    comparator class, that is a COMPARATOR_MISMATCH. This includes a source that states the value with no comparator
    at all (an answer adding "at least" asserts something the source does not say); the detail says which case it
    was. When the source pairs the value with several classes, the comparator cannot be checked: the mention passes
    and is counted as comparator_unverifiable.
    """
    comparator = comparator_near(sentence, mention)
    for source_id in cited:
        source_text = sources.get(source_id)
        if source_text is None:
            continue
        if not any(same_quantity(other, mention) for other in extract_numbers(source_text)):
            continue
        if comparator is None:
            return "traceable", None, source_id, "value found in cited source"
        classes = source_comparators(source_text, mention)
        if len(classes) > 1:
            return "comparator_unverifiable", comparator, source_id, f"source uses {sorted(classes)}"
        if comparator in classes:
            return "traceable", comparator, source_id, "value and comparator found in cited source"
        detail = "source states the value without a comparator" if not classes else f"source uses {sorted(classes)}"
        return "comparator_mismatch", comparator, source_id, detail
    detail = "no cited source in context" if not any(item in sources for item in cited) else "value not in sources"
    return "untraceable", comparator, None, detail


def check_numbers(text: str, sources: dict[str, str]) -> NumericCheck:
    """Check every number in text against the cited sources that were in context (see the module docstring).

    sources maps an id (a section id such as "NBC-4.2", or a snippet id such as "S1") to the text the model saw.
    A cited id missing from sources (not in context, or fabricated) cannot make a number traceable.
    """
    result = NumericCheck()
    for index, (_, sentence) in enumerate(split_sentences(text)):
        cited = cited_ids(sentence)
        for mention in extract_numbers(sentence):
            status, comparator, matched, detail = check_mention(sentence, mention, cited, sources)
            result.mentions.append(MentionCheck(index, mention, status, comparator, matched, cited, detail))
    return result
