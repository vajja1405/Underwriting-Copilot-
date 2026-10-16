"""The golden founder packages end to end (Studio S6): package -> PDF -> audits -> application -> handoff.

For every scenario: the PDF starts with %PDF and has pages, every page carries the product, the disclaimer, "Page x/y"
and the SYNTHETIC tag, the package text and the PDF text trace to the ledger (round-trip audit empty), the agent
reaches the human checkpoint with a passing template memo, zero unsupported numbers and zero decisions without a
human. Six scenarios (one per business type, every metro, every document variant) run in the default suite; the
full 36 are marked slow and run with UWC_RUN_SLOW_TESTS=1 (`make studio-package-golden`). Real metros run in this
machine's mode (limited mode without snapshots in CI), as the S4 golden tests do.
"""

import os
from pathlib import Path

import pytest
from package_helpers import log_dir, offline_handlers

from uwcopilot.studio.narrative.golden import build_golden_scenario, golden_specs
from uwcopilot.studio.package import (
    audit_pdf,
    build_package,
    handoff,
    pdf_page_count,
    project_from_golden,
    render_pdf,
    to_application,
)
from uwcopilot.studio.package.audit import pages_missing_labels

SUBSET_PATHS = 300
FULL_PATHS = 1000
SUBSET_INDEXES = (0, 7, 14, 21, 28, 35)
RUN_SLOW = os.environ.get("UWC_RUN_SLOW_TESTS") == "1"
MIN_PAGES = 20


def check_end_to_end(metro_id: str, profile_id: str, n_paths: int, tmp_path: Path) -> None:
    """Every end-to-end property of one golden scenario (module docstring)."""
    scenario = build_golden_scenario(metro_id, profile_id, n_paths=n_paths)
    package = build_package(project_from_golden(scenario))
    assert package.audit.untraced == []
    document = render_pdf(package)
    assert document.startswith(b"%PDF") and pdf_page_count(document) >= MIN_PAGES
    assert audit_pdf(document, package.ledger) == []
    assert not any(pages_missing_labels(document, package).values())
    application = to_application(package)
    assert application.applicant.metro_id == metro_id and application.applicant.industry == profile_id
    result = handoff(package, tool_handlers=offline_handlers(), log_dir=log_dir(tmp_path))
    assert result.reached_checkpoint and result.status == "awaiting_review"
    assert result.decision_recorded_without_human is False
    assert result.memo_check_passed is True and result.unsupported_numbers == 0
    assert len(result.review_request.founder_ledger) == len(package.ledger.claims)


def subset_specs() -> list[tuple[str, str]]:
    """One scenario per business type, across metros and the three document variants."""
    specs = golden_specs()
    return [specs[index] for index in SUBSET_INDEXES]


def test_the_subset_covers_every_business_type_and_document_variant() -> None:
    chosen = subset_specs()
    assert len({profile for _, profile in chosen}) == 6
    assert {index % 3 for index in SUBSET_INDEXES} == {0, 1, 2}


@pytest.mark.parametrize(("metro_id", "profile_id"), subset_specs(), ids=lambda value: value)
def test_golden_subset_end_to_end(metro_id: str, profile_id: str, tmp_path: Path) -> None:
    check_end_to_end(metro_id, profile_id, SUBSET_PATHS, tmp_path)


@pytest.mark.slow
@pytest.mark.skipif(not RUN_SLOW, reason="the full 36 golden packages take minutes; UWC_RUN_SLOW_TESTS=1 runs them")
@pytest.mark.parametrize(("metro_id", "profile_id"), golden_specs(), ids=lambda value: value)
def test_all_golden_scenarios_end_to_end(metro_id: str, profile_id: str, tmp_path: Path) -> None:
    check_end_to_end(metro_id, profile_id, FULL_PATHS, tmp_path)
