"""Tests for the page text models and line grouping (docintel/layout.py)."""

import pytest
from pydantic import ValidationError

from uwcopilot.docintel.layout import Line, PageText, Word, group_words_into_lines, make_line, renumber, union_box


def word(text: str, x0: float, top: float, size: float = 9.0, conf: float = 1.0) -> Word:
    """A word box of a given font size, as wide as its text roughly needs."""
    return Word(text=text, x0=x0, top=top, x1=x0 + 5.0 * len(text), bottom=top + size, conf=conf)


def test_words_on_one_baseline_form_one_line_left_to_right() -> None:
    words = [word("$70,000.00", 230.0, 100.0), word("Requested", 54.0, 100.0), word("amount", 100.0, 100.0)]
    lines = group_words_into_lines(words)
    assert len(lines) == 1
    assert lines[0].text == "Requested amount $70,000.00"
    assert lines[0].line_number == 1
    assert lines[0].bbox == (54.0, 100.0, 280.0, 109.0)


def test_rows_are_numbered_top_to_bottom() -> None:
    words = [word("third", 54.0, 140.0), word("first", 54.0, 100.0), word("second", 54.0, 113.0)]
    lines = group_words_into_lines(words)
    assert [line.text for line in lines] == ["first", "second", "third"]
    assert [line.line_number for line in lines] == [1, 2, 3]


def test_a_bold_label_and_a_larger_value_on_one_row_stay_together() -> None:
    # Different font sizes on one baseline shift the box centres by less than a point.
    label = Word(text="Bank", x0=54.0, top=58.0, x1=80.0, bottom=70.0)
    value = Word(text="2026-09-02", x0=380.0, top=60.0, x1=420.0, bottom=70.0)
    assert len(group_words_into_lines([label, value])) == 1


def test_line_confidence_is_the_mean_word_confidence() -> None:
    line = make_line(4, [word("a", 0.0, 0.0, conf=0.5), word("b", 10.0, 0.0, conf=1.0)])
    assert line.confidence == 0.75
    assert line.line_number == 4


def test_union_box_and_renumber() -> None:
    assert union_box([(1.0, 2.0, 3.0, 4.0), (0.5, 3.0, 5.0, 3.5)]) == (0.5, 2.0, 5.0, 4.0)
    lines = [make_line(7, [word("x", 0.0, 0.0)]), make_line(9, [word("y", 0.0, 20.0)])]
    assert [line.line_number for line in renumber(lines)] == [1, 2]


def test_page_text_joins_lines_and_rejects_bad_values() -> None:
    lines = [make_line(1, [word("one", 0.0, 0.0)]), make_line(2, [word("two", 0.0, 20.0)])]
    page = PageText(page_number=3, width=612.0, height=792.0, source="text_layer", lines=lines)
    assert page.text == "one\ntwo"
    with pytest.raises(ValidationError):
        Word(text="x", x0=0, top=0, x1=1, bottom=1, conf=1.5)
    with pytest.raises(ValidationError):
        Line(line_number=0, text="x", bbox=(0, 0, 1, 1), words=[], confidence=1.0)
    with pytest.raises(ValidationError):
        PageText(page_number=1, width=1, height=1, source="scan", lines=[])
