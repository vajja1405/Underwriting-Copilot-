"""Evidence coverage rules S and E (docs/studio/s12_baseline.md, M2-M4) on hand-made claims and a golden package.

Everything is SYNTHETIC and offline.
"""

import pytest
from package_helpers import package, project

from uwcopilot.evidence.ledger_models import Claim, Ledger, Provenance, ValueRange, unknown_claim
from uwcopilot.studio.business_metrics.baseline import BaselineFigure, baseline_plan, baseline_setup
from uwcopilot.studio.business_metrics.coverage import (
    baseline_coverage,
    figure_breakdown,
    founder_figure_count,
    has_checkable_source,
    has_external_evidence,
    is_figure,
    package_coverage,
)

METRO, TYPE = "synthetic_metro", "laundromat"  # document variant 1: matching bank statements


def verified(claim_id: str, kind: str, source: str | None = None) -> Claim:
    """A VERIFIED figure of one provenance kind."""
    return Claim(
        id=claim_id,
        label=claim_id,
        value=1.0,
        unit="USD",
        display="usd0",
        status="VERIFIED",
        provenance=Provenance(kind=kind, ref=claim_id, source=source),
    )


def model_estimate(claim_id: str = "SIM.y1") -> Claim:
    """A MODEL_ESTIMATE figure."""
    return Claim(
        id=claim_id,
        label="simulated",
        value=2.0,
        unit="USD",
        display="usd0",
        status="MODEL_ESTIMATE",
        provenance=Provenance(kind="model", ref="sim", model_name="studio-sim", model_version="1"),
        uncertainty="P10-P90",
    )


def assumption(claim_id: str, source: str) -> Claim:
    """An ASSUMPTION figure with a range and a source (or 'unsourced')."""
    return Claim(
        id=claim_id,
        label=claim_id,
        value=0.1,
        unit="RATIO",
        display="ratio2",
        status="ASSUMPTION",
        provenance=Provenance(kind="assumption", ref=claim_id, source=source),
        value_range=ValueRange(low=0.0, high=0.2),
    )


def stated(claim_id: str = "FOUNDER.x", display: str = "usd0") -> Claim:
    """A USER_STATED claim (a figure, or text)."""
    value: float | str = "words" if display == "text" else 3.0
    return Claim(
        id=claim_id,
        label=claim_id,
        value=value,
        unit="TEXT" if display == "text" else "USD",
        display=display,
        status="USER_STATED",
        provenance=Provenance(kind="user_input", ref=claim_id),
    )


def conflicting(claim_id: str, kind: str, other: str) -> Claim:
    """A CONFLICTING claim of one kind, linked to another."""
    return Claim(
        id=claim_id,
        label=claim_id,
        value=4.0,
        unit="USD",
        display="usd0",
        status="CONFLICTING",
        provenance=Provenance(kind=kind, ref=claim_id),
        conflict_ids=(other,),
    )


@pytest.mark.parametrize(
    ("claim", "sourced", "external"),
    [
        (verified("V.doc", "document"), True, True),
        (verified("V.data", "public_data", "Census ACS (SYNTHETIC test)"), True, True),
        (verified("V.policy", "policy"), True, True),
        (verified("V.calc", "calculation"), True, False),
        (model_estimate(), True, False),
        (assumption("A.named", "config/studio/feasibility/assumptions.yaml"), True, False),
        (assumption("A.none", "unsourced"), False, False),
        (stated(), False, False),
        (conflicting("C.founder", "user_input", "C.doc"), False, False),
        (conflicting("C.doc", "document", "C.founder"), True, True),
        (unknown_claim("U.x", "missing", "USD", "usd0", "not read"), False, False),
    ],
)
def test_rules_s_and_e_follow_the_written_definition(claim: Claim, sourced: bool, external: bool) -> None:
    assert has_checkable_source(claim) is sourced
    assert has_external_evidence(claim) is external


def test_text_claims_are_not_figures_and_unknown_claims_are() -> None:
    assert not is_figure(stated("PLAN.name", "text"))
    assert is_figure(unknown_claim("U.x", "missing", "USD", "usd0", "not read"))


def test_package_coverage_counts_figures_only() -> None:
    ledger = Ledger(
        claims=(
            verified("V.doc", "document"),
            model_estimate(),
            assumption("A.none", "unsourced"),
            stated("PLAN.name", "text"),
            stated(),
        )
    )
    count = package_coverage(ledger)
    assert (count.figures, count.sourced, count.external) == (4, 2, 1)
    assert figure_breakdown(ledger) == {
        "ASSUMPTION/assumption/not sourced": 1,
        "MODEL_ESTIMATE/model/sourced": 1,
        "USER_STATED/user_input/not sourced": 1,
        "VERIFIED/document/sourced": 1,
    }


def test_baseline_coverage_counts_document_citations_under_both_rules() -> None:
    figures = [
        BaselineFigure("plan.owner_equity", "founder", 1.0, "SYN-STATEMENTS p1"),
        BaselineFigure("plan.capex", "founder", 2.0),
        BaselineFigure("y1.revenue", "result", 3.0),
    ]
    count = baseline_coverage(figures)
    assert (count.figures, count.sourced, count.external) == (3, 1, 1)


def test_founder_figures_are_backed_equally_in_both_plans_on_a_golden_package() -> None:
    built = package(METRO, TYPE)
    plan = project(METRO, TYPE)
    baseline = baseline_plan(plan.case, plan.documents, baseline_setup(plan.case, 36))
    count = founder_figure_count(built.ledger, plan.case, baseline)
    assert count.figures > 0 and count.studio_backed == count.baseline_backed == 1
    assert count.studio_conflicting == 0


def test_a_package_has_more_figures_with_a_source_than_its_baseline() -> None:
    built = package(METRO, TYPE)
    plan = project(METRO, TYPE)
    studio = package_coverage(built.ledger)
    base = baseline_coverage(baseline_plan(plan.case, plan.documents, baseline_setup(plan.case, 36)))
    assert studio.figures > base.figures > 0
    assert 0 < studio.external <= studio.sourced <= studio.figures
    assert base.sourced == base.external == 1
