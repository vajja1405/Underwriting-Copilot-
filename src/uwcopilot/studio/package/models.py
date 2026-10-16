"""The founder package as data (schema "founder-package-v1"): what build_package returns and render_pdf prints.

A Package is plain, JSON-serializable data. It holds the ONE founder ledger, the rendered locked narrative and the
package's sections as BLOCKS that point into the ledger instead of holding numbers:
- Paragraph: a slot-only draft ({{claim:ID}} / {{assert:ID}} slots, [NSP-x.y] citations), filled by the provenance
  renderer when the package is printed;
- Table: rows of Cells. A cell is layout text (column labels, plain words), a claim (its value as the renderer
  prints it), a claim's status, range or source, an assertion's result, or a small slot draft;
- KeyFacts: label + claim pairs; Bullets: slot drafts; Figure: a map or chart drawn from ledger values (the map from
  the package's GeoJSON layer).
So every number the package shows is typed from the ledger at render time, and the audit can re-read it.
ApplicantInfo and the material assumptions are what to_application needs besides the ledger.
"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.applications import CollateralItem
from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.narrative.models import NarrativeResult
from uwcopilot.studio.package.constants import (
    FULL_DISCLAIMER,
    PACKAGE_BUILDER_VERSION,
    PACKAGE_SCHEMA_VERSION,
    PACKAGE_TITLE,
    PRODUCT_NAME,
    SIMULATION_LABEL,
)
from uwcopilot.studio.package.project import PackageOwner
from uwcopilot.studio.package.runs import RunIndex

CellKind = Literal["text", "claim", "status", "range", "source", "assert", "draft", "ref"]


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Cell(Frozen):
    """One table cell. kind says what it prints:
    text   - `text` as it is (layout words: column labels, names of things; no figures);
    claim  - the claim `ref`'s value in its display format (linked to its appendix row);
    status - the claim's status in plain words; range - its value range ("low to high"); source - its provenance
             in square brackets (a reference, like a citation);
    assert - the assertion `ref`'s result phrase and whether it passed;
    draft  - `text` is a slot draft, filled by the renderer;
    ref    - the claim id `ref` and its appendix number, in square brackets."""

    kind: CellKind
    text: str = ""
    ref: str = ""
    bold: bool = False


class Paragraph(Frozen):
    """A slot-only draft (or plain words) rendered from the ledger."""

    kind: Literal["paragraph"] = "paragraph"
    draft: str
    style: Literal["body", "note", "lead", "heading"] = "body"


class Table(Frozen):
    """A table of cells. widths are relative column widths; align is per column."""

    kind: Literal["table"] = "table"
    title: str = ""
    header: list[str]
    rows: list[list[Cell]]
    widths: list[float]
    align: list[Literal["left", "right", "center"]]
    note: str = Field(default="", description="A slot draft printed under the table (for example its status)")
    size: Literal["normal", "small", "tiny"] = "normal"
    group_rows: list[int] = Field(default_factory=list, description="Row indexes printed as group headings")


class KeyFact(Frozen):
    """One labelled figure of a key-facts box."""

    label: str
    ref: str


class KeyFacts(Frozen):
    """A box of labelled figures (two per row)."""

    kind: Literal["key_facts"] = "key_facts"
    title: str = ""
    items: list[KeyFact]


class Bullets(Frozen):
    """A list of slot drafts."""

    kind: Literal["bullets"] = "bullets"
    items: list[str]


class Figure(Frozen):
    """A figure drawn at render time from ledger values (map: from Package.map)."""

    kind: Literal["figure"] = "figure"
    figure: Literal["map", "cash_band", "tornado"]
    caption: str = Field(description="A slot draft")
    refs: list[str] = Field(default_factory=list, description="The claims the figure plots, in order")
    labels: list[str] = Field(default_factory=list, description="Axis labels (for example YYYY-MM months)")
    height_in: float = 3.0


Block = Annotated[Paragraph | Table | KeyFacts | Bullets | Figure, Field(discriminator="kind")]


class Section(Frozen):
    """One numbered section of the package and the SBA sections it covers."""

    key: str
    number: int
    title: str
    sba_sections: list[str]
    blocks: list[Block]


class MapLayer(Frozen):
    """The static map's data: a GeoJSON FeatureCollection (tract centroids, the site, the rings), no tiles."""

    geojson: dict[str, Any]
    site_lat: float
    site_lon: float
    value_label: str = Field(description="What the tract dots are colored by (words)")
    near_ref: str | None = None
    market_ref: str | None = None


class ApplicantInfo(Frozen):
    """What the lender's application needs besides the ledger (codes and the founder's own text)."""

    business_name: str
    business_type: str
    metro_id: str
    metro_name: str
    metro_mode: str
    entity_type: str
    state: str
    formation_date: date | None
    planned_opening_date: date
    site_label: str
    loan_purpose: str
    owners: list[PackageOwner]
    documents_provided: list[str]
    other_debt_kinds: list[str]
    collateral: list[CollateralItem]


class MaterialAssumption(Frozen):
    """One simulator input as the startup program's assumption list needs it (NSP-4.3, NSP-6.1)."""

    name: str
    claim_id: str
    value: float
    unit: str
    status: str
    source: str
    supporting_document_types: list[str] = Field(default_factory=list)
    tornado_rank: int | None = None


class RunIdentity(Frozen):
    """The simulation run behind the package."""

    simulator_version: str
    seed: int
    n_paths: int
    months: int
    plan_sha256: str
    dscr_target: float = Field(gt=0, description="The DSCR the simulation counts as covered (FeasibilityResult)")
    path_engine_version: str | None = None


class PackageAudit(Frozen):
    """The round-trip audit of the package text: numbers read back and those that trace to no ledger claim."""

    numbers: int
    untraced: list[str]
    value_cells: int
    sections: int


class Package(Frozen):
    """A lender-ready founder package (module docstring). Versioned: schema_version founder-package-v1."""

    schema_version: Literal["founder-package-v1"] = PACKAGE_SCHEMA_VERSION
    builder_version: str = PACKAGE_BUILDER_VERSION
    package_id: str
    product: str = PRODUCT_NAME
    title: str = PACKAGE_TITLE
    data_label: str
    disclaimer: str = FULL_DISCLAIMER
    simulation_label: str = SIMULATION_LABEL
    prepared_on: date
    project_id: str
    inputs_sha256: str
    applicant: ApplicantInfo
    run: RunIdentity
    ledger: Ledger
    narrative: NarrativeResult
    sections: list[Section]
    appendix_index: dict[str, int] = Field(description="Claim id -> its row number in the evidence appendix")
    runs: RunIndex = Field(description="The model runs behind the MODEL_ESTIMATE claims (listed once in the appendix)")
    map: MapLayer | None = None
    material_assumptions: list[MaterialAssumption]
    sba: dict[str, Any] = Field(description="The SBA page record the section names were read from")
    audit: PackageAudit
    notes: list[str] = Field(default_factory=list)

    def section(self, key: str) -> Section | None:
        """The section with this key, or None."""
        return next((item for item in self.sections if item.key == key), None)
