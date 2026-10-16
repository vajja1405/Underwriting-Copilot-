"""Tests for the free-form numeric-provenance checker (provenance/numeric_check.py)."""

from uwcopilot.provenance.numbers import extract_numbers
from uwcopilot.provenance.numeric_check import (
    TableCell,
    check_numbers,
    cited_ids,
    comparator_near,
    source_comparators,
    split_sentences,
    table_cells,
)
from uwcopilot.provenance.reference import section_texts

SECTION = (
    "Every term loan above $100,000 is stress tested.\n\n"
    "- Revenue stress: reduce verified annual revenue by 20%, reduce NOI by the same dollar amount. A stressed DSCR\n"
    "  below 1.00 is a referral to a Senior Underwriter.\n"
    "- Rate stress: add 2.00 percentage points to the note rate.\n"
)
TABLE_SECTION = "| Product | Minimum decision DSCR |\n|---|---|\n| Term loan | 1.25 |\n| Revolving line | 1.35 |\n"
BANDS_SECTION = "| Revenue gap | Classification |\n|---|---|\n| 10.0% or less | Benign |\n| More than 10.0% | Refer |\n"
SOURCES = {"NBC-4.5": SECTION, "NBC-4.2": TABLE_SECTION, "NBC-3.4": BANDS_SECTION}


def statuses(text: str) -> list[str]:
    """The status of every number in text, against SOURCES."""
    return [item.status for item in check_numbers(text, SOURCES).mentions]


def test_traceable_numbers_pass() -> None:
    """A restated value with its citation passes, also with an equivalent unit."""
    assert statuses("Revenue stress cuts revenue by 20% [NBC-4.5].") == ["traceable"]
    assert statuses("Rate stress adds 200 basis points [NBC-4.5].") == ["traceable"]


def test_untraceable_number_is_flagged() -> None:
    """A value the cited section does not contain is UNTRACEABLE_NUMBER."""
    result = check_numbers("Revenue stress cuts revenue by 25% [NBC-4.5].", SOURCES)
    assert result.flagged and [item.status for item in result.mentions] == ["untraceable"]


def test_uncited_or_out_of_context_citation_is_untraceable() -> None:
    """No citation, or a citation whose text was not in context, cannot make a number traceable."""
    assert statuses("Revenue stress cuts revenue by 20%.") == ["untraceable"]
    assert statuses("Revenue stress cuts revenue by 20% [NBC-9.9].") == ["untraceable"]


def test_bps_is_not_percent_without_conversion() -> None:
    """'2 bps' is not '2.00 percentage points' (a unit swap)."""
    assert statuses("Rate stress adds 2 bps [NBC-4.5].") == ["untraceable"]


def test_comparator_mismatch() -> None:
    """The source pairs 1.00 with 'below' (LT); 'at least 1.00' is a different class."""
    assert statuses("A stressed DSCR of at least 1.00 is a referral [NBC-4.5].") == ["comparator_mismatch"]
    assert statuses("A stressed DSCR below 1.00 is a referral [NBC-4.5].") == ["traceable"]


def test_added_comparator_on_a_plain_value_is_a_mismatch() -> None:
    """The source states 20% with no comparator; 'at least 20%' asserts more than the source says."""
    result = check_numbers("Revenue stress cuts revenue by at least 20% [NBC-4.5].", SOURCES)
    assert [item.status for item in result.mentions] == ["comparator_mismatch"]
    assert "without a comparator" in result.mentions[0].detail


def test_table_header_binds_to_its_cells() -> None:
    """'Minimum decision DSCR' in the header pairs with 1.25, so 'minimum ... 1.25' is traceable."""
    assert statuses("The minimum DSCR for a term loan is 1.25 [NBC-4.2].") == ["traceable"]
    assert TableCell("1.25", "Minimum decision DSCR", "Term loan") in table_cells(TABLE_SECTION)


def test_value_used_with_several_comparators_is_unverifiable() -> None:
    """NBC-3.4 pairs 10.0% with both 'or less' and 'more than': the comparator cannot be checked (counted)."""
    result = check_numbers("A gap of at least 10.0% is benign [NBC-3.4].", SOURCES)
    assert [item.status for item in result.mentions] == ["comparator_unverifiable"]
    assert not result.flagged


def test_comparator_window_is_five_tokens_and_stays_in_the_sentence() -> None:
    """Comparators farther than 5 tokens, or in another sentence, do not count."""
    near = "a DSCR of at least 1.25"
    assert comparator_near(near, extract_numbers(near)[0]) == "GE"
    far = "minimum required by the bank for the loan is 1.25"
    assert comparator_near(far, extract_numbers(far)[0]) is None


def test_citation_stays_with_its_sentence_and_lists_are_split() -> None:
    """'[NBC-4.2]' after a full stop belongs to that sentence; '[A, B]' cites both."""
    sentences = [text for _, text in split_sentences("DSCR is 1.25. [NBC-4.2] Next sentence here.")]
    assert "[NBC-4.2]" in sentences[0] and "Next" not in sentences[0]
    assert cited_ids("Text [NBC-4.2, NBC-4.5] and [S1].") == ("NBC-4.2", "NBC-4.5", "S1")


def test_soft_line_wraps_do_not_split_comparators() -> None:
    """A comparator broken across a markdown line wrap still binds to its value."""
    source = {"X-1.1": "businesses that derive more\n  than 10% of revenue are prohibited."}
    assert [m.status for m in check_numbers("More than 10% of revenue [X-1.1].", source).mentions] == ["traceable"]


# Regression tests from the S1b review (docs/walkthrough_innovations_s1.md). Each uses the real guideline text
# (corpus/guidelines, SYNTHETIC) through section_texts(), so a change in the guideline shows up here.
GUIDELINES = section_texts()


def guideline_statuses(text: str) -> list[tuple[str, str, str | None]]:
    """(number, status, answer comparator) for every number in text, against the real guideline sections."""
    return [(item.mention.text, item.status, item.comparator) for item in check_numbers(text, GUIDELINES).mentions]


def test_header_comparator_binds_to_cells_far_from_the_header() -> None:
    """NBC-4.2 '| Equipment loan | 1.25 |' under 'Minimum decision DSCR': 6+ tokens apart once written as a sentence.

    Before the fix the source paired 1.25 and 1.35 with no comparator, so a correct 'at least 1.25' was flagged.
    """
    for value in ("1.25", "1.35"):
        [mention] = extract_numbers(value)
        assert source_comparators(GUIDELINES["NBC-4.2"], mention) == {"GE"}
    assert guideline_statuses("A term loan needs a decision DSCR of at least 1.25 [NBC-4.2].") == [
        ("1.25", "traceable", "GE")
    ]
    assert guideline_statuses("A revolving line needs a decision DSCR of at least 1.35 [NBC-4.2].") == [
        ("1.35", "traceable", "GE")
    ]
    assert guideline_statuses("A revolving line needs a decision DSCR of at most 1.35 [NBC-4.2].") == [
        ("1.35", "comparator_mismatch", "LE")
    ]


def test_header_bound_for_an_approver_limit() -> None:
    """HM-9.2 '| Senior Underwriter | $15,000,000 | $150,000 | One |' under 'Maximum annual premium' (LE)."""
    [mention] = extract_numbers("$150,000")
    assert source_comparators(GUIDELINES["HM-9.2"], mention) == {"LE"}
    assert guideline_statuses("A Senior Underwriter can approve an annual premium of up to $150,000 [HM-9.2].") == [
        ("$150,000", "traceable", "LE")
    ]
    # "exceeds" belongs to $180,000 (the closer number); $150,000 keeps its own "maximum".
    answer = "A premium of $180,000 exceeds the Senior Underwriter maximum of $150,000 [HM-9.2]."
    assert guideline_statuses(answer)[1] == ("$150,000", "traceable", "LE")
    # Documented limit: the other side of the same limit is a different class, so it is still a mismatch.
    assert guideline_statuses("A premium above $150,000 needs the Underwriting Manager [HM-9.2].") == [
        ("$150,000", "comparator_mismatch", "GT")
    ]


def test_a_row_label_comparator_stays_with_its_own_number() -> None:
    """NBC-5.1 '| Accounts receivable (aged 90 days or less) | ... | 70% |': 'or less' is 90 days', not 70%'s."""
    cell = next(cell for cell in table_cells(GUIDELINES["NBC-5.1"]) if cell.text == "70%")
    assert cell.row_label == "Accounts receivable (aged 90 days or less)"
    [seventy] = extract_numbers("70%")
    assert source_comparators(GUIDELINES["NBC-5.1"], seventy) == {"LE"}  # from "Maximum advance rate"
    [ninety] = extract_numbers("90 days")
    assert source_comparators(GUIDELINES["NBC-5.1"], ninety) == {"LE"}  # from "or less" in its own cell


def test_a_row_label_lends_its_comparator_in_a_transposed_table() -> None:
    """'| Minimum DSCR | 1.25 | 1.35 |': the row label is the only place the comparator can come from."""
    source = "| Measure | Term loan | Revolver |\n|---|---|---|\n| Minimum DSCR | 1.25 | 1.35 |\n"
    [mention] = extract_numbers("1.35")
    assert source_comparators(source, mention) == {"GE"}


def test_a_comparator_belongs_to_the_closest_number() -> None:
    """'more than' in 'five-year loss ratio is more than 55.0%' belongs to 55.0%, not to five-year (HM-4.2)."""
    answer = "If the five-year loss ratio is more than 55.0%, refer it to a Senior Underwriter [HM-4.2]."
    assert guideline_statuses(answer) == [
        ("five-year", "traceable", None),
        ("55.0%", "comparator_unverifiable", "GT"),  # HM-4.2 uses 55.0% with "or less" and "more than"
    ]
    text = "A five-year loss ratio of more than 55.0% and up to 70.0% is referred [HM-4.2]."
    first, second, third = extract_numbers(text)
    assert first.text == "five-year" and first.value == 5  # "A" is an article, not "one"
    assert comparator_near(text, first) is None
    assert comparator_near(text, second) == "GT" and comparator_near(text, third) == "LE"


def test_on_a_tie_a_comparator_reads_towards_its_side() -> None:
    """'or less' takes the number before it; other phrases the number after it."""
    text = "Leverage of 3.50 or less and 4.00 maximum"
    low, high = extract_numbers(text)
    assert comparator_near(text, low) == "LE" and comparator_near(text, high) == "LE"
    text = "a DSCR of 1.18 is below 1.25"
    question_value, policy_value = extract_numbers(text)
    assert comparator_near(text, question_value) is None and comparator_near(text, policy_value) == "LT"


def test_durations_with_qualifiers_match_their_source() -> None:
    """'60 consecutive days' (HM-2.2) and '12 consecutive months' (NBC-3.x) are durations, not counts."""
    assert guideline_statuses("Buildings vacant for more than 60 consecutive days are prohibited [HM-2.2].") == [
        ("60 consecutive days", "traceable", "GT")
    ]
    assert guideline_statuses("Buildings vacant for more than 60 days are prohibited [HM-2.2].") == [
        ("60 days", "traceable", "GT")
    ]
    assert guideline_statuses("The experience period is 5 years [HM-1.2].") == [("5 years", "traceable", None)]
    assert guideline_statuses("Buildings vacant for more than 60 months are prohibited [HM-2.2].")[0][1] == (
        "untraceable"
    )


def test_a_restated_duration_traces_to_its_qualified_source() -> None:
    """NBC-3.5 says 'the required 12 consecutive months'; HM-1.2 'the 5 most recent complete policy years'.

    Before the fix both were counts, so an answer writing '12 months' or '5 years' (or 'one year') was untraceable.
    """
    assert guideline_statuses("A missing statement month within the required 12 months is a trigger [NBC-3.5].") == [
        ("12 months", "traceable", None)
    ]
    assert guideline_statuses("The required statements cover one year [NBC-3.5].") == [("one year", "traceable", None)]
    assert guideline_statuses("The experience period covers the last 5 policy years [HM-1.2].") == [
        ("5 policy years", "traceable", None)
    ]


# Regression tests from the code review of the S1b checker fixes (real guideline text, SYNTHETIC).


def test_coordinated_values_share_the_comparator() -> None:
    """HM-7.1 'limits above $2,000,000 / $4,000,000 are not offered': "above" governs both values.

    With one comparator per number and no coordination rule, $4,000,000 had no comparator in the source, so a correct
    'above $4,000,000' was a COMPARATOR_MISMATCH (S1b question HM32's correct answer was flagged).
    """
    [four] = extract_numbers("$4,000,000")
    assert source_comparators(GUIDELINES["HM-7.1"], four) == {"GT"}
    assert guideline_statuses("General liability aggregate limits above $4,000,000 are not offered [HM-7.1].") == [
        ("$4,000,000", "traceable", "GT")
    ]
    hm32 = (  # the correct answer to S1b question HM32
        "No. Harborline does not offer liability limits above $2,000,000 per occurrence "
        "or above $4,000,000 aggregate [HM-7.1]."
    )
    assert guideline_statuses(hm32) == [
        ("$2,000,000", "traceable", "GT"),
        ("$4,000,000", "traceable", "GT"),
    ]
    # The answer side: a flip of the shared comparator is caught through both values, not only the first.
    assert guideline_statuses("Limits below $2,000,000 / $4,000,000 are not offered [HM-7.1].") == [
        ("$2,000,000", "comparator_mismatch", "LT"),
        ("$4,000,000", "comparator_mismatch", "LT"),
    ]


def test_coordination_needs_a_joiner_and_the_same_unit() -> None:
    """Only '/', 'and', 'or', 'and/or' (after an optional 'per <word>' and comma) join; nothing flows backwards."""

    def second_comparator(text: str) -> str | None:
        mentions = extract_numbers(text)
        return comparator_near(text, mentions[1], mentions)

    assert second_comparator("up to $25,000,000 per account and $10,000,000 per location") == "LE"  # HM-1.1
    assert second_comparator("above $1,000,000 per occurrence / $2,000,000 aggregate") == "GT"  # HM-7.1
    assert second_comparator("above $2,000,000, $4,000,000") is None  # a bare comma does not join
    assert second_comparator("above $2,000,000 / 4.00") is None  # different units
    text = "$2,000,000 / $4,000,000 or more"
    first, second = extract_numbers(text)
    assert comparator_near(text, first) is None and comparator_near(text, second) == "GE"


def test_fraction_words_before_a_scale_do_not_round_to_a_source_limit() -> None:
    """'two and a half million dollars' is $2,500,000, not $2,000,000 (NBC-1.1 maximums: $2,000,000, $1,500,000)."""
    assert guideline_statuses("A term loan can be up to two and a half million dollars [NBC-1.1].") == [
        ("two and a half million dollars", "untraceable", "LE")
    ]
    assert guideline_statuses("Limits above one and a half million dollars require approval [HM-7.1].") == [
        ("one and a half million dollars", "untraceable", "GT")
    ]
    assert guideline_statuses("An equipment loan can be up to one and a half million dollars [NBC-1.1].") == [
        ("one and a half million dollars", "traceable", "LE")
    ]
    assert guideline_statuses("A revolving line can be up to half a million dollars [NBC-1.1].") == [
        ("half a million dollars", "traceable", "LE")
    ]


def test_a_limit_stated_from_both_sides_is_unverifiable() -> None:
    """NBC-7.2 pairs $500,000 and $1,000,000 with 'Maximum total exposure' (LE, header) and 'above' (GT, prose).

    Known trade-off of header binding, pinned here until the owners decide whether a limit and its complement (LE/GT,
    GE/LT) count as one threshold: these flips pass as comparator_unverifiable (the old checker caught them), while a
    correct 'up to $1,000,000' (a false flag in the old checker) now passes.
    """
    for value in ("$500,000", "$1,000,000"):
        [mention] = extract_numbers(value)
        assert source_comparators(GUIDELINES["NBC-7.2"], mention) == {"GT", "LE"}
    assert guideline_statuses("Total exposure below $500,000 goes to the Chief Credit Officer [NBC-7.2].") == [
        ("$500,000", "comparator_unverifiable", "LT")
    ]
    assert guideline_statuses("The Chief Credit Officer can approve up to $1,000,000 [NBC-7.2].") == [
        ("$1,000,000", "comparator_unverifiable", "LE")
    ]
