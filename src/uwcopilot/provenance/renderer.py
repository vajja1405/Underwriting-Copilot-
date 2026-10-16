"""Deterministic renderer for provenance-locked memos (docs/innovations.md, SIG step 8).

The LLM writes prose with {{claim:ID}} and {{assert:ID}} slots. This renderer replaces each slot:
- a claim slot becomes the ledger value in the claim's display format, followed by a footnote marker;
- an assert slot becomes the rule's phrase for its result (true / false / unknown).
Footnotes list where every value came from: the calculation, document page or model, and a short hash of its inputs.

Because every figure in the rendered memo is typed by this code from the ledger, the round-trip audit (re-extract
every number from the rendered text and find it in the ledger) passes by construction. It is a property test of the
design, not an empirical finding about the model.

The ledger is anything that matches LedgerLike: Phase 4's evidence ledger implements it, and SimpleLedger below is a
minimal implementation for tests.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from uwcopilot.provenance.memo_grammar import SLOT
from uwcopilot.provenance.numbers import NumberMention, extract_numbers, same_quantity

DISPLAY_FORMATS: tuple[str, ...] = ("usd0", "usd2", "ratio2", "ratio2x", "pct1", "int", "months", "years", "text")
UNKNOWN_TEXT = "not available"
# How a claim's status shows next to its value. VERIFIED prints the value alone. USER_STATED and ASSUMPTION are the
# Founder Studio statuses (a founder's own figure; a labeled planning assumption), shared with the Phase 4 ledger.
STATUS_SUFFIX = {
    "CONFLICTING": " (conflicting sources)",
    "MODEL_ESTIMATE": " (model estimate)",
    "USER_STATED": " (stated by the applicant)",
    "ASSUMPTION": " (assumption)",
}
# An UNKNOWN claim carries a value only when that value was imputed (filled in, not read). The Phase 4 lender
# ledger keeps imputed values out of value, but any other ledger that prints one gets this marker.
IMPUTED_SUFFIX = " (imputed, not read from the documents)"
FOOTNOTE_LINE = re.compile(r"^\[\^[a-z]\d+\]:.*$", re.MULTILINE)
FOOTNOTE_MARKER = re.compile(r"\[\^[a-z]\d+\]")


class ClaimLike(Protocol):
    """What the renderer needs from one ledger claim."""

    id: str
    label: str
    value: Any
    display: str
    status: str

    def provenance_note(self) -> str:
        """One line saying where the value came from (calculation, document page, model)."""
        ...


class AssertionLike(Protocol):
    """What the renderer needs from one ledger assertion."""

    id: str
    result: bool | None
    phrase_true: str
    phrase_false: str
    phrase_unknown: str


class LedgerLike(Protocol):
    """A ledger: claims and assertions by id."""

    def claim(self, claim_id: str) -> ClaimLike | None:
        """The claim with this id, or None."""
        ...

    def assertion(self, assertion_id: str) -> AssertionLike | None:
        """The assertion with this id, or None."""
        ...

    def claim_values(self) -> list[ClaimLike]:
        """Every claim (used by the round-trip audit)."""
        ...


def format_value(value: Any, display: str) -> str:
    """A ledger value in its display format: usd0 '$1,234,567', pct1 '12.5%', ratio2 '1.25', months '12 months'."""
    if value is None:
        return UNKNOWN_TEXT
    if display == "usd0":
        return f"${float(value):,.0f}"
    if display == "usd2":
        return f"${float(value):,.2f}"
    if display == "ratio2":
        return f"{float(value):.2f}"
    if display == "ratio2x":
        return f"{float(value):.2f}x"
    if display == "pct1":
        return f"{float(value):.1f}%"
    if display == "int":
        return f"{int(round(float(value))):,}"
    if display == "months":
        return f"{int(round(float(value)))} months"
    if display == "years":
        return f"{int(round(float(value)))} years"
    return str(value)


@dataclass
class RenderedMemo:
    """The rendered memo body, its footnotes, and the claim ids used (in order)."""

    body: str
    footnotes: list[str] = field(default_factory=list)
    claims_used: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Body plus footnotes, as shown to the reviewer."""
        return self.body + ("\n\n" + "\n".join(self.footnotes) if self.footnotes else "")


class UnknownSlotError(KeyError):
    """A slot names an id the ledger does not have. Validate the draft before rendering."""


def render(text: str, ledger: LedgerLike) -> RenderedMemo:
    """Fill every slot from the ledger and add one footnote per distinct claim."""
    memo = RenderedMemo(body="")
    footnote_numbers: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        kind, slot_id = match.group("kind"), match.group("id")
        if kind == "assert":
            assertion = ledger.assertion(slot_id)
            if assertion is None:
                raise UnknownSlotError(f"assert:{slot_id}")
            if assertion.result is True:
                return assertion.phrase_true
            if assertion.result is False:
                return assertion.phrase_false
            return assertion.phrase_unknown
        claim = ledger.claim(slot_id)
        if claim is None:
            raise UnknownSlotError(f"claim:{slot_id}")
        if slot_id not in footnote_numbers:
            footnote_numbers[slot_id] = len(footnote_numbers) + 1
            memo.claims_used.append(slot_id)
            memo.footnotes.append(f"[^c{footnote_numbers[slot_id]}]: {claim.label}: {claim.provenance_note()}")
        suffix = STATUS_SUFFIX.get(claim.status, "")
        if claim.status == "UNKNOWN" and claim.value is not None:
            suffix = IMPUTED_SUFFIX
        shown = format_value(claim.value, claim.display) + suffix
        return f"{shown}[^c{footnote_numbers[slot_id]}]"

    memo.body = SLOT.sub(replace, text)
    return memo


def display_mention(claim: ClaimLike) -> NumberMention | None:
    """The number as the renderer prints it for this claim (None when the claim prints no number)."""
    if claim.value is None or claim.display == "text":
        return None
    mentions = extract_numbers(format_value(claim.value, claim.display))
    return mentions[0] if mentions else None


def round_trip_audit(memo: RenderedMemo, ledger: LedgerLike) -> list[str]:
    """Numbers in the rendered body that do not equal any ledger claim in its display format (should be empty).

    Footnote lines and markers are removed first: they hold hashes and references, not claims.
    """
    body = FOOTNOTE_MARKER.sub(" ", FOOTNOTE_LINE.sub(" ", memo.body))
    shown = [mention for claim in ledger.claim_values() if (mention := display_mention(claim)) is not None]
    return [mention.text for mention in extract_numbers(body) if not any(same_quantity(mention, s) for s in shown)]


# --------------------------------------------------------------------------------------------------------------
# A minimal ledger for tests and examples. Phase 4's evidence ledger is the real one.
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SimpleClaim:
    """A claim with a value, a display format and a one-line provenance."""

    id: str
    label: str
    value: Any
    display: str
    status: str = "VERIFIED"
    provenance: str = "calculation"

    def provenance_note(self) -> str:
        """Where the value came from."""
        return self.provenance


@dataclass(frozen=True)
class SimpleAssertion:
    """A rule result with its three phrases."""

    id: str
    result: bool | None
    phrase_true: str
    phrase_false: str
    phrase_unknown: str = "could not be evaluated"


@dataclass
class SimpleLedger:
    """Claims and assertions kept in dictionaries."""

    claims: dict[str, SimpleClaim] = field(default_factory=dict)
    assertions: dict[str, SimpleAssertion] = field(default_factory=dict)

    def claim(self, claim_id: str) -> SimpleClaim | None:
        """The claim with this id, or None."""
        return self.claims.get(claim_id)

    def assertion(self, assertion_id: str) -> SimpleAssertion | None:
        """The assertion with this id, or None."""
        return self.assertions.get(assertion_id)

    def claim_values(self) -> list[SimpleClaim]:
        """Every claim."""
        return list(self.claims.values())
