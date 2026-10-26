"""Per-person resume lines from config/contributions.yaml (Studio S12).

Git history cannot say who built what: Claude Code is the implementer, so almost every commit is authored by
"Claude", and the owners' work (specs, build prompts, evaluation protocol, reviews) does not show up as commit
authorship. This module therefore never infers ownership. The owners list the components they own in
config/contributions.yaml; a person gets a bullet only for a component listed there. With the file empty, the output
says that the owners need to fill it in.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from uwcopilot.resume.text import Bullet, Omission

# The component ids an owner can list: one per resume bullet, in print order.
COMPONENTS: dict[str, str] = {
    "evidence_platform": "Lender Desk: evidence-driven copilot (contradictions, request policy, human checkpoint)",
    "credit_model": "Lender Desk: Lending Club credit default model",
    "insurance_models": "Lender Desk: freMTPL2 frequency-severity models",
    "document_extraction": "Lender Desk: document extraction, citations and injection measurements",
    "kyc_screening": "Lender Desk: sanctions screening",
    "governance": "Lender Desk: release gate and drift monitoring",
    "studio_platform": "Founder Studio: metros, data coverage and the web app",
    "studio_feasibility": "Founder Studio: Monte Carlo feasibility simulator",
    "studio_provenance": "Founder Studio: provenance-locked narrative, package and handoff",
    "studio_path": "Founder Studio: path-to-approval search",
    "studio_siteiq": "Founder Studio: SiteIQ site scoring",
    "studio_business": "Founder Studio: S12 end-to-end evaluation against the baseline B0",
}
# The only top-level key of config/contributions.yaml.
OWNERS_KEY = "owners"
FILL_IN_NOTE = (
    "Per-person lines need the owners to fill in config/contributions.yaml: list the component ids each of you owns "
    "(git history cannot say who built what, so this script never guesses). Until then, use the shared bullets above."
)
ROLE_NOTE = (
    "Wording describes the project, not who typed the code: the README's 'How it was built' says the owners wrote the "
    "specs, build prompts and evaluation protocol and reviewed the work, and Claude Code implemented it."
)


class ContributionsError(ValueError):
    """config/contributions.yaml is not in the expected shape or names an unknown component."""


@dataclass(frozen=True)
class Contributions:
    """Component ids per owner, in file order, and whether the file was found."""

    owners: dict[str, tuple[str, ...]]
    found: bool


def parse_contributions(data: Any) -> dict[str, tuple[str, ...]]:
    """{owner name: component ids} from the parsed YAML; ContributionsError on a bad shape or an unknown id.

    An empty file (or one with only comments) parses to None and means no owners. Otherwise the top level must be a
    mapping whose only key is "owners", so a misspelled key such as "owner:" is an error, not an empty file.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        found = type(data).__name__
        raise ContributionsError(f"the top level must be a mapping with the key {OWNERS_KEY!r} (found a {found})")
    unknown_keys = [key for key in data if key != OWNERS_KEY]
    if unknown_keys:
        raise ContributionsError(f"unknown top-level keys {unknown_keys}; the only key is {OWNERS_KEY!r}")
    owners = data.get(OWNERS_KEY)
    if owners is None:
        return {}
    if not isinstance(owners, dict):
        raise ContributionsError("'owners' must map each owner's name to a list of component ids")
    parsed: dict[str, tuple[str, ...]] = {}
    for name, listed in owners.items():
        components = listed if listed is not None else []
        if not isinstance(name, str) or not isinstance(components, list):
            raise ContributionsError(f"owner {name!r}: expected a name and a list of component ids")
        unknown = [item for item in components if item not in COMPONENTS]
        if unknown:
            raise ContributionsError(f"owner {name!r}: unknown component ids {unknown}; known ids: {list(COMPONENTS)}")
        parsed[name] = tuple(dict.fromkeys(components))
    return parsed


def load_contributions(path: Path) -> Contributions:
    """Read config/contributions.yaml; a missing file counts as empty, and invalid YAML is a ContributionsError."""
    if not path.exists():
        return Contributions(owners={}, found=False)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ContributionsError(f"not valid YAML: {error}") from error
    return Contributions(owners=parse_contributions(data), found=True)


def render_per_person(
    contributions: Contributions, bullets: list[Bullet], omissions: list[Omission]
) -> tuple[list[str], list[str]]:
    """The per-person section (stdout lines) and notes about listed components that have no bullet (stderr lines)."""
    lines = ["Per-person lines (from config/contributions.yaml)"]
    notes: list[str] = []
    if not contributions.found:
        notes.append("config/contributions.yaml does not exist: no per-person lines")
    if not any(contributions.owners.values()):
        lines.append(FILL_IN_NOTE)
        return lines, notes
    lines.append(ROLE_NOTE)
    by_id = {bullet.bullet_id: bullet for bullet in bullets}
    for owner, components in contributions.owners.items():
        lines.append("")
        lines.append(owner)
        if not components:
            lines.append("- (no components listed in config/contributions.yaml)")
            continue
        for component in components:
            bullet = by_id.get(component)
            if bullet is None:
                reasons = [item.reason for item in omissions if item.bullet_id == component]
                notes.append(f"{owner}: {component} has no bullet ({'; '.join(reasons) or 'not built'})")
                continue
            others = [name for name, ids in contributions.owners.items() if name != owner and component in ids]
            joint = f" [joint with {', '.join(others)}]" if others else ""
            lines.append(f"- {bullet.text}{joint}")
    return lines, notes
