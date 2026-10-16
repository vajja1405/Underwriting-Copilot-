"""Comparison keys for document values (Phase 2), plus a re-export of the canonical text normalization (S2).

The text normalization for untrusted document text lives in uwcopilot.security.normalize (the S2 handover of
2026-10-14). This module re-exports it unchanged, so the document pipeline and the injection controls use one
implementation: normalize_text() strips the whole Unicode Default_Ignorable_Code_Point set (zero-width characters,
soft hyphen, bidirectional controls, variation selectors, Hangul fillers, the tag characters used for "ASCII
smuggling"), applies NFKC, strips again, folds lookalike letters (HOMOGLYPHS) and angle-bracket lookalikes, and
collapses whitespace. hidden_tag_text() decodes a message hidden in tag characters. See that module's docstring for
the sources of the character tables.

What stays here are the document-consistency helpers, which are not injection controls:
- canonical_name(): a comparison key for business and person names (punctuation, "&"/"and" and company-suffix
  spellings folded);
- canonical_fein(): a comparison key for FEINs, and digits_only() for account numbers;
- edit_distance(): the Levenshtein distance the name checks use.
Nothing here interprets the text; it only makes comparisons fair.
"""

import re

from uwcopilot.security.normalize import (
    HOMOGLYPHS,
    INVISIBLE_CODEPOINTS,
    TAG_FIRST,
    TAG_LAST,
    TAG_PRINTABLE_FIRST,
    TAG_PRINTABLE_LAST,
    TAG_TO_ASCII_OFFSET,
    WHITESPACE_RUN,
    fold_homoglyphs,
    hidden_tag_text,
    is_invisible,
    normalize_text,
    strip_invisible,
)

__all__ = [
    "HOMOGLYPHS",
    "INVISIBLE_CODEPOINTS",
    "TAG_FIRST",
    "TAG_LAST",
    "TAG_PRINTABLE_FIRST",
    "TAG_PRINTABLE_LAST",
    "TAG_TO_ASCII_OFFSET",
    "WHITESPACE_RUN",
    "canonical_fein",
    "canonical_name",
    "digits_only",
    "edit_distance",
    "fold_homoglyphs",
    "hidden_tag_text",
    "is_invisible",
    "normalize_text",
    "strip_invisible",
]

# Punctuation that varies between documents. Periods are deleted ("L.L.C" -> "llc", "Inc." -> "inc"); the other
# characters become spaces.
NAME_PERIOD = "."
NAME_PUNCTUATION = re.compile(r"[,'\"()/\\-]")
# Spellings of the same word or company suffix that documents use interchangeably, folded to one form. Applied to
# whole words after punctuation is removed; the phrase entry is applied first.
NAME_PHRASES: dict[str, str] = {"limited liability company": "llc"}
NAME_WORDS: dict[str, str] = {
    "&": "and",
    "incorporated": "inc",
    "corporation": "corp",
    "company": "co",
    "limited": "ltd",
}


def canonical_name(name: str | None) -> str:
    """A comparison key for a business or person name: normalized, casefolded, without punctuation.

    Steps: normalize_text, casefold, delete periods, turn the other NAME_PUNCTUATION into spaces, fold the
    NAME_PHRASES and NAME_WORDS spellings, collapse spaces. "Harbor Bakery, L.L.C.", "HARBOR BAKERY LLC" and
    "Harbor Bakery Limited Liability Company" give the same key ("harbor bakery llc"); "Smith & Sons Co." and
    "Smith and Sons Company" give "smith and sons co". A one-letter typo still gives a different key, which is
    what the name checks need to see.
    """
    if not name:
        return ""
    text = normalize_text(name).casefold().replace(NAME_PERIOD, "")
    text = NAME_PUNCTUATION.sub(" ", text).replace("&", " & ")
    text = " ".join(text.split())
    for phrase, replacement in NAME_PHRASES.items():
        text = f" {text} ".replace(f" {phrase} ", f" {replacement} ").strip()
    words = [NAME_WORDS.get(word, word) for word in text.split()]
    return " ".join(words)


def edit_distance(first: str, second: str) -> int:
    """Levenshtein distance: the fewest single-character insertions, deletions or substitutions between two texts."""
    previous = list(range(len(second) + 1))
    for row, first_char in enumerate(first, start=1):
        current = [row]
        for column, second_char in enumerate(second, start=1):
            insert = current[column - 1] + 1
            delete = previous[column] + 1
            substitute = previous[column - 1] + (0 if first_char == second_char else 1)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def digits_only(text: str | None) -> str:
    """The ASCII digits of a normalized text, in order ("" for None). Used for account numbers and FEINs."""
    if not text:
        return ""
    return "".join(character for character in normalize_text(text) if character in "0123456789")


def canonical_fein(fein: str | None) -> str:
    """A comparison key for a FEIN: its digits as NN-NNNNNNN when there are nine, otherwise the digits alone."""
    digits = digits_only(fein)
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:]}"
    return digits
