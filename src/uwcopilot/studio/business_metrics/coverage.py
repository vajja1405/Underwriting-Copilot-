"""Evidence coverage of a Studio package and of the baseline B0 (docs/studio/s12_baseline.md, M2-M4).

A printed figure of a package is a ledger claim with a numeric display (every claim is printed in the evidence
appendix; an UNKNOWN claim prints "not available"). Two rules decide whether a figure carries a source:

- rule S (checkable source): VERIFIED, MODEL_ESTIMATE or CONFLICTING with provenance kind document, public_data,
  policy, calculation or model; or ASSUMPTION whose source is not "unsourced". USER_STATED, a CONFLICTING founder
  figure (kind user_input), an "unsourced" ASSUMPTION and UNKNOWN do not count;
- rule E (external evidence): VERIFIED or CONFLICTING with provenance kind document, public_data or policy.

A B0 figure carries a source under both rules exactly when it cites a document (baseline.BaselineFigure.has_source).
M4 compares the founder's typed figures only, the same population for both plans.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from uwcopilot.evidence.ledger_models import NUMERIC_DISPLAYS, UNSOURCED, Claim, Ledger
from uwcopilot.studio.business_metrics.baseline import BaselineFigure, typed_fields
from uwcopilot.studio.narrative.founder_claims import claim_id_for_field
from uwcopilot.studio.path_to_approval.models import PlanCase

SOURCE_STATUSES: frozenset[str] = frozenset({"VERIFIED", "MODEL_ESTIMATE", "CONFLICTING"})
SOURCE_KINDS: frozenset[str] = frozenset({"document", "public_data", "policy", "calculation", "model"})
EXTERNAL_STATUSES: frozenset[str] = frozenset({"VERIFIED", "CONFLICTING"})
EXTERNAL_KINDS: frozenset[str] = frozenset({"document", "public_data", "policy"})


@dataclass(frozen=True)
class CoverageCount:
    """How many printed figures a plan has, and how many carry a source under rule S and under rule E."""

    figures: int
    sourced: int
    external: int


@dataclass(frozen=True)
class FounderFigureCount:
    """M4: the founder's typed figures, how many a document backs in each plan, and Studio's conflict flags."""

    figures: int
    studio_backed: int
    baseline_backed: int
    studio_conflicting: int


def is_figure(claim: Claim) -> bool:
    """True for a claim the package prints as a number (or "not available"), not as text."""
    return claim.display in NUMERIC_DISPLAYS


def has_checkable_source(claim: Claim) -> bool:
    """Rule S (module docstring)."""
    if claim.status == "ASSUMPTION":
        return bool(claim.provenance.source) and claim.provenance.source != UNSOURCED
    return claim.status in SOURCE_STATUSES and claim.provenance.kind in SOURCE_KINDS


def has_external_evidence(claim: Claim) -> bool:
    """Rule E (module docstring)."""
    return claim.status in EXTERNAL_STATUSES and claim.provenance.kind in EXTERNAL_KINDS


def package_coverage(ledger: Ledger) -> CoverageCount:
    """Rule S and rule E over the printed figures of one package's ledger."""
    figures = [claim for claim in ledger.claims if is_figure(claim)]
    return CoverageCount(
        figures=len(figures),
        sourced=sum(has_checkable_source(claim) for claim in figures),
        external=sum(has_external_evidence(claim) for claim in figures),
    )


def baseline_coverage(figures: Sequence[BaselineFigure]) -> CoverageCount:
    """Rule S and rule E over B0's figures (both are the document rule for B0)."""
    cited = sum(figure.has_source for figure in figures)
    return CoverageCount(figures=len(figures), sourced=cited, external=cited)


def founder_figure_count(ledger: Ledger, case: PlanCase, baseline: Sequence[BaselineFigure]) -> FounderFigureCount:
    """M4 over the founder's typed figures: Studio's VERIFIED / CONFLICTING founder claims and B0's cited figures."""
    fields = [field for field, _ in typed_fields(case)]
    statuses = []
    for field in fields:
        claim = ledger.claim(claim_id_for_field(field))
        if claim is None:
            raise ValueError(f"the package ledger has no founder claim for {field}")
        statuses.append(claim.status)
    cited = {figure.name for figure in baseline if figure.group == "founder" and figure.has_source}
    return FounderFigureCount(
        figures=len(fields),
        studio_backed=sum(status == "VERIFIED" for status in statuses),
        baseline_backed=sum(field in cited for field in fields),
        studio_conflicting=sum(status == "CONFLICTING" for status in statuses),
    )


def figure_breakdown(ledger: Ledger) -> dict[str, int]:
    """{'STATUS/kind/sourced|not sourced': count} over the printed figures: what the rule-S share is made of."""
    counts: Counter[str] = Counter()
    for claim in ledger.claims:
        if not is_figure(claim):
            continue
        verdict = "sourced" if has_checkable_source(claim) else "not sourced"
        counts[f"{claim.status}/{claim.provenance.kind}/{verdict}"] += 1
    return dict(sorted(counts.items()))
