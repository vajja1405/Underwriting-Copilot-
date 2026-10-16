"""Reading the Census delineation workbook and the population ranking, and the fetch script's pure helpers."""

import io
import json
import zipfile

import pytest
import yaml
from studio_helpers import fixture_body, load_fixture

import studio_fetch_delineation as script
from uwcopilot.studio.metros.delineation import (
    column_index,
    parse_delineation,
    read_xlsx_rows,
    rows_for_title_prefix,
    states_from_title,
)
from uwcopilot.studio.metros.ranking import rank_metros

SHEET = (
    '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="C2" t="inlineStr"><is><t>inline</t></is></c>'
    '<c r="D2"><v>42</v></c></row>'
    "</sheetData></worksheet>"
)
SHARED = (
    '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<si><t>Title JULY 2023</t></si><si><r><t>rich </t></r><r><t>text</t></r></si></sst>"
)


def write_workbook(path, sheet: str = SHEET, shared: str = SHARED) -> None:
    """A minimal .xlsx (zip with a sheet and shared strings), built in the test."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)


def recorded_rows() -> list[list[str]]:
    """Rows of the recorded delineation excerpt (title, header, the five v1 CBSAs, notes)."""
    return json.loads(fixture_body("delineation/list1_2023_rows.json"))


def test_column_letters() -> None:
    assert [column_index(letters) for letters in ("A", "Z", "AA", "AB")] == [0, 25, 26, 27]


def test_reads_shared_inline_and_plain_cells(tmp_path) -> None:
    path = tmp_path / "book.xlsx"
    write_workbook(path)
    assert read_xlsx_rows(path) == [["Title JULY 2023"], ["rich text", "", "inline", "42"]]


def test_refuses_a_doctype(tmp_path) -> None:
    path = tmp_path / "evil.xlsx"
    write_workbook(path, sheet='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><worksheet/>')
    with pytest.raises(ValueError, match="DOCTYPE"):
        read_xlsx_rows(path)


def test_parse_recorded_rows_finds_the_five_metros() -> None:
    parsed = parse_delineation(recorded_rows())
    assert parsed.title.startswith("List 1.") and parsed.title.endswith("JULY 2023")
    assert any(note.startswith("Source:") for note in parsed.notes)
    houston = rows_for_title_prefix(parsed, "Houston-Pasadena-The Woodlands")
    assert {row.cbsa_code for row in houston} == {"26420"}
    assert all(len(row.county_fips) == 3 and len(row.state_fips) == 2 for row in houston)
    assert script.delineation_date(parsed) == "July 2023"
    assert script.delineation_publisher(parsed).startswith("U.S. Census Bureau")


def test_ambiguous_or_missing_prefix_fails() -> None:
    parsed = parse_delineation(recorded_rows())
    with pytest.raises(ValueError, match="matches 0"):
        rows_for_title_prefix(parsed, "Springfield")
    with pytest.raises(ValueError, match="matches 5"):
        rows_for_title_prefix(parsed, "")


def test_missing_column_fails() -> None:
    rows = recorded_rows()
    header_index = next(index for index, row in enumerate(rows) if "CBSA Code" in row)
    rows[header_index] = [cell.replace("FIPS State Code", "State") for cell in rows[header_index]]
    with pytest.raises(ValueError, match="missing columns"):
        parse_delineation(rows)


def test_states_come_from_the_title() -> None:
    assert states_from_title("Chicago-Naperville-Elgin, IL-IN") == ["IL", "IN"]
    with pytest.raises(ValueError):
        states_from_title("No state codes")


def test_ranking_uses_metropolitan_totals_only() -> None:
    body = fixture_body("ranking/cbsa_est2025_excerpt.json").encode("utf-8")
    column, ranking = rank_metros(body)
    assert column == "POPESTIMATE2025"
    assert [item.cbsa_code for item in ranking[:5]] == ["35620", "31080", "16980", "19100", "26420"]
    assert len({item.cbsa_code for item in ranking}) == len(ranking)  # no county or division rows
    assert ranking == sorted(ranking, key=lambda item: item.population, reverse=True)


def test_ranking_rejects_unknown_year() -> None:
    body = fixture_body("ranking/cbsa_est2025_excerpt.json").encode("utf-8")
    with pytest.raises(ValueError, match="POPESTIMATE1999"):
        rank_metros(body, year=1999)


def test_updated_config_disables_state_adapters_outside_the_metro() -> None:
    parsed = parse_delineation(recorded_rows())
    rows = rows_for_title_prefix(parsed, "New York-Newark-Jersey City")
    raw = {
        "id": "x",
        "enhancements": [
            {"kind": "traffic_state", "adapter": "PA", "enabled": True},
            {"kind": "traffic_state", "adapter": "NY", "enabled": True},
        ],
    }
    data = script.updated_config(raw, rows, {"source": {}}, "note")
    assert data["states"] == states_from_title(rows[0].cbsa_title)
    flags = {item["adapter"]: item["enabled"] for item in data["enhancements"]}
    assert flags["NY"] is True
    if "PA" not in data["states"]:
        assert flags["PA"] is False


def test_yaml_dumper_quotes_digit_strings() -> None:
    text = yaml.dump({"county_fips": "039", "name": "x"}, Dumper=script.QuotedCodesDumper)
    assert "'039'" in text
    assert yaml.safe_load(text)["county_fips"] == "039"


def test_recorded_fixture_is_labeled() -> None:
    assert "recorded from the live API" in load_fixture("delineation/list1_2023_rows.json")["fixture_origin"]


def test_reader_handles_bytes_like_the_script(tmp_path) -> None:
    buffer = io.BytesIO()
    write_workbook(buffer)
    path = tmp_path / "copy.xlsx"
    path.write_bytes(buffer.getvalue())
    assert read_xlsx_rows(path)[0] == ["Title JULY 2023"]
