"""Founder Studio S12: business metrics of the golden SYNTHETIC founder scenarios, against a stated baseline.

docs/studio/s12_baseline.md defines the baseline B0 (a plan made without SiteIQ, the simulation and the ledger) and
every metric before any was measured; scripts/eval_studio.py runs this package and writes
results/studio_business_metrics.json. Modules:

- baseline: B0 for one plan (founder figures, untyped inputs, one-forecast results) and its document source rule;
- coverage: evidence coverage rules S and E over a package's ledger and over B0, and the founder-figure comparison;
- intervals: scenario-bootstrap intervals for pooled shares and paired differences, and time summaries;
- cited: reads the S1, S5 and S6 results files that already measured a metric, with their provenance;
- scenario_run: one golden scenario end to end, timed by stage, with its evidence counts;
- report: the per-metric blocks and the headline of the results file.

Only the baseline and the coverage rules are re-exported here; scenario_run pulls in the PDF renderer and the agent
graph, so the script imports it directly and importing this package stays light.

Everything is SYNTHETIC; nothing here measures real founders, lenders, outcomes or savings.
"""

from uwcopilot.studio.business_metrics.baseline import BaselineFigure, baseline_plan, baseline_setup
from uwcopilot.studio.business_metrics.coverage import (
    CoverageCount,
    FounderFigureCount,
    baseline_coverage,
    has_checkable_source,
    has_external_evidence,
    package_coverage,
)

__all__ = [
    "BaselineFigure",
    "CoverageCount",
    "FounderFigureCount",
    "baseline_coverage",
    "baseline_plan",
    "baseline_setup",
    "has_checkable_source",
    "has_external_evidence",
    "package_coverage",
]
