"""Name normalization for sanctions screening.

Screening compares a customer's name with watchlist names written by many different people, in different
alphabets and conventions. Before any comparison every name goes through the same steps:

1. Unicode NFKD decomposition, then accents (combining marks) are dropped: "José" -> "Jose".
2. casefold() instead of lower(), so "STRASSE" and "Straße" compare equal.
3. Apostrophes are removed ("O'Brien" -> "obrien"); every other character that is not a letter or digit
   becomes a space ("AL-HASSAN" -> "al hassan", "CO., LTD." -> "co ltd").
4. Honorifics and generational suffixes are stripped from the ends of person names ("Dr.", "Sheikh", "Jr.").
5. For every other name, dotted abbreviations are joined first ("S.A." -> "sa", "L.L.C." -> "llc") and then
   legal forms are stripped from the ends ("LLC", "Ltd", "GmbH", "S.A.", "OOO" at the start, and spelled-out
   forms such as "Joint Stock Company").

Letters from other scripts (Cyrillic, Arabic, Chinese) are kept as they are; they simply do not match Latin
names. Normalization never invents tokens, it only removes them, and it records what it removed so an alert
can explain why "Dr. John Smith Jr." matched "SMITH, John".

Step 0, for the name being screened only (added after the S2 review). A name typed by a customer or an applicant is
untrusted text, so normalize_query_name() first passes a non-ASCII name through screening_text(): invisible
characters are removed (a zero-width space or a soft hyphen inside "Ser\u200bpens" would otherwise split it into
"ser" and "pens" and miss the listed "Serpens"), NFKC is applied, and lookalike letters are folded inside words that
can only be read as Latin ("S\u0435rp\u0435ns" with two Cyrillic "\u0435" becomes "Serpens"). Words written in
another script are left as they are, ASCII names are not touched at all, and uwcopilot.security.normalize holds the
character tables. List names do not go through step 0: they come from the list publishers, not from the applicant,
and keeping them as they were keeps the blocking index and every measured KYC result unchanged. (Folding them too
would change the tokens of some OpenSanctions names that mix Latin letters with Cyrillic lookalikes; that is a
matcher change to evaluate on its own, not part of this fix.)
"""

import re
import unicodedata
from dataclasses import dataclass

from uwcopilot.security.normalize import fold_mixed_script_words, strip_invisible

# Titles and honorifics, stripped only from the START of a person name.
HONORIFICS: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "miss",
        "mx",
        "dr",
        "prof",
        "professor",
        "sir",
        "dame",
        "madam",
        "sheikh",
        "shaikh",
        "sheik",
        "haji",
        "hajji",
        "alhaji",
        "mullah",
        "imam",
        "general",
        "gen",
        "colonel",
        "col",
        "captain",
        "capt",
        "major",
        "maj",
        "lieutenant",
        "lt",
        "sgt",
        "rev",
        "reverend",
    }
)
# Generational and professional suffixes, stripped only from the END of a person name.
PERSON_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "md", "dds", "dvm", "esq", "cpa"})
# Legal-form words, stripped from the END of an organization name ("anglo caribbean co ltd" -> "anglo caribbean").
LEGAL_FORM_SUFFIXES: frozenset[str] = frozenset(
    {
        "llc",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "mbh",
        "ag",
        "kg",
        "sa",
        "sarl",
        "srl",
        "spa",
        "bv",
        "nv",
        "plc",
        "llp",
        "lp",
        "pte",
        "pty",
        "pvt",
        "fze",
        "fzco",
        "fzc",
        "fzllc",
        "jsc",
        "pjsc",
        "ojsc",
        "cjsc",
        "oao",
        "zao",
        "ooo",
        "pao",
        "ao",
        "tov",
        "sas",
        "kft",
        "doo",
        "dooel",
        "as",
        "ab",
        "oy",
        "sl",
    }
)
# Legal-form words that usually come FIRST (Russian/Ukrainian/Indonesian style: "OOO Romashka", "PT Bank X").
LEGAL_FORM_PREFIXES: frozenset[str] = frozenset(
    {"ooo", "oao", "zao", "pao", "ao", "jsc", "pjsc", "ojsc", "cjsc", "tov", "pt", "llc"}
)
# Spelled-out legal forms (frequent in English translations of Russian and Ukrainian company names), stripped
# as whole phrases from the start or the end: "Public Joint Stock Company Priboy" -> "priboy".
LEGAL_FORM_PHRASES: tuple[tuple[str, ...], ...] = (
    ("public", "joint", "stock", "company"),
    ("closed", "joint", "stock", "company"),
    ("open", "joint", "stock", "company"),
    ("joint", "stock", "company"),
    ("limited", "liability", "company"),
    ("limited", "liability", "partnership"),
)

# Straight and curly apostrophes, backtick and acute accent: removed so "O'Brien" becomes "obrien".
APOSTROPHES = "'\u2019\u2018`\u00b4"
# Two or more single letters each followed by a dot, with no spaces: "S.A.", "L.L.C.", "S.p.A.".
DOTTED_ABBREVIATION = re.compile(r"\b(?:\w\.){2,}", flags=re.UNICODE)
NON_ALPHANUMERIC = re.compile(r"[^\w]|_", flags=re.UNICODE)
WHITESPACE = re.compile(r"\s+")

PERSON = "person"
ORGANIZATION = "organization"


@dataclass(frozen=True)
class NormalizedName:
    """A name after normalization.

    original: the name exactly as given.
    tokens: the normalized tokens, in their original order.
    removed: tokens that were stripped (honorifics, suffixes, legal forms), for explanations.
    """

    original: str
    tokens: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def text(self) -> str:
        """The normalized tokens joined with single spaces."""
        return " ".join(self.tokens)

    @property
    def sorted_key(self) -> str:
        """Tokens in alphabetical order, so "Hassan Ali" and "Ali Hassan" give the same key."""
        return " ".join(sorted(self.tokens))


def screening_text(name: str) -> str:
    """Step 0 of the module docstring: invisible characters removed, NFKC, mixed-script lookalikes folded.

    ASCII names are returned unchanged at once (nothing to remove or fold; this matters for millions of list names).
    """
    if name.isascii():
        return name
    compatible = unicodedata.normalize("NFKC", strip_invisible(name))
    return fold_mixed_script_words(strip_invisible(compatible))


def strip_accents(text: str) -> str:
    """Decompose characters (NFKD) and drop the combining marks, so "é" becomes "e"."""
    if text.isascii():
        return text  # nothing to decompose; this shortcut matters for millions of list names
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def join_dotted_abbreviations(text: str) -> str:
    """Remove the dots inside abbreviations such as "S.A." or "L.L.C." so they become one token."""
    return DOTTED_ABBREVIATION.sub(lambda match: match.group(0).replace(".", "") + " ", text)


def basic_tokens(text: str) -> list[str]:
    """Unicode-fold, casefold, and split a string into alphanumeric tokens (no stripping of any words)."""
    folded = strip_accents(text).casefold()
    for apostrophe in APOSTROPHES:
        folded = folded.replace(apostrophe, "")
    spaced = NON_ALPHANUMERIC.sub(" ", folded)
    return [token for token in WHITESPACE.split(spaced) if token]


def strip_leading(tokens: list[str], words: frozenset[str], removed: list[str]) -> list[str]:
    """Remove tokens from the start while they are in `words`, but never remove every token."""
    start = 0
    while start < len(tokens) - 1 and tokens[start] in words:
        removed.append(tokens[start])
        start += 1
    return tokens[start:]


def strip_trailing(tokens: list[str], words: frozenset[str], removed: list[str]) -> list[str]:
    """Remove tokens from the end while they are in `words`, but never remove every token."""
    end = len(tokens)
    while end > 1 and tokens[end - 1] in words:
        removed.append(tokens[end - 1])
        end -= 1
    return tokens[:end]


def strip_phrases(tokens: list[str], phrases: tuple[tuple[str, ...], ...], removed: list[str]) -> list[str]:
    """Remove one listed multi-word phrase from the start and one from the end, if the name has other tokens."""
    for phrase in phrases:
        size = len(phrase)
        if len(tokens) > size and tuple(tokens[:size]) == phrase:
            removed.extend(phrase)
            tokens = tokens[size:]
            break
    for phrase in phrases:
        size = len(phrase)
        if len(tokens) > size and tuple(tokens[-size:]) == phrase:
            removed.extend(phrase)
            tokens = tokens[:-size]
            break
    return tokens


def normalize_name(name: str, entity_type: str | None = None) -> NormalizedName:
    """Normalize a person or organization name.

    entity_type decides which words are stripped:
    - "person": leading honorifics and trailing generational/professional suffixes. Dotted initials stay
      separate ("J.R. Smith" -> "j r smith").
    - anything else ("organization", "vessel", "other", None): dotted abbreviations are joined, then leading
      and trailing legal forms are stripped. Honorifics are not stripped here, because "General" or "Major"
      at the start of a company name is part of the name.
    A name made only of strippable words (for example "Ltd") keeps its last token.
    """
    removed: list[str] = []
    if entity_type == PERSON:
        tokens = basic_tokens(name)
        tokens = strip_leading(tokens, HONORIFICS, removed)
        tokens = strip_trailing(tokens, PERSON_SUFFIXES, removed)
    else:
        tokens = basic_tokens(join_dotted_abbreviations(name))
        tokens = strip_phrases(tokens, LEGAL_FORM_PHRASES, removed)
        tokens = strip_leading(tokens, LEGAL_FORM_PREFIXES, removed)
        tokens = strip_trailing(tokens, LEGAL_FORM_SUFFIXES, removed)
    return NormalizedName(original=name, tokens=tuple(tokens), removed=tuple(removed))


def normalize_query_name(name: str, entity_type: str | None = None) -> NormalizedName:
    """normalize_name for the name being SCREENED (typed by a customer or an applicant): step 0 runs first.

    original keeps the name exactly as given. List names are not passed through step 0 (see the module docstring).
    """
    cleaned = normalize_name(screening_text(name), entity_type)
    return NormalizedName(original=name, tokens=cleaned.tokens, removed=cleaned.removed)


def is_initial(token: str) -> bool:
    """True for a one-character token such as the "j" in "J. Smith"."""
    return len(token) == 1


def is_latin_token(token: str) -> bool:
    """True when every character is an ASCII letter or digit (after accent stripping)."""
    return token.isascii() and token.isalnum()
