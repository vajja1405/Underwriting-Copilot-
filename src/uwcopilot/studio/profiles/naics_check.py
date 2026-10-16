"""Check the profiles' NAICS codes against a published code list: the BLS QCEW industry titles file.

    https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv

Why this list: the Census NAICS pages (www.census.gov/naics) answered HTTP 403 from the build environment on
2026-10-12 and 2026-10-13, while the BLS file answered. It is the list of industry codes the QCEW data (wages.py)
uses, so a code found here is also a code the wage connector can ask for.

The file has two columns, industry_code and industry_title. Titles start with a prefix that says what the code is:
"NAICS 811192 Car washes" is a code of the current NAICS vintage; "NAICS17 333244 ..." or "NAICS07 ..." are codes that
exist only in an older vintage; titles without a NAICS prefix ("10 Total, all industries") are QCEW aggregates, not
NAICS codes. A profile code is confirmed only when it has the current "NAICS " prefix AND its title, without the
prefix, equals the profile's title (case and spacing ignored). Everything else is reported, never guessed.
"""

import csv
import io
import re
from dataclasses import dataclass
from typing import Literal

from uwcopilot.studio.profiles.models import Profile

INDUSTRY_TITLES_URL = "https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv"
PUBLISHER = "U.S. Bureau of Labor Statistics, Quarterly Census of Employment and Wages"
LIST_NAME = "QCEW industry titles (industry_titles.csv)"
REQUIRED_COLUMNS: tuple[str, ...] = ("industry_code", "industry_title")
# "NAICS 811192 Car washes" -> vintage "", code "811192", text "Car washes"; "NAICS17 333244 ..." -> vintage "17".
NAICS_TITLE = re.compile(r"^NAICS(?P<vintage>\d{2})? (?P<code>[0-9-]+) (?P<text>.+)$")
CheckStatus = Literal["confirmed", "title_differs", "older_vintage_only", "not_a_naics_title", "not_in_list"]


@dataclass(frozen=True)
class ListedCode:
    """One row of the industry titles file."""

    code: str
    title: str

    def parts(self) -> tuple[str | None, str]:
        """(vintage suffix or None when the title has no NAICS prefix, title text without the prefix)."""
        match = NAICS_TITLE.match(self.title)
        if match is None or match.group("code") != self.code:
            return None, self.title
        return match.group("vintage") or "", match.group("text")


@dataclass(frozen=True)
class CodeCheck:
    """The result for one profile code."""

    profile_id: str
    code: str
    profile_title: str
    status: CheckStatus
    published_title: str | None
    detail: str

    @property
    def confirmed(self) -> bool:
        """True when the code and its title are in the current-vintage list."""
        return self.status == "confirmed"


def parse_industry_titles(text: str) -> dict[str, ListedCode]:
    """{industry_code: ListedCode} from the CSV text. Raises ValueError when the expected columns are missing."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"industry titles file is missing columns {missing}")
    listed: dict[str, ListedCode] = {}
    for row in reader:
        code = row["industry_code"].strip()
        listed[code] = ListedCode(code=code, title=row["industry_title"].strip())
    return listed


def same_title(left: str, right: str) -> bool:
    """Titles compared without case and with runs of spaces collapsed."""
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


def check_code(profile_id: str, code: str, title: str, listed: dict[str, ListedCode]) -> CodeCheck:
    """Look one profile code up in the list and say what was found."""
    row = listed.get(code)
    if row is None:
        return CodeCheck(profile_id, code, title, "not_in_list", None, f"code {code} is not in the list")
    vintage, text = row.parts()
    if vintage is None:
        detail = f"the list has {code} only as a non-NAICS aggregate ({row.title!r})"
        return CodeCheck(profile_id, code, title, "not_a_naics_title", row.title, detail)
    if vintage:
        detail = f"the list marks {code} as a NAICS{vintage} code only ({row.title!r}), not the current vintage"
        return CodeCheck(profile_id, code, title, "older_vintage_only", row.title, detail)
    if not same_title(text, title):
        detail = f"published title {text!r} differs from the profile title {title!r}"
        return CodeCheck(profile_id, code, title, "title_differs", row.title, detail)
    return CodeCheck(profile_id, code, title, "confirmed", row.title, f"listed as {row.title!r}")


def check_profiles(profiles: dict[str, Profile], listed: dict[str, ListedCode]) -> list[CodeCheck]:
    """Every NAICS code of every profile, checked against the list (profile and code order kept)."""
    return [
        check_code(profile_id, item.code, item.title, listed)
        for profile_id, profile in profiles.items()
        for item in profile.naics
    ]
