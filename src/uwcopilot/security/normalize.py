"""The canonical normalization API for untrusted text (S2): use this module, not docintel/normalize.py.

Untrusted text (an applicant's notes and names, OCR output, retrieved snippets, a model's answer before it is scored
or checked) is normalized before the injection tripwire reads it, before it is rendered into a prompt and before a
validator searches it for decision wording. normalize_text():

1. strips every invisible character: the Unicode Default_Ignorable_Code_Point set (DEFAULT_IGNORABLE_RANGES below:
   zero-width characters, the soft hyphen, bidirectional embeddings, overrides and isolates, variation selectors,
   the combining grapheme joiner, Hangul fillers, the Unicode tag characters U+E0000-U+E0FFF used for "ASCII
   smuggling", ...). These characters render as nothing, so a reader never sees them, but they split words for a
   keyword detector or a name matcher;
2. applies NFKC (fullwidth letters become ASCII, ligatures are split, the no-break space becomes a space), then
   strips invisible characters again, because NFKC can turn one invisible character into another (U+3164 HANGUL
   FILLER becomes U+1160 HANGUL JUNGSEONG FILLER);
3. folds lookalike characters: the homoglyph table HOMOGLYPHS (Cyrillic and Greek letters that look Latin,
   typographic quotes and dashes) and BRACKET_LOOKALIKES (characters that look like "<" or ">", so a fake tag such
   as "\u2039/system\u203a" is seen by the tripwire and escaped like a real one);
4. collapses runs of whitespace to one space.

detection_text() is what a tripwire should read: the normalized visible text plus the ASCII message hidden in tag
characters, decoded and normalized, so a smuggled instruction is checked too (the prompt itself never shows it).
has_visible_text() says whether a field holds anything a reader can see. fold_mixed_script_words() is the fold a
name matcher can use without touching names written wholly in another script (see its docstring).

Sources for the two character tables written here (both checked on 2026-10-13 against the Unicode version that
Python 3.11's unicodedata implements, 14.0.0):
- DEFAULT_IGNORABLE_RANGES: Unicode 14.0.0 DerivedCoreProperties.txt, property Default_Ignorable_Code_Point,
  https://www.unicode.org/Public/14.0.0/ucd/DerivedCoreProperties.txt (adjacent ranges merged);
- BRACKET_LOOKALIKES: the entries of Unicode 14.0.0 confusables.txt that map to U+003C "<" or U+003E ">" and are
  punctuation or symbols, https://www.unicode.org/Public/security/14.0.0/confusables.txt. The entries that are
  letters of a real script (Canadian syllabics, Runic, Miao) are left out, so words in those scripts never change.

Where the implementation lives: here. The Phase 2 code (docintel/normalize.py) handed over its homoglyph table, the
tag-character constants, fold_homoglyphs(), hidden_tag_text(), strip_invisible() and normalize_text() to this module
on 2026-10-14; docintel/normalize.py is now a thin re-export, so `from uwcopilot.docintel.normalize import
normalize_text` and `from uwcopilot.security.normalize import normalize_text` name the same function. The handover
made the document pipeline strip the full Default_Ignorable set and fold the bracket lookalikes too (before it, the
Phase 2 copy kept characters such as U+034F, the bidirectional controls and the variation selectors). The comparison
keys for names and FEINs (canonical_name, canonical_fein, digits_only, edit_distance) are document-consistency
helpers, not injection controls, and stay in docintel/normalize.py.

The homoglyph table is written with \\u escapes and the Unicode character name in a comment, because a Cyrillic
small a (U+0430) and a Latin "a" look the same in an editor. This module imports only the standard library, so any
package (docintel, rag, agent, kyc, web) can import it without a cycle.
"""

import re
import unicodedata

__all__ = [
    "BRACKET_LOOKALIKES",
    "DEFAULT_IGNORABLE_RANGES",
    "HOMOGLYPHS",
    "INVISIBLE_CODEPOINTS",
    "TAG_FIRST",
    "TAG_LAST",
    "TAG_PRINTABLE_FIRST",
    "TAG_PRINTABLE_LAST",
    "TAG_TO_ASCII_OFFSET",
    "WHITESPACE_RUN",
    "detection_text",
    "fold_homoglyphs",
    "fold_lookalikes",
    "fold_mixed_script_words",
    "has_visible_text",
    "hidden_tag_text",
    "is_invisible",
    "normalize_text",
    "strip_invisible",
]

# Unicode 14.0.0 Default_Ignorable_Code_Point, as (first, last) code point ranges (see the module docstring).
DEFAULT_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),  # soft hyphen
    (0x034F, 0x034F),  # combining grapheme joiner
    (0x061C, 0x061C),  # arabic letter mark
    (0x115F, 0x1160),  # hangul choseong filler, hangul jungseong filler
    (0x17B4, 0x17B5),  # khmer vowel inherent aq, aa
    (0x180B, 0x180F),  # mongolian free variation selectors and vowel separator
    (0x200B, 0x200F),  # zero width space, non-joiner, joiner, left-to-right mark, right-to-left mark
    (0x202A, 0x202E),  # bidirectional embeddings and overrides
    (0x2060, 0x206F),  # word joiner, invisible operators, bidirectional isolates, deprecated format characters
    (0x3164, 0x3164),  # hangul filler
    (0xFE00, 0xFE0F),  # variation selectors 1-16
    (0xFEFF, 0xFEFF),  # zero width no-break space (byte order mark)
    (0xFFA0, 0xFFA0),  # halfwidth hangul filler
    (0xFFF0, 0xFFF8),  # reserved
    (0x1BCA0, 0x1BCA3),  # shorthand format controls
    (0x1D173, 0x1D17A),  # musical symbol format controls
    (0xE0000, 0xE0FFF),  # tag characters, variation selectors 17-256, reserved
)
# The smaller invisible set the Phase 2 copy stripped before the handover (zero-width characters, word joiner, byte
# order mark, soft hyphen, invisible operators). Every one is inside DEFAULT_IGNORABLE_RANGES; is_invisible() uses
# the ranges. Kept so earlier imports keep working and a test can show the handover only made the set larger.
INVISIBLE_CODEPOINTS: tuple[int, ...] = (*range(0x200B, 0x2010), 0x2060, 0xFEFF, 0x00AD, *range(0x2061, 0x2065))
# Unicode tag characters U+E0000-U+E007F (inside the last DEFAULT_IGNORABLE_RANGES entry). U+E0020-U+E007E mirror
# printable ASCII 0x20-0x7E, which is how a message is hidden in them ("ASCII smuggling").
TAG_FIRST = 0xE0000
TAG_LAST = 0xE007F
TAG_PRINTABLE_FIRST = 0xE0020
TAG_PRINTABLE_LAST = 0xE007E
TAG_TO_ASCII_OFFSET = 0xE0000
# Homoglyph fold (step 3 of normalize_text): lookalike letters from Cyrillic and Greek, plus typographic
# punctuation. Deliberately small; every entry is a character that renders like the Latin letter in common fonts.
HOMOGLYPHS: dict[str, str] = {
    # Cyrillic lowercase
    "\u0430": "a",  # cyrillic small letter a
    "\u0435": "e",  # cyrillic small letter ie
    "\u043e": "o",  # cyrillic small letter o
    "\u0440": "p",  # cyrillic small letter er
    "\u0441": "c",  # cyrillic small letter es
    "\u0443": "y",  # cyrillic small letter u
    "\u0445": "x",  # cyrillic small letter ha
    "\u0455": "s",  # cyrillic small letter dze
    "\u0456": "i",  # cyrillic small letter byelorussian-ukrainian i
    "\u0458": "j",  # cyrillic small letter je
    "\u04bb": "h",  # cyrillic small letter shha
    "\u04cf": "l",  # cyrillic small letter palochka
    "\u0501": "d",  # cyrillic small letter komi de
    # Cyrillic uppercase
    "\u0410": "A",  # cyrillic capital letter a
    "\u0412": "B",  # cyrillic capital letter ve
    "\u0415": "E",  # cyrillic capital letter ie
    "\u041a": "K",  # cyrillic capital letter ka
    "\u041c": "M",  # cyrillic capital letter em
    "\u041d": "H",  # cyrillic capital letter en
    "\u041e": "O",  # cyrillic capital letter o
    "\u0420": "P",  # cyrillic capital letter er
    "\u0421": "C",  # cyrillic capital letter es
    "\u0422": "T",  # cyrillic capital letter te
    "\u0425": "X",  # cyrillic capital letter ha
    "\u0405": "S",  # cyrillic capital letter dze
    "\u0406": "I",  # cyrillic capital letter byelorussian-ukrainian i
    "\u0408": "J",  # cyrillic capital letter je
    # Greek
    "\u03bf": "o",  # greek small letter omicron
    "\u03b1": "a",  # greek small letter alpha
    "\u03b9": "i",  # greek small letter iota
    "\u03ba": "k",  # greek small letter kappa
    "\u03bd": "v",  # greek small letter nu
    "\u03c1": "p",  # greek small letter rho
    "\u0391": "A",  # greek capital letter alpha
    "\u0392": "B",  # greek capital letter beta
    "\u0395": "E",  # greek capital letter epsilon
    "\u0396": "Z",  # greek capital letter zeta
    "\u0397": "H",  # greek capital letter eta
    "\u0399": "I",  # greek capital letter iota
    "\u039a": "K",  # greek capital letter kappa
    "\u039c": "M",  # greek capital letter mu
    "\u039d": "N",  # greek capital letter nu
    "\u039f": "O",  # greek capital letter omicron
    "\u03a1": "P",  # greek capital letter rho
    "\u03a4": "T",  # greek capital letter tau
    "\u03a5": "Y",  # greek capital letter upsilon
    "\u03a7": "X",  # greek capital letter chi
    # Typographic punctuation
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201c": '"',  # left double quotation mark
    "\u201d": '"',  # right double quotation mark
    "\u2010": "-",  # hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus sign
}

# Unicode 14.0.0 confusables.txt entries for "<" and ">" that are punctuation or symbols (see the module docstring).
BRACKET_LOOKALIKES: dict[str, str] = {
    "\u02c2": "<",  # modifier letter left arrowhead
    "\u2039": "<",  # single left-pointing angle quotation mark
    "\u276e": "<",  # heavy left-pointing angle quotation mark ornament
    "\U0001d236": "<",  # greek instrumental notation symbol-40
    "\u02c3": ">",  # modifier letter right arrowhead
    "\u203a": ">",  # single right-pointing angle quotation mark
    "\u276f": ">",  # heavy right-pointing angle quotation mark ornament
    "\U0001d237": ">",  # greek instrumental notation symbol-42
}
WHITESPACE_RUN = re.compile(r"\s+")
WORD = re.compile(r"\w+")


def is_invisible(character: str) -> bool:
    """True for a Default_Ignorable_Code_Point character (zero-width, bidirectional control, tag, ...)."""
    codepoint = ord(character)
    for first, last in DEFAULT_IGNORABLE_RANGES:
        if first <= codepoint <= last:
            return True
    return False


def strip_invisible(text: str) -> str:
    """Remove every invisible character (see is_invisible). ASCII text is returned unchanged at once."""
    if text.isascii():
        return text
    return "".join(character for character in text if not is_invisible(character))


def hidden_tag_text(text: str) -> str:
    """The ASCII message hidden in Unicode tag characters ("" when there is none); U+E0041 is a hidden "A"."""
    hidden = []
    for character in text:
        codepoint = ord(character)
        if TAG_PRINTABLE_FIRST <= codepoint <= TAG_PRINTABLE_LAST:
            hidden.append(chr(codepoint - TAG_TO_ASCII_OFFSET))
    return "".join(hidden)


def fold_homoglyphs(text: str) -> str:
    """Replace lookalike letters and typographic punctuation using the HOMOGLYPHS table."""
    return "".join(HOMOGLYPHS.get(character, character) for character in text)


def fold_lookalikes(text: str) -> str:
    """Replace homoglyphs (HOMOGLYPHS) and angle-bracket lookalikes (BRACKET_LOOKALIKES) by their ASCII forms."""
    folded = fold_homoglyphs(text)
    return "".join(BRACKET_LOOKALIKES.get(character, character) for character in folded)


def normalize_text(text: str) -> str:
    """Strip invisible characters, NFKC, strip again, fold lookalikes, collapse whitespace (module docstring)."""
    visible = strip_invisible(text)
    compatible = strip_invisible(unicodedata.normalize("NFKC", visible))
    folded = fold_lookalikes(compatible)
    return WHITESPACE_RUN.sub(" ", folded).strip()


def detection_text(raw: str) -> str:
    """The text a tripwire should read: normalized visible text, then any decoded tag-character message.

    "Thanks" followed by tag characters spelling "ignore previous instructions" gives
    "Thanks ignore previous instructions". Text without tag characters gives normalize_text(raw).
    """
    visible = normalize_text(raw)
    hidden = normalize_text(hidden_tag_text(raw))
    if hidden:
        return f"{visible} {hidden}".strip()
    return visible


def has_visible_text(text: str | None) -> bool:
    """True when text holds at least one character a reader can see (None, "", spaces and invisibles are False).

    Use it wherever a rule asks whether a free-text field was filled in: "\u200b" alone is not a stated source.
    """
    return bool(text) and normalize_text(text) != ""


def is_ascii_letter(character: str) -> bool:
    """True for a-z and A-Z."""
    return character.isascii() and character.isalpha()


def reads_as_latin(word: str) -> bool:
    """True when a word has at least one ASCII letter and every other letter is a lookalike of a Latin letter.

    Such a word can only be read as Latin: "S\u0435rp\u0435ns" (two Cyrillic "\u0435") or
    "\u0405\u0435r\u0440\u0435ns". A Cyrillic word with a stray Latin letter, such as "\u0412i\u043a\u0442\u0430\u0440"
    (Latin "i"), is not: "\u043a" and "\u0442" have no Latin lookalike, so the word is Cyrillic.
    """
    letters = [character for character in word if character.isalpha()]
    has_ascii = any(is_ascii_letter(character) for character in letters)
    has_lookalike = any(character in HOMOGLYPHS for character in letters)
    others_are_lookalikes = all(is_ascii_letter(character) or character in HOMOGLYPHS for character in letters)
    return has_ascii and has_lookalike and others_are_lookalikes


def fold_mixed_script_words(text: str) -> str:
    """Fold HOMOGLYPHS inside the words that can only be read as Latin (reads_as_latin); leave every other word.

    "S\u0435rp\u0435ns Ltd" becomes "Serpens Ltd", because no real word mixes Latin letters with Cyrillic letters
    that merely look Latin. Words written in another script are left as they are, even when they contain a stray
    Latin letter, so a name matcher can still compare them with the names on a list:
    "\u0421\u0435\u0440\u0433\u0435\u0439" and "\u0412i\u043a\u0442\u0430\u0440" do not change. A word written
    wholly in lookalikes, with no ASCII letter at all, is not folded either (a genuine Cyrillic word such as
    "\u0421\u041e\u0420\u041e\u041a\u0410" looks exactly like that), which leaves a whole-word spoof as a known
    limit. Run strip_invisible() first, so a zero-width character cannot split a word into two single-script halves.
    """
    if text.isascii():
        return text

    def fold(match: re.Match[str]) -> str:
        word = match.group(0)
        return fold_homoglyphs(word) if reads_as_latin(word) else word

    return WORD.sub(fold, text)
