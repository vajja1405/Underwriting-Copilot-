"""One golden SYNTHETIC founder scenario end to end, timed by stage, with its evidence counts (S12, M1-M4, M9).

Stages (docs/studio/s12_baseline.md, M1), each timed with time.perf_counter on this machine:
  scenario      build_golden_scenario: SiteIQ when the metro has tracts here, the S3 simulation, the S5 quick search
  build_package project_from_golden + build_package (the founder ledger, the narrative, the sections, the audit)
  render_pdf    render_pdf
  audit         audit_pdf (the PDF's own text read back) + pages_missing_labels
  handoff       handoff: the existing agent up to the human checkpoint (timed apart; not part of the package time)
package_total = scenario + build_package + render_pdf + audit. The row also holds B0 for the same plan and documents
(baseline.py), the evidence counts (coverage.py) and the narrative provider check behind the LLM cost of 0 (M9).
"""

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uwcopilot.studio.business_metrics.baseline import baseline_plan, baseline_setup
from uwcopilot.studio.business_metrics.coverage import (
    CoverageCount,
    baseline_coverage,
    figure_breakdown,
    founder_figure_count,
    package_coverage,
)
from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.package.audit import audit_pdf, pages_missing_labels, pdf_page_count
from uwcopilot.studio.package.build import build_package
from uwcopilot.studio.package.golden import VARIANTS, project_from_golden, scenario_index
from uwcopilot.studio.package.handoff import handoff
from uwcopilot.studio.package.models import Package
from uwcopilot.studio.package.pdf import render_pdf
from uwcopilot.tools.registry import ToolHandler

TEMPLATE_PROVIDER = "template"
SECONDS_DECIMALS = 3
PACKAGE_STAGES: tuple[str, ...] = ("scenario", "build_package", "render_pdf", "audit")


def counts(count: CoverageCount) -> dict[str, int]:
    """A coverage count as a plain dict."""
    return {"figures": count.figures, "sourced": count.sourced, "external": count.external}


def narrative_check(package: Package) -> dict[str, Any]:
    """The facts behind "LLM cost 0 by construction": who wrote the narrative draft (M9)."""
    narrative = package.narrative
    return {
        "provider": narrative.provider,
        "model": narrative.model,
        "fallback_used": narrative.fallback_used,
        "llm_attempts": len(narrative.attempts),
        "no_llm_called": narrative.provider == TEMPLATE_PROVIDER
        and narrative.model is None
        and not narrative.fallback_used
        and not narrative.attempts,
    }


def path_status(scenario: GoldenScenario) -> str:
    """The S5 search status of the scenario, or 'not searched'."""
    return scenario.path_result.status if scenario.path_result is not None else "not searched"


def handoff_fields(
    package: Package, handlers: Mapping[str, ToolHandler] | None, log_dir: Path
) -> tuple[dict[str, Any], float | None]:
    """The handoff consistency check (M8) and its seconds; skipped (None) when no handlers are given."""
    if handlers is None:
        return {"run": False}, None
    started = time.perf_counter()
    result = handoff(package, tool_handlers=handlers, log_dir=log_dir)
    seconds = time.perf_counter() - started
    return {
        "run": True,
        "reached_checkpoint": result.reached_checkpoint,
        "decision_recorded_without_human": result.decision_recorded_without_human,
        "memo_check_passed": bool(result.memo_check_passed),
        "unsupported_numbers": result.unsupported_numbers,
        "reference_recommendation": result.reference_recommendation,
    }, seconds


def run_scenario(
    metro_id: str,
    profile_id: str,
    *,
    n_paths: int,
    handlers: Mapping[str, ToolHandler] | None,
    log_dir: Path,
) -> dict[str, Any]:
    """One scenario end to end (module docstring): timings, PDF checks, Studio and B0 evidence counts, handoff."""
    seconds: dict[str, float | None] = {}
    started = time.perf_counter()
    scenario = build_golden_scenario(metro_id, profile_id, n_paths=n_paths)
    seconds["scenario"] = time.perf_counter() - started
    started = time.perf_counter()
    project = project_from_golden(scenario)
    package = build_package(project)
    seconds["build_package"] = time.perf_counter() - started
    started = time.perf_counter()
    pdf = render_pdf(package)
    seconds["render_pdf"] = time.perf_counter() - started
    started = time.perf_counter()
    pdf_untraced = audit_pdf(pdf, package.ledger)
    missing_labels = {key: pages for key, pages in pages_missing_labels(pdf, package).items() if pages}
    seconds["audit"] = time.perf_counter() - started
    seconds["package_total"] = sum(seconds[stage] or 0.0 for stage in PACKAGE_STAGES)
    handoff_row, seconds["handoff"] = handoff_fields(package, handlers, log_dir)
    baseline = baseline_plan(
        scenario.case, project.documents, baseline_setup(scenario.case, scenario.feasibility.months)
    )
    founder = founder_figure_count(package.ledger, scenario.case, baseline)
    return {
        "scenario_id": scenario.scenario_id,
        "metro_id": metro_id,
        "profile_id": profile_id,
        "metro_mode": scenario.metro_mode,
        "site_scored": scenario.site_score is not None,
        "path_status": path_status(scenario),
        "document_variant": scenario_index(scenario) % VARIANTS,
        "data_label": package.data_label,
        "package_id": package.package_id,
        "pdf_pages": pdf_page_count(pdf),
        "pdf_untraced_numbers": len(pdf_untraced),
        "package_untraced_numbers": len(package.audit.untraced),
        "pages_missing_labels": missing_labels,
        "complete_package": not pdf_untraced and not package.audit.untraced and not missing_labels,
        "seconds": {key: None if value is None else round(value, SECONDS_DECIMALS) for key, value in seconds.items()},
        "studio": counts(package_coverage(package.ledger)),
        "baseline": counts(baseline_coverage(baseline)),
        "baseline_groups": {
            group: sum(figure.group == group for figure in baseline) for group in ("founder", "input", "result")
        },
        "founder_figures": {
            "figures": founder.figures,
            "studio_backed": founder.studio_backed,
            "baseline_backed": founder.baseline_backed,
            "studio_conflicting": founder.studio_conflicting,
        },
        "studio_figure_breakdown": figure_breakdown(package.ledger),
        "narrative": narrative_check(package),
        "handoff": handoff_row,
    }
