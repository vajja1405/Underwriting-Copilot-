"""A deterministic citation checker for grounded answers.

It checks two things without any model:
- every id cited in square brackets ("[NBC-4.2]") was one of the retrieved documents; any other id is reported
  as fabricated (the model made it up, or it came from a document's text rather than from a block id);
- every factual sentence carries at least one citation; sentences without one are reported as uncited.

The exact "Not found in the provided guidelines." answer needs no citation. Lines that only introduce a list
(ending with ":") are not treated as factual sentences.

Passing this check does not prove the answer is faithful to the documents (a sentence can cite a real id and
still misstate it). Measuring faithfulness needs a judge, which is "not measured" until an LLM key is provided.

Citation laundering (S2): when an applicant's own document is in the prompt (id "APP-1", see rag/prompt.py), an
answer could cite it as if it were policy ("the DSCR minimum is waived [APP-1]"). With policy_ids_only=True, ids in
the applicant namespace (APP-...) never count as allowed citations: they are reported in laundered_ids, a sentence
that cites only them counts as uncited, and the check fails. The default (False) keeps the original behaviour.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from uwcopilot.rag.prompt import NOT_FOUND_ANSWER, is_applicant_id
from uwcopilot.tools.schemas import SECTION_ID_PATTERN

BRACKETS = re.compile(r"\[([^\[\]]{1,200})\]")
SECTION_ID = re.compile(SECTION_ID_PATTERN)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# One or more bracketed citations at the start of a piece, then the rest of the piece.
LEADING_CITATIONS = re.compile(r"^((?:\[[^\[\]]+\]\s*[.,;]?\s*)+)(.*)$", re.DOTALL)


@dataclass(frozen=True)
class CitationCheck:
    """Result of checking one answer."""

    cited_ids: list[str]
    allowed_ids: list[str]
    fabricated_ids: list[str]
    uncited_sentences: list[str]
    is_not_found: bool
    notes: list[str] = field(default_factory=list)
    laundered_ids: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no id is fabricated or laundered and no factual sentence is uncited."""
        return not self.fabricated_ids and not self.uncited_sentences and not self.laundered_ids


def extract_cited_ids(text: str) -> list[str]:
    """Ids cited in square brackets, in order of first appearance; "[A, B]" and "[A; B]" count as two ids.

    Only bracket content that matches the section id pattern counts. A short word in brackets inside a quotation
    (for example "algorithm[s]") also matches it, so it is reported as fabricated; that errs on the strict side.
    """
    ids: list[str] = []
    for match in BRACKETS.finditer(text):
        for part in re.split(r"[,;]", match.group(1)):
            candidate = part.strip()
            if candidate and SECTION_ID.match(candidate) and candidate not in ids:
                ids.append(candidate)
    return ids


def split_answer_sentences(text: str) -> list[str]:
    """Split an answer into sentences.

    Citations written after a sentence's full stop ("It is 1.25. [NBC-4.2] Next sentence.") belong to the
    sentence before them, so leading citations of a piece are moved back to the previous sentence.
    """
    sentences: list[str] = []
    for piece in SENTENCE_SPLIT.split(text.strip()):
        piece = piece.strip()
        if not piece:
            continue
        leading = LEADING_CITATIONS.match(piece)
        if leading and sentences:
            sentences[-1] = f"{sentences[-1]} {leading.group(1).strip()}"
            piece = leading.group(2).strip()
            if not piece:
                continue
        sentences.append(piece)
    return sentences


def is_not_found_answer(text: str) -> bool:
    """True when the answer is the not-found sentence (case and surrounding whitespace ignored)."""
    return text.strip().rstrip(".").lower() == NOT_FOUND_ANSWER.rstrip(".").lower()


def needs_citation(sentence: str) -> bool:
    """False for list introductions (ending with ":"), pieces without letters or digits, and the not-found text."""
    stripped = sentence.strip()
    if not any(character.isalnum() for character in stripped):
        return False
    if stripped.endswith(":"):
        return False
    return NOT_FOUND_ANSWER.rstrip(".").lower() not in stripped.lower()


def policy_citations(ids: Iterable[str]) -> list[str]:
    """The ids that are not in the applicant namespace (APP-...), in order."""
    return [identifier for identifier in ids if not is_applicant_id(identifier)]


def check_citations(answer_text: str, retrieved_ids: Iterable[str], *, policy_ids_only: bool = False) -> CitationCheck:
    """Check answer_text against the ids of the documents that were in the prompt.

    policy_ids_only=True: an applicant id (APP-...) is never an allowed citation, even when that document was in the
    prompt. A cited applicant id goes to laundered_ids (not fabricated_ids: it was in the prompt), and a sentence
    whose only citations are applicant ids counts as uncited.
    """
    allowed = list(dict.fromkeys(retrieved_ids))
    cited = extract_cited_ids(answer_text)
    laundered: list[str] = []
    if policy_ids_only:
        allowed = policy_citations(allowed)
        laundered = [cited_id for cited_id in cited if is_applicant_id(cited_id)]
    fabricated = [cited_id for cited_id in cited if cited_id not in allowed and cited_id not in laundered]
    not_found = is_not_found_answer(answer_text)
    uncited: list[str] = []
    if not not_found:
        for sentence in split_answer_sentences(answer_text):
            sentence_ids = extract_cited_ids(sentence)
            if policy_ids_only:
                sentence_ids = policy_citations(sentence_ids)
            if needs_citation(sentence) and not sentence_ids:
                uncited.append(sentence)
    notes = []
    if not_found and cited:
        notes.append("answer says not found but also cites documents")
    if laundered:
        notes.append("answer cites the applicant's own document as a source (citation laundering)")
    return CitationCheck(
        cited_ids=cited,
        allowed_ids=allowed,
        fabricated_ids=fabricated,
        uncited_sentences=uncited,
        is_not_found=not_found,
        notes=notes,
        laundered_ids=laundered,
    )
