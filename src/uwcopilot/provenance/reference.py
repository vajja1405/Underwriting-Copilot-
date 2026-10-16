"""Reference sentences for the numeric-checker mutation test (docs/innovations.md, SIG "Data").

Every sentence and every table row of the two SYNTHETIC guideline files that contains a number becomes one cited
reference sentence:
- prose: the sentence itself, with its section citation appended, e.g. "Verified annual revenue is at least
  $250,000. [NBC-2.1]";
- tables: one sentence per numeric cell, from the fixed template "The {column} for {row} is {value} [{section}]".
  When the number is in the first column (threshold tables such as NBC-3.4), the template uses the second column:
  "The {second column} for {first cell} is {second cell} [{section}]".

These sentences are correct by construction (they restate the guideline). The mutation module then breaks them in
controlled ways.
"""

import re
from dataclasses import asdict, dataclass

from uwcopilot.agent.programs import load_program_chunks
from uwcopilot.provenance.numbers import extract_numbers
from uwcopilot.rag.guidelines import load_guideline_chunks

SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])")
TABLE_RULE = re.compile(r"^\|\s*-{2,}")
BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")


@dataclass(frozen=True)
class ReferenceSentence:
    """One cited sentence that restates a guideline number."""

    ref_id: str
    section_id: str
    source: str  # "prose" or "table"
    text: str
    table: str | None = None  # table key (section + header) for table rows
    row: str | None = None
    column: str | None = None

    def to_json(self) -> dict:
        """Plain dict for JSONL."""
        return asdict(self)


def split_cells(line: str) -> list[str]:
    """'| a | b |' -> ['a', 'b']."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table_sentences(section_id: str, lines: list[str]) -> list[ReferenceSentence]:
    """Template sentences for the numeric cells of one markdown table."""
    header = split_cells(lines[0])
    table_key = f"{section_id}|{'|'.join(header)}"
    sentences = []
    for line in lines[1:]:
        if TABLE_RULE.match(line):
            continue
        cells = split_cells(line)
        if len(cells) != len(header) or len(cells) < 2:
            continue
        row = cells[0]
        if extract_numbers(row) and not any(extract_numbers(cell) for cell in cells[1:]):
            text = f"The {header[1].lower()} for {row} is {cells[1]} [{section_id}]"
            sentences.append(ReferenceSentence("", section_id, "table", text, table_key, row, header[1]))
            continue
        for column, cell in zip(header[1:], cells[1:], strict=True):
            if extract_numbers(cell):
                text = f"The {column.lower()} for {row} is {cell} [{section_id}]"
                sentences.append(ReferenceSentence("", section_id, "table", text, table_key, row, column))
    return sentences


def prose_sentences(section_id: str, paragraph: str) -> list[ReferenceSentence]:
    """Cited sentences with at least one number from one paragraph or bullet."""
    sentences = []
    for sentence in SENTENCE_END.split(paragraph):
        sentence = sentence.strip()
        if len(sentence) < 12 or not extract_numbers(sentence):
            continue
        sentences.append(ReferenceSentence("", section_id, "prose", f"{sentence} [{section_id}]"))
    return sentences


def section_reference_sentences(section_id: str, text: str) -> list[ReferenceSentence]:
    """All reference sentences of one guideline section, in reading order."""
    sentences: list[ReferenceSentence] = []
    lines = text.splitlines()
    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            sentences.extend(prose_sentences(section_id, " ".join(paragraph)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("|"):
            flush()
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            sentences.extend(table_sentences(section_id, block))
            continue
        if not line.strip():
            flush()
        elif BULLET.match(line):
            flush()
            paragraph.append(BULLET.sub("", line))
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return sentences


def build_reference_sentences() -> list[ReferenceSentence]:
    """Reference sentences for every guideline section, numbered REF-0001, REF-0002, ..."""
    sentences = []
    for chunk in load_guideline_chunks():
        sentences.extend(section_reference_sentences(chunk.chunk_id, chunk.text))
    return [
        ReferenceSentence(f"REF-{number:04d}", s.section_id, s.source, s.text, s.table, s.row, s.column)
        for number, s in enumerate(sentences, start=1)
    ]


def section_texts() -> dict[str, str]:
    """Guideline section id -> section text: the sources of the mutation test's reference sentences.

    It reads corpus/guidelines only, so the committed reference sentences and mutations stay reproducible. To check
    a memo or narrative that may cite lending-program sections (for example [NSP-4.1]), use citable_section_texts().
    """
    return {chunk.chunk_id: chunk.text for chunk in load_guideline_chunks()}


def citable_section_texts() -> dict[str, str]:
    """Every citable section id -> text: the guidelines plus the SYNTHETIC lending programs (corpus/programs).

    This is the source map for checking cited sentences outside the mutation test, such as a founder narrative that
    cites the startup program. A section id may appear only once across both folders.
    """
    texts = section_texts()
    for chunk in load_program_chunks():
        if chunk.chunk_id in texts:
            raise ValueError(f"section id {chunk.chunk_id} appears in a guideline and in a program document")
        texts[chunk.chunk_id] = chunk.text
    return texts
