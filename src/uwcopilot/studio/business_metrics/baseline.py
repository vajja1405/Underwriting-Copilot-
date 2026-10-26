"""The baseline B0: the plain plan a founder could print without SiteIQ, the simulation and the ledger.

docs/studio/s12_baseline.md (section 2) defines B0; this module computes it. B0 is a construction from the same
SYNTHETIC inputs as the Studio package, not a sample of real plans. It prints three groups of figures:

1. founder: every plan and loan figure the founder typed (founder_claims.FIELD_SPECS; the owner draw only when it is
   not zero);
2. input: every input of the one-forecast projection the founder did not type, at its most likely value (the
   probability and the size of each of the three shocks - competitor entry, a rate rise on a variable-rate loan and a
   one-off event - and the monthly noise are left out: a one-forecast plan never has a shock, and a shock's size only
   acts when the shock happens); the note rate when the founder did not type it;
3. result: years 1-3 revenue, net operating income, debt service (loan plus other debt) and DSCR of the one-forecast
   run (feasibility.model.single_point_run: one deterministic path, every input at its most likely value).

The source rule: a founder figure carries a source when at least one document is about its field and every such
document prints the same value (founder_claims.same_value, the ledger's own tolerance). No other B0 figure carries a
source: B0 has no ledger to record where a default came from or how a result was calculated.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from uwcopilot.studio.feasibility.model import single_point_run
from uwcopilot.studio.feasibility.setup import SimulationSetup
from uwcopilot.studio.narrative.founder_claims import FIELD_FOR_INPUT, FIELD_SPECS, plan_value, same_value
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.path_to_approval.evaluate import setup_for_case, with_other_debt
from uwcopilot.studio.path_to_approval.models import PlanCase
from uwcopilot.studio.path_to_approval.search import default_assumptions, fabric_for, profile_for

FigureGroup = Literal["founder", "input", "result"]
OWNER_DRAW_FIELD = "plan.owner_draw_per_month"
NOTE_RATE_FIELD = "loan.annual_rate_pct"
# Inputs a one-forecast plan does not use: it has no shocks and no month-to-month noise. Every input of the three
# shocks (feasibility.setup.risk_inputs) starts with one of these prefixes; a shock's size only acts inside the shock.
UNUSED_PREFIXES: tuple[str, ...] = ("competitor_entry_", "rate_shock_", "one_off_shock_")
UNUSED_SUFFIXES: tuple[str, ...] = ("_annual_probability",)
UNUSED_NAMES: tuple[str, ...] = ("monthly_noise_sd",)
MONTHS_PER_YEAR = 12
RESULT_YEARS: tuple[int, ...] = (1, 2, 3)


@dataclass(frozen=True)
class BaselineFigure:
    """One figure B0 prints: its name, group, value and the document it can cite (None: no source)."""

    name: str
    group: FigureGroup
    value: float
    source: str | None = None

    @property
    def has_source(self) -> bool:
        """True when the figure can cite a document (B0's source rule)."""
        return self.source is not None


def document_citation(documents: Sequence[FounderDocumentValue]) -> str:
    """'SYN-STATEMENTS p1; SYN-LOI p1': where the documents print the value."""
    parts = []
    for item in documents:
        page = f" p{item.page}" if item.page is not None else ""
        parts.append(f"{item.doc_id}{page}")
    return "; ".join(parts)


def backing_documents(
    field: str, value: float, documents: Sequence[FounderDocumentValue]
) -> list[FounderDocumentValue] | None:
    """The documents about this field when there is at least one and every one prints the same value, else None."""
    matching = [item for item in documents if item.field == field]
    if not matching:
        return None
    if all(same_value(field, value, item.value) for item in matching):
        return matching
    return None


def typed_fields(case: PlanCase) -> list[tuple[str, float]]:
    """(field, value) of every plan and loan figure the founder typed (module docstring, group 1)."""
    typed = []
    for field, *_ in FIELD_SPECS:
        value = plan_value(case, field)
        if value is None:
            continue
        if field == OWNER_DRAW_FIELD and not value:
            continue
        typed.append((field, float(value)))
    return typed


def founder_figures(case: PlanCase, documents: Sequence[FounderDocumentValue]) -> list[BaselineFigure]:
    """Group 1: the founder's typed figures, each with a document citation when the source rule allows one."""
    figures = []
    for field, value in typed_fields(case):
        backing = backing_documents(field, value, documents)
        source = document_citation(backing) if backing else None
        figures.append(BaselineFigure(name=field, group="founder", value=value, source=source))
    return figures


def used_by_one_forecast(input_name: str) -> bool:
    """False for the inputs a one-forecast plan does not use (shocks and noise)."""
    if input_name in UNUSED_NAMES:
        return False
    if input_name.startswith(UNUSED_PREFIXES) or input_name.endswith(UNUSED_SUFFIXES):
        return False
    return True


def founder_typed_input(case: PlanCase, input_name: str) -> bool:
    """True when the founder typed the plan field that replaces this simulator input."""
    field = FIELD_FOR_INPUT.get(input_name)
    return field is not None and plan_value(case, field) is not None


def input_figures(case: PlanCase, setup: SimulationSetup) -> list[BaselineFigure]:
    """Group 2: the inputs the founder did not type, at their most likely value (no source)."""
    figures = []
    for name, distribution in setup.distributions.items():
        if not used_by_one_forecast(name) or founder_typed_input(case, name):
            continue
        figures.append(BaselineFigure(name=name, group="input", value=float(distribution.mode)))
    if plan_value(case, NOTE_RATE_FIELD) is None:
        figures.append(BaselineFigure(name="note_rate_pct", group="input", value=float(setup.loan.annual_rate_pct)))
    return figures


def result_figures(case: PlanCase, setup: SimulationSetup) -> list[BaselineFigure]:
    """Group 3: years 1-3 revenue, NOI, debt service and DSCR of the one-forecast run (no source)."""
    paths = with_other_debt(single_point_run(setup), case.facts.other_debt_monthly_payment)
    figures = []
    for year in RESULT_YEARS:
        start, end = (year - 1) * MONTHS_PER_YEAR, year * MONTHS_PER_YEAR
        if end > setup.months:
            break
        revenue = float(paths.revenue[0, start:end].sum())
        noi = float(paths.noi[0, start:end].sum())
        debt = float(paths.debt_service[0, start:end].sum())
        figures.append(BaselineFigure(name=f"y{year}.revenue", group="result", value=revenue))
        figures.append(BaselineFigure(name=f"y{year}.noi", group="result", value=noi))
        figures.append(BaselineFigure(name=f"y{year}.debt_service", group="result", value=debt))
        if debt > 0:
            figures.append(BaselineFigure(name=f"y{year}.dscr", group="result", value=noi / debt))
    return figures


def baseline_setup(case: PlanCase, months: int) -> SimulationSetup:
    """The S3 setup of the plan, built the way the path search builds it (same inputs as the Studio run)."""
    fabric = fabric_for(case.metro_id)
    profile = profile_for(case.profile_id, case.metro_id)
    return setup_for_case(case, profile, fabric.metro_inputs(profile), fabric.data_label, default_assumptions(), months)


def baseline_plan(
    case: PlanCase, documents: Sequence[FounderDocumentValue], setup: SimulationSetup
) -> list[BaselineFigure]:
    """Every figure B0 prints for one plan, in group order (module docstring)."""
    return [*founder_figures(case, documents), *input_figures(case, setup), *result_figures(case, setup)]
