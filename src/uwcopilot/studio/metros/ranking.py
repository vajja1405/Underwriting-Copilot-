"""Rank US metropolitan statistical areas by population from the Census Population Estimates Program (PEP) file.

The Studio plan's rule for v1 cities is "the five largest US metropolitan statistical areas by population in the
latest Census Bureau estimates". scripts/studio_fetch_delineation.py downloads the PEP CBSA totals file
(cbsa-est<vintage>-alldata.csv), and this module ranks the metropolitan statistical areas in it, so the rule is
checked against the file, not memory.

The file has one row per CBSA total, plus rows for metropolitan divisions (MDIV filled) and counties (STCOU filled).
Only CBSA totals of metropolitan statistical areas are ranked.
"""

import csv
import io
from dataclasses import dataclass

METRO_LSAD = "Metropolitan Statistical Area"


@dataclass(frozen=True)
class MetroPopulation:
    """One metropolitan statistical area's population estimate for one year."""

    cbsa_code: str
    name: str
    population: int


def decode_census_csv(body: bytes) -> str:
    """Census CSV files are UTF-8 or Latin-1 (place names with accents); try UTF-8 first."""
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def estimate_column(header: list[str], year: int | None = None) -> str:
    """The POPESTIMATE<year> column to rank by: the given year, else the latest year in the file."""
    columns = sorted(name for name in header if name.startswith("POPESTIMATE") and name[11:].isdigit())
    if not columns:
        raise ValueError("no POPESTIMATE<year> column in the file")
    if year is None:
        return columns[-1]
    wanted = f"POPESTIMATE{year}"
    if wanted not in columns:
        raise ValueError(f"{wanted} is not in the file; available: {columns}")
    return wanted


def rank_metros(body: bytes, year: int | None = None) -> tuple[str, list[MetroPopulation]]:
    """(column used, metropolitan statistical areas sorted from largest to smallest population)."""
    reader = csv.DictReader(io.StringIO(decode_census_csv(body)))
    header = list(reader.fieldnames or [])
    for name in ("CBSA", "MDIV", "STCOU", "NAME", "LSAD"):
        if name not in header:
            raise ValueError(f"column {name!r} is missing from the PEP file")
    column = estimate_column(header, year)
    metros = [
        MetroPopulation(cbsa_code=row["CBSA"], name=row["NAME"], population=int(row[column]))
        for row in reader
        if row["LSAD"] == METRO_LSAD and not row["MDIV"] and not row["STCOU"]
    ]
    metros.sort(key=lambda item: item.population, reverse=True)
    return column, metros
