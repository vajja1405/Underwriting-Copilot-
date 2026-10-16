"""Read the Census Bureau's CBSA delineation file ("List 1", an .xlsx workbook) without extra dependencies.

The file lists every county of every Core Based Statistical Area (CBSA): CBSA code and title, metropolitan or
micropolitan, state and county FIPS codes, county name and whether the county is central or outlying. Studio reads
it to fill a real metro's county list (scripts/studio_fetch_delineation.py), so no FIPS code is typed from memory.

An .xlsx file is a zip archive of XML files. Only two parts matter here: xl/sharedStrings.xml (the text values) and
the first worksheet (the cells). read_xlsx_rows() turns the sheet into a list of rows of strings. A downloaded file
is untrusted input, so the reader refuses XML with a DOCTYPE (that blocks entity-expansion tricks) and caps sizes.
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Size caps for an untrusted workbook: the 2023 List 1 sheet is about 1 MB uncompressed.
MAX_XML_BYTES = 50 * 1024 * 1024
CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
# Column headers of List 1 that Studio needs, as printed in the file's header row.
REQUIRED_HEADERS: tuple[str, ...] = (
    "CBSA Code",
    "CBSA Title",
    "Metropolitan/Micropolitan Statistical Area",
    "County/County Equivalent",
    "State Name",
    "FIPS State Code",
    "FIPS County Code",
    "Central/Outlying County",
)


@dataclass(frozen=True)
class DelineationRow:
    """One county row of the delineation file."""

    cbsa_code: str
    cbsa_title: str
    area_type: str
    county_name: str
    state_name: str
    state_fips: str
    county_fips: str
    central_or_outlying: str


@dataclass(frozen=True)
class DelineationFile:
    """The parsed file: its title line, its note lines (which carry the delineation date) and its county rows."""

    title: str
    notes: list[str]
    rows: list[DelineationRow]


def read_xml_part(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    """Parse one XML part of the workbook, refusing oversized parts and any DOCTYPE declaration."""
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError(f"{name} is larger than {MAX_XML_BYTES} bytes; refusing to parse it")
    data = archive.read(name)
    if b"<!DOCTYPE" in data[:4096].upper():
        raise ValueError(f"{name} declares a DOCTYPE; refusing to parse it")
    return ElementTree.fromstring(data)


def column_index(letters: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def read_xlsx_rows(path: Path) -> list[list[str]]:
    """Every row of the first worksheet as a list of strings ('' for empty cells), in sheet order."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for item in read_xml_part(archive, "xl/sharedStrings.xml").iter(f"{SHEET_NS}si"):
                shared.append("".join(text.text or "" for text in item.iter(f"{SHEET_NS}t")))
        sheet = read_xml_part(archive, "xl/worksheets/sheet1.xml")
    rows: list[list[str]] = []
    for row in sheet.iter(f"{SHEET_NS}row"):
        values: dict[int, str] = {}
        for cell in row.iter(f"{SHEET_NS}c"):
            match = CELL_REF.match(cell.get("r", ""))
            if match is None:
                continue
            values[column_index(match.group(1))] = cell_text(cell, shared)
        width = max(values) + 1 if values else 0
        rows.append([values.get(index, "") for index in range(width)])
    return rows


def cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    """The text of one cell: a shared string, an inline string or a plain value."""
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(text.text or "" for text in cell.iter(f"{SHEET_NS}t")).strip()
    value = cell.find(f"{SHEET_NS}v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        return shared[int(value.text)].strip()
    return value.text.strip()


def parse_delineation(rows: list[list[str]]) -> DelineationFile:
    """Find the header row, then read every county row below it until the first row without a CBSA code.

    The line above the header is the title; lines below the table are notes (the source and the delineation date).
    Raises ValueError when a required column is missing, so a changed layout is never read wrongly.
    """
    header_index = next(
        (index for index, row in enumerate(rows) if "CBSA Code" in row and "FIPS County Code" in row), None
    )
    if header_index is None:
        raise ValueError("no header row with 'CBSA Code' and 'FIPS County Code' found")
    header = rows[header_index]
    missing = [name for name in REQUIRED_HEADERS if name not in header]
    if missing:
        raise ValueError(f"the delineation file is missing columns {missing}")
    position = {name: header.index(name) for name in REQUIRED_HEADERS}
    # The title is the last non-empty line above the header (the first line is an accessibility note).
    lines_above = [" ".join(cell for cell in row if cell) for row in rows[:header_index]]
    title = next((line for line in reversed(lines_above) if line), "")
    table: list[DelineationRow] = []
    notes: list[str] = []
    for row in rows[header_index + 1 :]:
        padded = row + [""] * (len(header) - len(row))
        code = padded[position["CBSA Code"]]
        if not code.isdigit():
            notes.extend(cell for cell in padded if cell)
            continue
        table.append(
            DelineationRow(
                cbsa_code=code,
                cbsa_title=padded[position["CBSA Title"]],
                area_type=padded[position["Metropolitan/Micropolitan Statistical Area"]],
                county_name=padded[position["County/County Equivalent"]],
                state_name=padded[position["State Name"]],
                state_fips=padded[position["FIPS State Code"]].zfill(2),
                county_fips=padded[position["FIPS County Code"]].zfill(3),
                central_or_outlying=padded[position["Central/Outlying County"]],
            )
        )
    return DelineationFile(title=title, notes=notes, rows=table)


def rows_for_title_prefix(parsed: DelineationFile, title_prefix: str) -> list[DelineationRow]:
    """The county rows of the one metropolitan CBSA whose title starts with title_prefix.

    Raises ValueError when no CBSA or more than one CBSA matches, so an ambiguous prefix never picks a metro.
    """
    matches = [
        row
        for row in parsed.rows
        if row.cbsa_title.startswith(title_prefix) and row.area_type.startswith("Metropolitan")
    ]
    codes = sorted({row.cbsa_code for row in matches})
    if len(codes) != 1:
        raise ValueError(f"title prefix {title_prefix!r} matches {len(codes)} metropolitan CBSAs: {codes}")
    return matches


def states_from_title(cbsa_title: str) -> list[str]:
    """The state postal codes printed after the comma in a CBSA title ('Chicago-Naperville-Elgin, IL-IN' -> IL, IN)."""
    if ", " not in cbsa_title:
        raise ValueError(f"CBSA title {cbsa_title!r} has no ', ' before its state codes")
    return cbsa_title.rsplit(", ", 1)[1].split("-")
