"""The package's sections and how they line up with the SBA's traditional business plan sections.

The SBA section names are NOT typed here: scripts/studio_fetch_sba_plan_guide.py read them from the SBA's "Write
your business plan" guidance page and wrote config/studio/sba_business_plan_sections.json (requested URL, the URL
that answered, retrieval time, SHA-256 of the page). sba_record() reads that file; docs/studio/sources.md cites it.
Each package section names the SBA sections it covers, and a test checks every name against the record, so a
renamed or missing SBA section is caught instead of silently cited. The package's extra sections (stress tests,
risks, the path-to-approval history, the evidence appendix) name no SBA section.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from uwcopilot.common.paths import CONFIG_DIR

SBA_RECORD_FILE = CONFIG_DIR / "studio" / "sba_business_plan_sections.json"


@dataclass(frozen=True)
class SectionSpec:
    """One package section: its key, its title, and the SBA traditional plan sections it covers (may be empty)."""

    key: str
    title: str
    sba_sections: tuple[str, ...]


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec("executive_summary", "Executive summary", ("Executive summary",)),
    SectionSpec(
        "business",
        "Business, owners and plan",
        ("Company description", "Organization and management", "Service or product line"),
    ),
    SectionSpec("market", "Market and location analysis", ("Market analysis",)),
    SectionSpec("site_comparison", "Site comparison", ("Market analysis",)),
    SectionSpec("funding_request", "Funding request: sources and uses of funds", ("Funding request",)),
    SectionSpec("projections", "Financial projections", ("Financial projections",)),
    SectionSpec("loan", "Loan schedule and debt service coverage", ("Financial projections", "Funding request")),
    SectionSpec("stress", "Stress tests (the lender's levers)", ("Financial projections",)),
    SectionSpec("risks", "Risks and mitigations", ()),
    SectionSpec("path", "Path-to-approval history", ()),
    SectionSpec("appendix", "Evidence appendix", ()),
)


@lru_cache(maxsize=1)
def sba_record() -> dict[str, Any]:
    """The record the fetch script wrote (requested and final URL, retrieval time, SHA-256, section names)."""
    return json.loads(SBA_RECORD_FILE.read_text(encoding="utf-8"))


def sba_sections() -> list[str]:
    """The SBA's traditional business plan section names, in page order."""
    return list(sba_record()["sections"])


def covered_sba_sections() -> list[str]:
    """The SBA sections some package section covers, in the SBA's order."""
    covered = {name for spec in SECTION_SPECS for name in spec.sba_sections}
    return [name for name in sba_sections() if name in covered]


def uncovered_sba_sections() -> list[str]:
    """The SBA sections no package section covers (shown on the contents page, never hidden)."""
    covered = set(covered_sba_sections())
    return [name for name in sba_sections() if name not in covered]


def unknown_sba_names() -> list[str]:
    """SBA names used by SECTION_SPECS that the record does not list (empty when everything lines up)."""
    listed = set(sba_sections())
    return sorted({name for spec in SECTION_SPECS for name in spec.sba_sections if name not in listed})


def sba_citation() -> str:
    """'U.S. Small Business Administration, "Write your business plan" (on the page "Plan your business"), <url>,
    retrieved YYYY-MM-DD, page SHA-256 <first 12 hex>'."""
    record = sba_record()
    return (
        f'{record["publisher"]}, "{record["section_h2"]}" (on the page "{record["page_h1"]}"), {record["final_url"]}, '
        f"retrieved {record['retrieved_at'][:10]}, page SHA-256 {record['sha256'][:12]}"
    )
