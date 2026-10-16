"""Section 11: the evidence appendix: every claim of the ledger with its value, range, status and source, and every
check with its result.

Claims are grouped by where they come from (what the founder stated, simulator inputs, the site, the simulation,
the sensitivity analysis, the path search, program thresholds and targets, calculations). Each row has a number
[k]: every value printed elsewhere in the package links to the row of its claim, and paragraphs cite "[k]".
"""

from uwcopilot.evidence.ledger_models import Claim, Ledger
from uwcopilot.studio.package.blocks import check, claim, reference, source, status, text, value_range
from uwcopilot.studio.package.constants import STATUS_MEANINGS, STATUS_WORDS
from uwcopilot.studio.package.models import Block, Cell, Paragraph, Table
from uwcopilot.studio.package.runs import RunIndex
from uwcopilot.studio.package.sba import sba_citation
from uwcopilot.studio.package.text import clean_label

# (group title, id prefixes), in print order. A claim goes to the first group whose prefix it starts with.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("What the founder stated, and documents", ("PLAN.", "FOUNDER.", "DOC.", "PKG.business_name", "PKG.owner.")),
    ("Simulator inputs and assumptions", ("ASSUME.", "RATE.")),
    ("Site, location and candidate sites", ("SITE.", "PKG.site.", "PKG.map.")),
    ("Simulation outputs", ("SIM.", "STRESS.", "PKG.sim.")),
    ("Sensitivity analysis (tornado)", ("TORNADO.",)),
    ("Path-to-approval search", ("PATH.",)),
    ("Program thresholds and Studio targets", ("PROGRAM.", "TARGET.")),
    ("Calculations", ("CALC.", "PKG.loan.")),
)
OTHER_GROUP = "Other claims"


def group_of(claim_id: str) -> str:
    """The appendix group of a claim id."""
    for title, prefixes in GROUPS:
        if claim_id.startswith(prefixes):
            return title
    return OTHER_GROUP


def ordered_claims(ledger: Ledger) -> list[tuple[str, list[Claim]]]:
    """(group title, its claims in ledger order) for every non-empty group, in GROUPS order."""
    titles = [title for title, _ in GROUPS] + [OTHER_GROUP]
    grouped: dict[str, list[Claim]] = {title: [] for title in titles}
    for item in ledger.claims:
        grouped[group_of(item.id)].append(item)
    return [(title, grouped[title]) for title in titles if grouped[title]]


def appendix_index(ledger: Ledger) -> dict[str, int]:
    """Claim id -> its row number [k] in the appendix (1, 2, ... in print order)."""
    index: dict[str, int] = {}
    for _, claims in ordered_claims(ledger):
        for item in claims:
            index[item.id] = len(index) + 1
    return index


def claims_table(ledger: Ledger) -> Table:
    """Every claim, grouped, with its value, range, status and source."""
    rows: list[list[Cell]] = []
    group_rows: list[int] = []
    for title, claims in ordered_claims(ledger):
        group_rows.append(len(rows))
        rows.append([text(title, bold=True), *[text("")] * 5])
        for item in claims:
            rows.append(
                [
                    reference(item.id),
                    text(clean_label(item.label)),
                    claim(item.id),
                    value_range(item.id),
                    status(item.id),
                    source(item.id),
                ]
            )
    return Table(
        title="Claims of the founder ledger",
        header=["Row and claim id", "What it is", "Value", "Range", "Status", "Source (reference)"],
        rows=rows,
        widths=[1.75, 1.85, 0.85, 1.05, 0.75, 2.45],
        align=["left", "left", "right", "right", "left", "left"],
        size="tiny",
        group_rows=group_rows,
        note="A model claim names its run; the runs table above lists each run's settings and shared notes once.",
    )


def settings_text(details: dict) -> str:
    """A run's settings as a bracketed reference ('[seed 4404; n_paths 1000; plan_sha256 626fc552e615; ...]')."""
    parts = []
    for key, value in sorted(details.items()):
        shown = str(value)
        if key.endswith("sha256"):
            shown = shown[:12]
        parts.append(f"{key} {shown}")
    return "[" + "; ".join(part.replace("[", "(").replace("]", ")") for part in parts) + "]"


def runs_table(runs: RunIndex) -> Table | None:
    """Each model run once: its model, version, settings and the uncertainty notes its claims share."""
    if not runs.runs:
        return None
    rows = []
    for run in runs.runs:
        notes = "; ".join(run.common_notes)
        rows.append(
            [
                text(run.label, bold=True),
                text(f"{run.model_name} [{run.model_version}]"),
                text(settings_text(run.details)),
                text(f"[{notes}]" if notes else ""),
            ]
        )
    return Table(
        title="Model runs behind the simulated claims",
        header=["Run", "Model and version", "Settings", "Shared uncertainty notes"],
        rows=rows,
        widths=[0.7, 1.6, 3.4, 2.3],
        align=["left", "left", "left", "left"],
        size="tiny",
    )


def checks_table(ledger: Ledger) -> Table:
    """Every assertion: id, rule, sections, result and the claims it read."""
    rows = []
    for assertion in ledger.assertions:
        sections = ", ".join(assertion.section_ids) or "Studio target"
        basis = ", ".join(assertion.evidence_ids)
        rows.append(
            [
                text(f"[{assertion.id}]"),
                text(assertion.expression),
                text(f"[{sections}]"),
                check(assertion.id),
                text(f"[{basis}]" if basis else ""),
            ]
        )
    return Table(
        title="Checks (assertions) of the founder ledger",
        header=["Check", "What is checked", "Sections", "Result", "Claims it read"],
        rows=rows,
        widths=[1.2, 2.2, 1.1, 2.0, 1.5],
        align=["left", "left", "left", "left", "left"],
        size="tiny",
    )


def legend_table() -> Table:
    """The six statuses and what they mean."""
    rows = [
        [text(STATUS_WORDS[key], bold=True), text(f"[{key}]"), text(meaning)]
        for key, meaning in STATUS_MEANINGS.items()
    ]
    return Table(
        title="Status legend",
        header=["Status", "Ledger code", "Meaning"],
        rows=rows,
        widths=[1.6, 1.6, 4.8],
        align=["left", "left", "left"],
        size="small",
    )


def appendix_section(ledger: Ledger, package_id: str, ledger_version: str, runs: RunIndex) -> list[Block]:
    """Section 11 (module docstring)."""
    intro = (
        "Every number in this package is a claim of one founder ledger, printed from it by the renderer. Each claim "
        "has a status, a source and a SHA-256 of its inputs. Figures elsewhere link to their row here."
    )
    return [
        Paragraph(draft=intro, style="lead"),
        Paragraph(
            draft=(
                f"Package [{package_id}], ledger [{ledger.subject_id}], ledger version [{ledger_version}], data label "
                f"{ledger.data_label}. Section names follow [{sba_citation()}]."
            ),
            style="note",
        ),
        legend_table(),
        *([table] if (table := runs_table(runs)) is not None else []),
        claims_table(ledger),
        checks_table(ledger),
    ]
