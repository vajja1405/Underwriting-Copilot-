"""Turn the per-scenario rows and the cited results into the blocks of results/studio_business_metrics.json.

One function per metric of docs/studio/s12_baseline.md: time_block (M1), evidence_coverage (M2-M5), data_coverage
(M6), path_found (M7), agent_handoff (M8), cost_per_package (M9), usability_test (M10), and headline() which copies
the headline values out of those blocks (it computes nothing new). Rates measured here carry scenario-bootstrap
95% intervals (intervals.py); cited rates carry the intervals of the file they come from.
"""

from collections import Counter
from collections.abc import Callable
from typing import Any

from uwcopilot.studio.business_metrics.intervals import paired_difference, pooled_share, seconds_summary, spread
from uwcopilot.studio.narrative.golden import SYNTHETIC_METRO
from uwcopilot.studio.package.evaluation import proportion

Row = dict[str, Any]
STAGES: tuple[str, ...] = ("scenario", "build_package", "render_pdf", "audit", "package_total")
MACHINE_LABEL = (
    "machine-dependent wall-clock measurement on the machine that ran the script (shared; CPU count and load "
    "average in the provenance block); not a user time: it excludes the founder's time to gather inputs and "
    "documents, and nobody's time was observed"
)
TIME_DEFINITION = (
    "seconds from a scenario's inputs to an audited PDF: golden scenario (SiteIQ when tracts exist, the simulation, "
    "the quick path search) + build_package + render_pdf + PDF round-trip audit and page-label check; the handoff "
    "is timed apart and is not part of it"
)
BASELINE_TIME = "not measured: no one was timed writing a plan"
FIRST_SCENARIO_NOTE = "the first scenario of a process also loads configs and fills caches once"
HANDOFF_TIME_NOTE = (
    "the first handoff of a process also loads the screening lists (agent.tools.screening_handler loads them on "
    "first call), so its time includes that one-time load; handoff_s_without_first_scenario leaves it out"
)
RULE_S = (
    "share of printed figures with a checkable source: VERIFIED, MODEL_ESTIMATE or CONFLICTING claims of kind "
    "document, public_data, policy, calculation or model, and ASSUMPTION claims whose source is not 'unsourced'"
)
RULE_E = "share of printed figures backed by a document page, a public-data row or a program or target section"
B0_RULE = (
    "a B0 figure carries a source only when it is a founder figure and every document about that field prints the "
    "same value (ledger tolerance); B0 has no ledger to record any other source (definition, not observation)"
)
COVERAGE_READING = (
    "Most of the Studio - B0 gap follows from the definitions: model estimates and calculations count for Studio "
    "because the ledger records their run or formula and inputs, and B0 prints no such record. Rule E removes that "
    "credit; the founder-figure comparison uses one population for both and is equal by construction. B0 is a "
    "construction from the same SYNTHETIC inputs, not a sample of real founders' plans."
)
EQUAL_BY_CONSTRUCTION = "expected equal by construction: the same documents and the same tolerance decide both plans"
B0_CONFLICTS = "none by definition: B0 has no step that compares a typed figure with a document (not a measurement)"
NOT_APPLICABLE_COVERAGE = "not applicable: B0 uses no location data, so it has no data-coverage score"
NOT_APPLICABLE_PATHS = "not applicable: B0 has no path search"
NOT_APPLICABLE_HANDOFF = "not applicable: to_application needs the package's founder ledger, which B0 does not have"
NOT_APPLICABLE_TRACE = "not applicable: B0 has no ledger to trace a number to"
LLM_COST_BASIS = (
    "0 by construction: the narrative is the template provider and the lender memo is the deterministic template "
    "(FAKE LLM - test mode); no language model is called. Checked per package (narrative provider 'template', no "
    "model, no fallback, no LLM attempt)."
)
LLM_COST_NOT_REPORTED = (
    "not reported as 0: at least one package's narrative was not the template (see narrative_checks)"
)
COMPUTE_COST = "not measured: no cloud resource was used and the machine's cost is not metered"
USABILITY = "not measured: no usability test with real people was done, so there is no n, protocol or raw time"
PATH_NOTE = (
    "path-search status of the golden scenarios in this run: a description of these seeded plans, not an estimate "
    "of the path-found rate (the cited S5 rate is the estimate)"
)


def is_real_metro(row: Row) -> bool:
    """True for a scenario in one of the five real metros (still a SYNTHETIC plan)."""
    return row["metro_id"] != SYNTHETIC_METRO


def column(rows: list[Row], plan: str, key: str) -> list[int]:
    """One count per scenario, for example column(rows, "studio", "sourced")."""
    return [int(row[plan][key]) for row in rows]


def share_per_scenario(rows: list[Row], plan: str, key: str) -> list[float]:
    """Each scenario's own share (for min / median / max)."""
    return [row[plan][key] / row[plan]["figures"] for row in rows if row[plan]["figures"]]


def compare(rows: list[Row], key: str) -> dict[str, Any]:
    """Studio and B0 pooled shares for one rule, and their paired difference."""
    studio_hits, studio_figures = column(rows, "studio", key), column(rows, "studio", "figures")
    base_hits, base_figures = column(rows, "baseline", key), column(rows, "baseline", "figures")
    return {
        "studio": pooled_share(studio_hits, studio_figures),
        "baseline_b0": pooled_share(base_hits, base_figures),
        "difference_studio_minus_b0": paired_difference(studio_hits, studio_figures, base_hits, base_figures),
        "per_scenario_share": {
            "studio": spread(share_per_scenario(rows, "studio", key)),
            "baseline_b0": spread(share_per_scenario(rows, "baseline", key)),
        },
    }


def by_metro(rows: list[Row], summarize: Callable[[list[Row]], Any]) -> dict[str, Any]:
    """summarize() applied to each metro's rows, metros in first-seen order."""
    metros = list(dict.fromkeys(row["metro_id"] for row in rows))
    return {metro: summarize([row for row in rows if row["metro_id"] == metro]) for metro in metros}


def rule_block(rows: list[Row], key: str, definition: str) -> dict[str, Any]:
    """One coverage rule: all scenarios, the real-metro subset and each metro."""
    real = [row for row in rows if is_real_metro(row)]
    return {
        "definition": definition,
        "baseline_rule": B0_RULE,
        "all_scenarios": compare(rows, key),
        "real_metro_scenarios": compare(real, key) if real else "no real-metro scenario in this run",
        "by_metro": by_metro(rows, lambda group: compare(group, key)),
    }


def founder_block(rows: list[Row]) -> dict[str, Any]:
    """M4: the founder's typed figures, one population for both plans."""
    figures = [row["founder_figures"]["figures"] for row in rows]
    studio = [row["founder_figures"]["studio_backed"] for row in rows]
    baseline = [row["founder_figures"]["baseline_backed"] for row in rows]
    return {
        "population": "the founder's typed plan and loan figures (founder_claims.FIELD_SPECS fields with a value)",
        "studio": pooled_share(studio, figures),
        "baseline_b0": pooled_share(baseline, figures),
        "equal_in_every_scenario": studio == baseline,
        "expected": EQUAL_BY_CONSTRUCTION,
        "conflicts_surfaced": {
            "studio": sum(row["founder_figures"]["studio_conflicting"] for row in rows),
            "baseline_b0": B0_CONFLICTS,
        },
    }


def breakdown(rows: list[Row]) -> dict[str, int]:
    """The Studio figures of every package by status, provenance kind and rule-S verdict, summed."""
    total: Counter[str] = Counter()
    for row in rows:
        total.update(row["studio_figure_breakdown"])
    return dict(sorted(total.items()))


def evidence_coverage(rows: list[Row], traceability: dict[str, Any]) -> dict[str, Any]:
    """M2-M5: rule S, rule E, the founder figures, the figure counts and the cited traceability."""
    groups: Counter[str] = Counter()
    for row in rows:
        groups.update(row["baseline_groups"])
    return {
        "rule_s_checkable_source": rule_block(rows, "sourced", RULE_S),
        "rule_e_external_evidence": rule_block(rows, "external", RULE_E),
        "founder_figures_backed_by_a_document": founder_block(rows),
        "figures_per_plan": {
            "studio": spread([row["studio"]["figures"] for row in rows]),
            "baseline_b0": spread([row["baseline"]["figures"] for row in rows]),
            "baseline_b0_figures_by_group": dict(groups),
        },
        "studio_figures_by_status_kind_and_verdict": breakdown(rows),
        "reading": COVERAGE_READING,
        "printed_numbers_trace_to_a_ledger_claim": {"cited": traceability, "baseline_b0": NOT_APPLICABLE_TRACE},
    }


def time_block(rows: list[Row]) -> dict[str, Any]:
    """M1: per-scenario seconds by stage, the package total and the handoff (timed apart)."""
    handoff_seconds = [row["seconds"]["handoff"] for row in rows if row["seconds"]["handoff"] is not None]
    real = [row["seconds"]["package_total"] for row in rows if is_real_metro(row)]
    return {
        "label": MACHINE_LABEL,
        "definition": TIME_DEFINITION,
        "package_total_s": seconds_summary([row["seconds"]["package_total"] for row in rows]),
        "package_total_s_real_metro_scenarios": seconds_summary(real) if real else "no real-metro scenario in this run",
        "first_scenario_package_total_s": rows[0]["seconds"]["package_total"] if rows else None,
        "first_scenario_note": FIRST_SCENARIO_NOTE,
        "stage_s": {stage: spread([row["seconds"][stage] for row in rows]) for stage in STAGES},
        "handoff_s": seconds_summary(handoff_seconds) if handoff_seconds else "not run in this execution",
        "handoff_s_without_first_scenario": (
            seconds_summary(handoff_seconds[1:]) if len(handoff_seconds) > 1 else "fewer than two handoffs run"
        ),
        "first_scenario_handoff_s": handoff_seconds[0] if handoff_seconds else None,
        "handoff_note": HANDOFF_TIME_NOTE,
        "complete_packages": proportion([row["complete_package"] for row in rows]),
        "baseline_b0": BASELINE_TIME,
    }


def observed_modes(rows: list[Row]) -> dict[str, Any]:
    """Per metro in this run: scenarios, the metro modes used and how many had a SiteIQ result."""
    return {
        "scenarios": len(rows),
        "metro_modes": dict(Counter(row["metro_mode"] for row in rows)),
        "site_scored": sum(row["site_scored"] for row in rows),
    }


def data_coverage(rows: list[Row], cited: dict[str, Any]) -> dict[str, Any]:
    """M6: the cited S1 coverage scores and what this run observed per metro."""
    return {
        "cited": cited,
        "this_run": by_metro(rows, observed_modes),
        "this_run_note": "depends on which metro snapshots exist on the machine that ran the script",
        "baseline_b0": NOT_APPLICABLE_COVERAGE,
    }


def path_found(rows: list[Row], cited: dict[str, Any]) -> dict[str, Any]:
    """M7: the cited S5 path-found rate and the golden scenarios' search statuses in this run."""
    return {
        "cited": cited,
        "this_run_golden_path_status": dict(Counter(row["path_status"] for row in rows)),
        "this_run_note": PATH_NOTE,
        "baseline_b0": NOT_APPLICABLE_PATHS,
    }


def consistency_check(rows: list[Row]) -> dict[str, Any] | str:
    """M8 re-run on this execution's packages (k/n next to the cited headline), or why it was not run."""
    ran = [row["handoff"] for row in rows if row["handoff"]["run"]]
    if not ran:
        return "not run in this execution (--skip-handoff)"
    return {
        "cases": len(ran),
        "reached_human_checkpoint": proportion([item["reached_checkpoint"] for item in ran]),
        "decisions_recorded_without_a_human": sum(item["decision_recorded_without_human"] for item in ran),
        "memo_check_passed": proportion([item["memo_check_passed"] for item in ran]),
        "unsupported_numbers_in_memos": sum(item["unsupported_numbers"] for item in ran),
    }


def agent_handoff(rows: list[Row], cited: dict[str, Any]) -> dict[str, Any]:
    """M8: the cited S6 handoff results (the headline) and this run's consistency check."""
    return {
        "cited": cited,
        "this_run_consistency_check": consistency_check(rows),
        "baseline_b0": NOT_APPLICABLE_HANDOFF,
    }


def cost_per_package(rows: list[Row]) -> dict[str, Any]:
    """M9: LLM cost 0 by construction when every package's narrative check holds; compute cost not measured."""
    checks = [row["narrative"]["no_llm_called"] for row in rows]
    every = bool(checks) and all(checks)
    return {
        "llm_cost_usd_per_package": 0.0 if every else None,
        "llm_cost_basis": LLM_COST_BASIS if every else LLM_COST_NOT_REPORTED,
        "narrative_checks": {
            "packages": len(checks),
            "no_llm_called": sum(checks),
            "providers": dict(Counter(row["narrative"]["provider"] for row in rows)),
        },
        "compute_cost": COMPUTE_COST,
        "baseline_b0": "not measured",
    }


def usability_test() -> dict[str, Any]:
    """M10: not measured (no test with real people was done)."""
    return {"status": USABILITY, "n": None, "protocol": None, "raw_times_s": None}


def metro_minimums(coverage: dict[str, Any]) -> dict[str, Any] | str:
    """{metro: {min_coverage_share, mode}} from the cited coverage block (or its 'not measured' status)."""
    if "metros" not in coverage:
        return coverage.get("status", "not measured")
    return {
        metro: {"min_coverage_share": entry["min_coverage_share"], "mode": entry["mode"]}
        for metro, entry in coverage["metros"].items()
    }


def cited_value(block: dict[str, Any], *keys: str) -> Any:
    """A nested value of a cited block, or the block's 'not measured' status when the file was missing."""
    if "status" in block and "source_file" not in block:
        return block["status"]
    node: Any = block
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def headline(blocks: dict[str, Any]) -> dict[str, Any]:
    """The headline values, copied from the metric blocks (nothing is computed here)."""
    time = blocks["time_to_complete_package"]["package_total_s"]
    rule_s = blocks["evidence_coverage"]["rule_s_checkable_source"]["all_scenarios"]
    rule_e = blocks["evidence_coverage"]["rule_e_external_evidence"]["all_scenarios"]
    founder = blocks["evidence_coverage"]["founder_figures_backed_by_a_document"]
    handoff_cited = blocks["agent_handoff"]["cited"]
    return {
        "time_to_complete_package_median_s": {"value": time["median"], "ci95": time["median_ci95"], "n": time["n"]},
        "time_to_complete_package_p95_s": {"value": time["p95"], "ci95": time["p95_ci95"], "n": time["n"]},
        "time_label": MACHINE_LABEL,
        "evidence_coverage_rule_s": {
            "studio": rule_s["studio"],
            "baseline_b0": rule_s["baseline_b0"],
            "difference": rule_s["difference_studio_minus_b0"],
        },
        "evidence_coverage_rule_e": {
            "studio": rule_e["studio"],
            "baseline_b0": rule_e["baseline_b0"],
            "difference": rule_e["difference_studio_minus_b0"],
        },
        "founder_figures_backed_by_a_document": {
            "studio": founder["studio"],
            "baseline_b0": founder["baseline_b0"],
            "expected": EQUAL_BY_CONSTRUCTION,
        },
        "data_coverage_min_share_by_metro_cited": metro_minimums(blocks["data_coverage_by_metro"]["cited"]),
        "path_found_rate_cited": cited_value(blocks["path_found_rate"]["cited"], "path_found_rate_full"),
        "agent_handoff_reached_checkpoint_cited": cited_value(
            handoff_cited, "golden_founder_scenarios", "reached_human_checkpoint"
        ),
        "agent_handoff_decisions_without_a_human_cited": cited_value(
            handoff_cited, "golden_founder_scenarios", "decisions_recorded_without_a_human"
        ),
        "llm_cost_usd_per_package": blocks["cost_per_package"]["llm_cost_usd_per_package"],
        "compute_cost": COMPUTE_COST,
        "usability_test": USABILITY,
    }


def metric_blocks(rows: list[Row], cited: dict[str, Any]) -> dict[str, Any]:
    """Every metric block plus the headline. cited: {"handoff": cite_handoff(), "coverage": ..., "paths": ...}."""
    blocks: dict[str, Any] = {
        "time_to_complete_package": time_block(rows),
        "evidence_coverage": evidence_coverage(rows, cited["handoff"]["traceability"]),
        "data_coverage_by_metro": data_coverage(rows, cited["coverage"]),
        "path_found_rate": path_found(rows, cited["paths"]),
        "agent_handoff": agent_handoff(rows, cited["handoff"]["handoff"]),
        "cost_per_package": cost_per_package(rows),
        "usability_test": usability_test(),
    }
    return {"headline": headline(blocks), **blocks}
