"""The words of the cover page and the contents, shared by render_pdf and the audit (so the audit reads the cover).

Cover: the product name, the package title, the business name (the founder's text, from the ledger), what and
where, the date, the package id (a reference, in brackets), the SYNTHETIC banner when the data label is SYNTHETIC,
the disclaimer, the simulation label and the no-decision note. Contents: every section with the SBA business plan
sections it covers, and the SBA sections the package does not cover (shown, never hidden).
"""

from uwcopilot.studio.package.constants import NO_DECISION_NOTE, SIMULATION_NOTE, SYNTHETIC_BANNER
from uwcopilot.studio.package.models import Package
from uwcopilot.studio.package.sba import sba_citation, uncovered_sba_sections
from uwcopilot.studio.package.text import value_text


def is_synthetic(package: Package) -> bool:
    """True when the package's data label is SYNTHETIC."""
    return package.data_label == "SYNTHETIC"


def business_name(package: Package) -> str:
    """The business name as the ledger holds it."""
    item = package.ledger.claim("PKG.business_name")
    return value_text(item) if item is not None else package.applicant.business_name


def subtitle(package: Package) -> str:
    """'A car wash in Synthetic Metro (SYNTHETIC)' (plain words from the configs)."""
    noun = package.applicant.business_type.replace("_", " ")
    return f"{noun.capitalize()} plan in {package.applicant.metro_name}"


def cover_lines(package: Package) -> dict[str, str]:
    """Every text block of the cover by its role."""
    lines = {
        "product": package.product,
        "title": package.title,
        "business": business_name(package),
        "subtitle": subtitle(package),
        "prepared": f"Prepared on {package.prepared_on.isoformat()}",
        "package_id": f"[Package {package.package_id}, schema {package.schema_version}]",
        "disclaimer": package.disclaimer,
        "simulation": SIMULATION_NOTE,
        "no_decision": NO_DECISION_NOTE,
        "mode": f"Metro data mode: {package.applicant.metro_mode}",
    }
    if is_synthetic(package):
        lines["banner"] = SYNTHETIC_BANNER
    return lines


def contents_rows(package: Package) -> list[tuple[str, str, str]]:
    """(number, section title, SBA sections it covers) for the contents table."""
    return [
        (f"{section.number}.", section.title, ", ".join(section.sba_sections) or "(package addition)")
        for section in package.sections
    ]


def contents_note() -> str:
    """Which SBA sections are not covered, and where the names come from."""
    missing = uncovered_sba_sections()
    gap = f"Not covered by this package: {', '.join(missing)}. " if missing else ""
    return f"{gap}SBA section names as read from [{sba_citation()}]."
