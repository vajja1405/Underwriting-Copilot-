"""Small constructors for package blocks and cells, and the BuildContext every section builder reads.

The section builders (sections_*.py) only arrange claim ids and plain words; they never format a number. These
helpers keep that short: claim(...) is a cell that prints a claim's value, slot(...) is "{{claim:ID}}" inside a
draft, and BuildContext answers "does the ledger have this claim?" so a row is shown only when its claims exist.
"""

from dataclasses import dataclass, field
from datetime import date

from uwcopilot.evidence.ledger_models import Assertion, Claim, Ledger
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative.models import NarrativeResult
from uwcopilot.studio.package.models import Cell
from uwcopilot.studio.package.project import FounderProject
from uwcopilot.studio.path_to_approval.models import PathResult
from uwcopilot.studio.siteiq.models import SiteScore


def text(words: str, bold: bool = False) -> Cell:
    """A layout cell: plain words (no figures)."""
    return Cell(kind="text", text=words, bold=bold)


def claim(claim_id: str, bold: bool = False) -> Cell:
    """A cell that prints the claim's value."""
    return Cell(kind="claim", ref=claim_id, bold=bold)


def status(claim_id: str) -> Cell:
    """A cell that prints the claim's status in plain words."""
    return Cell(kind="status", ref=claim_id)


def value_range(claim_id: str) -> Cell:
    """A cell that prints the claim's range ("low to high"), empty when it has none."""
    return Cell(kind="range", ref=claim_id)


def source(claim_id: str) -> Cell:
    """A cell that prints the claim's provenance in square brackets."""
    return Cell(kind="source", ref=claim_id)


def check(assertion_id: str) -> Cell:
    """A cell that prints an assertion's result phrase."""
    return Cell(kind="assert", ref=assertion_id)


def draft(slot_text: str, bold: bool = False) -> Cell:
    """A cell holding a slot draft (filled by the renderer)."""
    return Cell(kind="draft", text=slot_text, bold=bold)


def reference(claim_id: str) -> Cell:
    """A cell that prints the claim id and its appendix number in square brackets."""
    return Cell(kind="ref", ref=claim_id)


def slot(claim_id: str) -> str:
    """A claim slot for a draft."""
    return "{{claim:" + claim_id + "}}"


def assert_slot(assertion_id: str) -> str:
    """An assert slot for a draft."""
    return "{{assert:" + assertion_id + "}}"


@dataclass
class BuildContext:
    """What every section builder reads: the project, its ledger, the narrative and a few lookups."""

    project: FounderProject
    ledger: Ledger
    narrative: NarrativeResult
    metro_name: str
    business_noun: str
    calendar_months: list[str] = field(default_factory=list)
    claims: dict[str, Claim] = field(default_factory=dict)
    assertions: dict[str, Assertion] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Index claims and assertions by id."""
        self.claims = {item.id: item for item in self.ledger.claims}
        self.assertions = {item.id: item for item in self.ledger.assertions}

    @property
    def feasibility(self) -> FeasibilityResult:
        """The simulation of the chosen site."""
        return self.project.feasibility

    @property
    def path_result(self) -> PathResult | None:
        """The path-to-targets search, when one was run."""
        return self.project.path_result

    @property
    def site_score(self) -> SiteScore | None:
        """SiteIQ's score of the chosen site, when there is one."""
        return self.project.site_score

    @property
    def prepared_on(self) -> date:
        """The package date."""
        return self.project.prepared_on

    def has(self, *claim_ids: str) -> bool:
        """True when the ledger has every one of these claims."""
        return all(item in self.claims for item in claim_ids)

    def has_check(self, assertion_id: str) -> bool:
        """True when the ledger has this assertion."""
        return assertion_id in self.assertions

    def first(self, *claim_ids: str) -> str | None:
        """The first of these claim ids the ledger has (None when it has none)."""
        return next((item for item in claim_ids if item in self.claims), None)

    def value(self, claim_id: str) -> object:
        """A claim's value (None when the claim is missing or has none)."""
        item = self.claims.get(claim_id)
        return None if item is None else item.value
