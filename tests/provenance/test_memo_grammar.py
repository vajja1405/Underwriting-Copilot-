"""One test per validator code of the numberless memo grammar, plus the documented exceptions."""

import pytest

from uwcopilot.provenance.memo_grammar import VIOLATION_CODES, AssertionScope, validate

CLAIMS = {"verified_annual_revenue", "dscr", "revenue_gap_pct"}
ASSERTIONS = {"R-NBC-04", "R-NBC-02"}
SECTIONS = {"NBC-3.2", "NBC-3.4", "NBC-4.2"}


# R-NBC-04 rests on the DSCR and cites NBC-4.2; R-NBC-02 rests on the revenue gap and cites NBC-3.4.
SCOPES = {
    "R-NBC-04": AssertionScope(evidence_ids=frozenset({"dscr"}), section_ids=frozenset({"NBC-4.2"})),
    "R-NBC-02": AssertionScope(evidence_ids=frozenset({"revenue_gap_pct"}), section_ids=frozenset({"NBC-3.4"})),
}


def codes(text: str, required: list[str] | None = None) -> set[str]:
    """The violation codes the validator reports for text."""
    return {violation.code for violation in validate(text, CLAIMS, ASSERTIONS, required or [], SECTIONS, SCOPES)}


@pytest.mark.parametrize(
    ("text", "required", "expected"),
    [
        ("Revenue is 1.2 million [NBC-3.2].", [], "V1_DIGIT"),
        ("Revenue is about twelve months old [NBC-3.2].", [], "V2_NUMBER_WORD"),
        ("Revenue in $ terms is {{claim:verified_annual_revenue}} [NBC-3.2].", [], "V3_SYMBOL"),
        ("Coverage is {{claim:global_dscr}} [NBC-4.2].", [], "V4_UNKNOWN_SLOT"),
        ("Coverage of {{claim:dscr}} is below the minimum [NBC-4.2].", [], "V5_UNSLOTTED_COMPARATIVE"),
        ("Coverage is {{claim:dscr}} [NBC-4.2].", ["assert:R-NBC-04"], "V6_OMISSION"),
        ("The loan is approved [NBC-4.2].", [], "V7_DISPOSITION_LANGUAGE"),
        ("Coverage is {claim:dscr} [NBC-4.2].", [], "V8_MALFORMED_SLOT"),
        ("Coverage is {{claim:dscr}} [NBC-7.1].", [], "V9_UNKNOWN_CITATION"),
        ("The gap of {{claim:revenue_gap_pct}} {{assert:R-NBC-04}} [NBC-4.2].", [], "V10_UNBOUND_ASSERT"),
    ],
)
def test_each_code(text: str, required: list[str], expected: str) -> None:
    """Each violation code fires on a sentence built to break exactly that rule."""
    assert expected in codes(text, required)


def test_every_code_is_covered_by_a_test() -> None:
    """The parametrized cases above cover all ten codes."""
    assert len(VIOLATION_CODES) == 10


def test_valid_memo_passes() -> None:
    """Prose with slots and allowed citations is valid, including the required references."""
    text = (
        "Verified revenue is {{claim:verified_annual_revenue}} [NBC-3.2]. The decision DSCR of {{claim:dscr}} "
        "{{assert:R-NBC-04}} [NBC-4.2]. The gap is {{claim:revenue_gap_pct}} and it {{assert:R-NBC-02}} [NBC-3.4]."
    )
    assert codes(text, ["assert:R-NBC-04", "claim:revenue_gap_pct"]) == set()


def test_number_word_exceptions() -> None:
    """'no one', 'one of', 'someone' are not quantities."""
    assert codes("No one has reviewed it; one of the owners signed; someone will call [NBC-3.2].") == set()


def test_a_comparative_is_rejected_even_next_to_an_assert_slot() -> None:
    """Only the assert slot's own phrase may judge: a typed comparative could contradict it."""
    assert "V5_UNSLOTTED_COMPARATIVE" in codes(
        "The DSCR {{claim:dscr}} is comfortably above the minimum, so it {{assert:R-NBC-04}} [NBC-4.2]."
    )


@pytest.mark.parametrize(
    "word",
    ["fourth", "eighteenth", "twofold", "several", "a few", "a couple", "a single", "per cent", "a grand"],
)
def test_wider_number_words_are_rejected(word: str) -> None:
    """Ordinals from 'third' up, multipliers and vague counts state a quantity the ledger did not supply."""
    assert "V2_NUMBER_WORD" in codes(f"The plan has {word} locations [NBC-3.2].")


@pytest.mark.parametrize("char", ["½", "²", "Ⅳ", "①"])
def test_unicode_numeric_characters_are_rejected(char: str) -> None:
    """Characters with a numeric value are digits by another name."""
    assert "V1_DIGIT" in codes(f"Revenue grew by {char} [NBC-3.2].")


@pytest.mark.parametrize(
    "phrase", ["is bigger than", "beats", "is well over", "is under the target", "is ahead of", "clears"]
)
def test_wider_comparatives_are_rejected(phrase: str) -> None:
    """Comparatives beyond the original list."""
    assert "V5_UNSLOTTED_COMPARATIVE" in codes(f"Coverage of {{{{claim:dscr}}}} {phrase} the lender's view [NBC-4.2].")


def test_documented_allowances_still_pass() -> None:
    """'first', 'second', 'most', and a bare 'over' or 'under' are allowed (module docstring, Known limits)."""
    text = "Over the first and second year, the input that moves income the most is demand, under the plan [NBC-3.2]."
    assert codes(text) == set()


def test_an_assert_slot_with_its_own_claim_and_section_passes() -> None:
    """The binding rule allows a sentence stated with the check's own evidence and section."""
    assert codes("The DSCR of {{claim:dscr}} {{assert:R-NBC-04}} [NBC-4.2].") == set()


def test_an_assert_slot_with_another_checks_section_is_rejected() -> None:
    """A passing check cannot be cited to a different check's section."""
    assert "V10_UNBOUND_ASSERT" in codes("The DSCR of {{claim:dscr}} {{assert:R-NBC-04}} [NBC-3.4].")


def test_binding_is_not_checked_without_scopes() -> None:
    """Callers that pass no scopes keep the earlier behaviour (V10 off)."""
    text = "The gap of {{claim:revenue_gap_pct}} {{assert:R-NBC-04}} [NBC-4.2]."
    violations = validate(text, CLAIMS, ASSERTIONS, [], SECTIONS)
    assert "V10_UNBOUND_ASSERT" not in {violation.code for violation in violations}


def test_digits_inside_slots_and_citations_are_not_violations() -> None:
    """Digits in a slot id or a section id are part of the token, not the prose."""
    assert codes("The gap {{assert:R-NBC-02}} [NBC-3.4].") == set()
