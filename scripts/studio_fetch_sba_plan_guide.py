"""Read the SBA's business-plan guidance page once and record its traditional business plan section names (Studio S6).

    uv run python scripts/studio_fetch_sba_plan_guide.py          # fetch (or reuse the cache) and write the record
    uv run python scripts/studio_fetch_sba_plan_guide.py --dry-run  # print what would be written

The founder package (src/uwcopilot/studio/package) aligns its section names with the U.S. Small Business
Administration's "Write your business plan" guidance. Nothing about that page is typed from memory: this script
1. requests the page through the polite, caching client (studio/data/http.py; it follows redirects and records the
   URL that answered), saves the body under data/studio/reference/ (gitignored, never committed) and appends its
   SHA-256 to data/studio/checksums.jsonl;
2. reads the h5 headings that follow the h4 heading "Common traditional business plan sections" (up to the next h4
   or higher heading) and refuses to write anything when that heading is missing;
3. writes config/studio/sba_business_plan_sections.json: the requested URL, the URL that answered, the retrieval
   time, the SHA-256 of the body, the heading the list was read under, and the section names in page order.
docs/studio/sources.md cites that record. If the page cannot be fetched, nothing is written and nothing is cited.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

from uwcopilot.common.files import append_checksum_log
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.data.errors import StudioDataError
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.paths import CHECKSUM_LOG, REFERENCE_DIR

SBA_URL = "https://www.sba.gov/business-guide/plan-your-business/write-your-business-plan"
LIST_HEADING = "Common traditional business plan sections"
RECORD_FILE = CONFIG_DIR / "studio" / "sba_business_plan_sections.json"
BODY_FILE = REFERENCE_DIR / "sba_write_your_business_plan.html"
GENERATED_BY = "scripts/studio_fetch_sba_plan_guide.py"
HEADING = re.compile(r"<h(?P<level>[1-6])\b[^>]*>(?P<text>.*?)</h(?P=level)>", re.IGNORECASE | re.DOTALL)
TAG = re.compile(r"<[^>]+>")


class SectionListNotFound(ValueError):
    """The page has no "Common traditional business plan sections" heading followed by h5 section names."""


def heading_text(raw: str) -> str:
    """The visible text of a heading: tags removed, entities decoded, spaces collapsed."""
    return " ".join(html.unescape(TAG.sub("", raw)).split())


def page_headings(page: str) -> list[tuple[int, str]]:
    """(level, text) of every heading of the page, in order."""
    return [(int(match.group("level")), heading_text(match.group("text"))) for match in HEADING.finditer(page)]


def list_start(headings: list[tuple[int, str]]) -> int:
    """Index of the LIST_HEADING h4 (SectionListNotFound when the page has none)."""
    starts = [index for index, (level, text) in enumerate(headings) if level == 4 and text == LIST_HEADING]
    if not starts:
        raise SectionListNotFound(f"no h4 heading {LIST_HEADING!r} on the page")
    return starts[0]


def section_names(page: str) -> list[str]:
    """The h5 headings after the LIST_HEADING h4, up to the next heading of level 4 or higher."""
    headings = page_headings(page)
    names = []
    for level, text in headings[list_start(headings) + 1 :]:
        if level <= 4:
            break
        if level == 5 and text:
            names.append(text)
    if not names:
        raise SectionListNotFound(f"no h5 section names after {LIST_HEADING!r}")
    return names


def page_context(page: str) -> tuple[str | None, str | None]:
    """(the page's first h1, the last h2 before the list heading): where the list sits on the page."""
    headings = page_headings(page)
    start = list_start(headings)
    title = next((text for level, text in headings if level == 1), None)
    parent = next((text for level, text in reversed(headings[:start]) if level == 2), None)
    return title, parent


def build_record(client: PoliteClient) -> tuple[dict[str, Any], bytes, bool]:
    """Fetch the page; return the record to write, the page body and whether it came from the cache."""
    fetched = client.get(SBA_URL)
    names = section_names(fetched.text())
    title, parent = page_context(fetched.text())
    record = {
        "publisher": "U.S. Small Business Administration",
        "page_h1": title,
        "section_h2": parent,
        "requested_url": SBA_URL,
        "final_url": fetched.final_url or fetched.url,
        "retrieved_at": fetched.retrieved_at.isoformat(),
        "sha256": fetched.sha256,
        "status_code": fetched.status_code,
        "list_heading": LIST_HEADING,
        "sections": names,
        "generated_by": GENERATED_BY,
        "note": (
            "Section names read from the page's h5 headings under the list heading. The page body is kept under "
            "data/studio/reference/ (gitignored); its SHA-256 is also in data/studio/checksums.jsonl."
        ),
    }
    return record, fetched.body, fetched.from_cache


def main(argv: list[str] | None = None) -> int:
    """Fetch, parse and write the record (or print it with --dry-run)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the record, write nothing")
    parser.add_argument("--output", type=Path, default=RECORD_FILE)
    args = parser.parse_args(argv)
    try:
        record, body, from_cache = build_record(PoliteClient())
    except (StudioDataError, SectionListNotFound) as error:
        print(f"Not written: {error}. Do not cite the SBA page until this works.", file=sys.stderr)
        return 1
    text = json.dumps(record, indent=2) + "\n"
    if args.dry_run:
        print(text)
        return 0
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(BODY_FILE, body)
    action = "reused_cache" if from_cache else "downloaded"
    append_checksum_log(CHECKSUM_LOG, "sba_write_your_business_plan", BODY_FILE, SBA_URL, action)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output} ({len(record['sections'])} sections, sha256 {record['sha256'][:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
