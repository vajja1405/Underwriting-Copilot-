"""The "numberless memo" grammar and its validator (docs/innovations.md, SIG steps 5-6).

In a provenance-locked memo the LLM writes only prose plus three kinds of token:
- {{claim:ID}}  a value from the evidence ledger (the renderer fills it in, with a provenance footnote);
- {{assert:ID}} a pass/fail judgment from a compiled rule (the renderer fills in "meets the product minimum" etc.);
- [NBC-4.2] / [HM-3.3]  a citation to a guideline section.

The model may not type a digit, a number word, a currency or percent symbol, a comparative judgment (only an
{{assert:}} slot supplies one), or a decision. validate() returns one Violation per problem, with these codes:

- V1_DIGIT: a digit outside slot and citation tokens, including any other Unicode character with a numeric value
  ("½", "²", "Ⅳ", "①").
- V2_NUMBER_WORD: a number word ("twelve", "half", "a million", "fourth", "twofold", "several"); documented
  exceptions: NUMBER_WORD_EXCEPTIONS.
- V3_SYMBOL: one of $ % € £ × ≥ ≤.
- V4_UNKNOWN_SLOT: a well-formed slot whose id is not in the ledger.
- V5_UNSLOTTED_COMPARATIVE: a comparative word ("below", "exceeds", "meets", "bigger", ...) typed by the model, in any
  sentence. The pass/fail wording comes only from the {{assert:}} slot's own phrase, so a sentence with an assert
  slot cannot add a second, possibly contradicting, comparison of its own.
- V6_OMISSION: a required reference (a fired trigger's assertion, or a CONFLICTING or UNKNOWN claim) never appears.
- V7_DISPOSITION_LANGUAGE: decision wording ("is approved", "cleared the alert"), from the KYC memo's and the agent
  claim checker's existing detectors.
- V8_MALFORMED_SLOT: braces that are not a well-formed slot ("{claim:x}", "{{claim:}}", "{{dscr}}").
- V9_UNKNOWN_CITATION: a bracketed section id the case does not reference.
- V10_UNBOUND_ASSERT: in a sentence with an {{assert:}} slot, a claim that is the evidence of a DIFFERENT assertion
  (and of none of the sentence's own), or a citation that is not one of the sentence's assertions' sections. This
  stops a draft from pairing one check's "meets the minimum" with another check's fact. Checked only when the
  caller passes the assertions' evidence and sections (assertion_scopes).

Known limits (closed lists, disclosed rather than hidden): the ordinals "first" and "second" and the superlatives
"most"/"least" are allowed, because ordinary prose needs them; bare "over" and "under" are allowed and only flagged
next to a threshold word ("over the target"); Roman numerals typed in ASCII letters ("IV") are not detected. A claim
slot that points to the wrong claim of the right kind (a rent sentence showing the loan amount) passes the grammar:
the grammar checks which words the model typed, not whether a slot is the right one.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from uwcopilot.agent.claim_checker import decision_language
from uwcopilot.kyc.memo import disposition_language
from uwcopilot.security.normalize import detection_text

ViolationCode = Literal[
    "V1_DIGIT",
    "V2_NUMBER_WORD",
    "V3_SYMBOL",
    "V4_UNKNOWN_SLOT",
    "V5_UNSLOTTED_COMPARATIVE",
    "V6_OMISSION",
    "V7_DISPOSITION_LANGUAGE",
    "V8_MALFORMED_SLOT",
    "V9_UNKNOWN_CITATION",
    "V10_UNBOUND_ASSERT",
]
VIOLATION_CODES: tuple[str, ...] = (
    "V1_DIGIT",
    "V2_NUMBER_WORD",
    "V3_SYMBOL",
    "V4_UNKNOWN_SLOT",
    "V5_UNSLOTTED_COMPARATIVE",
    "V6_OMISSION",
    "V7_DISPOSITION_LANGUAGE",
    "V8_MALFORMED_SLOT",
    "V9_UNKNOWN_CITATION",
    "V10_UNBOUND_ASSERT",
)

SLOT = re.compile(r"\{\{(?P<kind>claim|assert):(?P<id>[A-Za-z][A-Za-z0-9_.\-]*)\}\}")
CITATION = re.compile(r"\[(?P<ids>(?:R-)?[A-Z]{1,5}-\d+(?:\.\d+)?(?:\s*,\s*(?:R-)?[A-Z]{1,5}-\d+(?:\.\d+)?)*)\]")
SECTION_ID = re.compile(r"(?:R-)?[A-Z]{1,5}-\d+(?:\.\d+)?")
# Any brace that is not part of a well-formed slot is malformed.
BRACE = re.compile(r"\{+[^{}]*\}+|\{+|\}+")
DIGIT = re.compile(r"\d+(?:[.,]\d+)*")
SYMBOL = re.compile(r"[$%€£×≥≤]")
NUMBER_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
    "|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|hundreds"
    "|thousand|thousands|million|millions|billion|billions|half|halves|quarter|quarters|third|thirds|twice"
    "|double|doubled|triple|tripled|dozen|dozens|percent|percentage|per cent|pct|bps|basis points?"
    # Ordinals from "third" up ("first" and "second" are allowed: see Known limits in the module docstring).
    "|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth"
    "|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth|fortieth|fiftieth|sixtieth|seventieth"
    "|eightieth|ninetieth|hundredth|thousandth|millionth|billionth"
    # Multipliers and vague counts that still state a quantity.
    "|(?:two|three|four|five|six|seven|eight|nine|ten|hundred|thousand)fold|couple|several|few|handful|single"
)
NUMBER_WORD = re.compile(rf"\b(?:{NUMBER_WORDS})\b|\ba grand\b(?! opening)", re.IGNORECASE)
# Phrases where a number word is not a quantity. Removed before V2 runs.
NUMBER_WORD_EXCEPTIONS = re.compile(
    r"\b(?:no one|someone|anyone|everyone|one another|one of|each one|any one|one or more|this one|that one)\b",
    re.IGNORECASE,
)
COMPARATIVES = re.compile(
    r"\b(?:above|below|exceeds?|exceeded|exceeding|less than|more than|greater than|fewer than|at least|at most"
    r"|minimum|maximum|meets?|met|fails?|failed|higher|lower|short of|surpass(?:es|ed)?|within policy"
    r"|outside policy|insufficient|sufficient|bigger|smaller|larger|greater|better|worse|beats?|beaten"
    r"|outperform(?:s|ed)?|ahead of|behind|trails?|trailed|clears?|cleared|comfortably|narrowly|barely"
    r"|(?:well|just|far|slightly|comfortably) (?:over|under)"
    r"|(?:over|under) (?:the |its |their |this |that )?(?:target|minimum|maximum|threshold|limit|requirement|cap"
    r"|floor|ceiling|policy))\b",
    re.IGNORECASE,
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Violation:
    """One grammar violation: its code, the offending span and a short explanation."""

    code: ViolationCode
    span: str
    detail: str


def blank(text: str, pattern: re.Pattern[str]) -> str:
    """Replace every match of pattern with spaces of the same length (keeps offsets)."""
    return pattern.sub(lambda match: " " * len(match.group(0)), text)


def slot_violations(text: str, claim_ids: set[str], assertion_ids: set[str]) -> list[Violation]:
    """V4 (unknown slot id) and V8 (malformed braces)."""
    violations: list[Violation] = []
    for match in SLOT.finditer(text):
        known = claim_ids if match.group("kind") == "claim" else assertion_ids
        if match.group("id") not in known:
            violations.append(Violation("V4_UNKNOWN_SLOT", match.group(0), f"no {match.group('kind')} with this id"))
    for match in BRACE.finditer(blank(text, SLOT)):
        violations.append(Violation("V8_MALFORMED_SLOT", match.group(0), "braces that are not a well-formed slot"))
    return violations


def citation_violations(text: str, allowed_sections: set[str]) -> list[Violation]:
    """V9: a cited section id that the case does not reference."""
    violations = []
    for match in CITATION.finditer(text):
        for section_id in SECTION_ID.findall(match.group("ids")):
            if section_id not in allowed_sections:
                violations.append(Violation("V9_UNKNOWN_CITATION", f"[{section_id}]", "section not in this case"))
    return violations


def numeric_characters(prose: str) -> list[str]:
    """Non-ASCII characters with a numeric value ("½", "²", "Ⅳ", "①"); ASCII digits are found by DIGIT."""
    return [char for char in prose if not char.isascii() and unicodedata.numeric(char, None) is not None]


def token_violations(prose: str) -> list[Violation]:
    """V1 digits, V2 number words and V3 symbols in the prose (slots and citations already blanked)."""
    violations = [
        Violation("V1_DIGIT", match.group(0), "digits must come from a claim slot") for match in DIGIT.finditer(prose)
    ]
    violations += [
        Violation("V1_DIGIT", char, "numeric characters must come from a claim slot")
        for char in numeric_characters(blank(prose, DIGIT))
    ]
    words = blank(prose, NUMBER_WORD_EXCEPTIONS)
    violations += [
        Violation("V2_NUMBER_WORD", match.group(0), "number words must come from a claim slot")
        for match in NUMBER_WORD.finditer(words)
    ]
    violations += [
        Violation("V3_SYMBOL", match.group(0), "currency and percent symbols come from the renderer")
        for match in SYMBOL.finditer(prose)
    ]
    return violations


def comparative_violations(text: str) -> list[Violation]:
    """V5: a comparative word typed by the model, in any sentence (the assert slot's own phrase is not typed)."""
    prose = blank(blank(text, SLOT), CITATION)
    return [
        Violation("V5_UNSLOTTED_COMPARATIVE", match.group(0), "pass/fail judgments come from an assert slot")
        for match in COMPARATIVES.finditer(prose)
    ]


@dataclass(frozen=True)
class AssertionScope:
    """What one assertion may be stated with: the claims it rests on and the sections it cites."""

    evidence_ids: frozenset[str]
    section_ids: frozenset[str]


def unbound_assert_violations(text: str, scopes: dict[str, AssertionScope]) -> list[Violation]:
    """V10: in a sentence with assert slots, claims owned by another assertion and citations from other sections."""
    owners: dict[str, set[str]] = {}
    for assertion_id, scope in scopes.items():
        for claim_id in scope.evidence_ids:
            owners.setdefault(claim_id, set()).add(assertion_id)
    violations = []
    for sentence in SENTENCE_SPLIT.split(text):
        slots = list(SLOT.finditer(sentence))
        asserted = [
            match.group("id") for match in slots if match.group("kind") == "assert" and match.group("id") in scopes
        ]
        if not asserted:
            continue
        own_claims = set().union(*(scopes[assertion_id].evidence_ids for assertion_id in asserted))
        own_sections = set().union(*(scopes[assertion_id].section_ids for assertion_id in asserted))
        for match in slots:
            claim_id = match.group("id")
            if match.group("kind") == "claim" and claim_id in owners and claim_id not in own_claims:
                violations.append(
                    Violation("V10_UNBOUND_ASSERT", match.group(0), "this claim belongs to a different check")
                )
        if not own_sections:
            continue
        for citation in CITATION.finditer(sentence):
            for section_id in SECTION_ID.findall(citation.group("ids")):
                if section_id not in own_sections:
                    violations.append(
                        Violation("V10_UNBOUND_ASSERT", f"[{section_id}]", "this section is not the check's own")
                    )
    return violations


def omission_violations(text: str, required_refs: list[str]) -> list[Violation]:
    """V6: every required reference ("claim:ID" or "assert:ID") must appear as a slot."""
    present = {f"{match.group('kind')}:{match.group('id')}" for match in SLOT.finditer(text)}
    return [
        Violation("V6_OMISSION", reference, "a fired trigger or an open claim is not mentioned")
        for reference in required_refs
        if reference not in present
    ]


def disposition_violations(text: str) -> list[Violation]:
    """V7: decision or alert-disposition wording, from the existing KYC and agent detectors.

    The prose is normalized first (uwcopilot.security.normalize.detection_text), so wording hidden with zero-width
    characters, lookalike letters, fullwidth forms or Unicode tag characters is found as a reader would see it.
    """
    prose = detection_text(blank(blank(text, SLOT), CITATION))
    phrases = list(dict.fromkeys([*disposition_language(prose), *decision_language(prose)]))
    return [Violation("V7_DISPOSITION_LANGUAGE", phrase, "the memo may not state a decision") for phrase in phrases]


def validate(
    text: str,
    claim_ids: set[str],
    assertion_ids: set[str],
    required_refs: list[str] | None = None,
    allowed_sections: set[str] | None = None,
    assertion_scopes: dict[str, AssertionScope] | None = None,
) -> list[Violation]:
    """Every grammar violation in a drafted memo (empty list = valid). See the module docstring for the codes.

    required_refs entries look like "assert:R-NBC-04" or "claim:stated_annual_revenue". allowed_sections None means
    citations are not checked. assertion_scopes None means V10 is not checked.
    """
    prose = blank(blank(text, SLOT), CITATION)
    violations = slot_violations(text, claim_ids, assertion_ids)
    violations += token_violations(prose)
    violations += comparative_violations(text)
    violations += omission_violations(text, required_refs or [])
    violations += disposition_violations(text)
    if allowed_sections is not None:
        violations += citation_violations(text, allowed_sections)
    if assertion_scopes is not None:
        violations += unbound_assert_violations(text, assertion_scopes)
    return violations
