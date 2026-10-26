"""The S12 results blocks and headline from hand-made SYNTHETIC rows and cited blocks (no scenario is run)."""

from typing import Any

from uwcopilot.studio.business_metrics.report import (
    COMPUTE_COST,
    LLM_COST_NOT_REPORTED,
    USABILITY,
    cited_value,
    cost_per_package,
    metric_blocks,
)

RATE = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 2, "k": 2, "wilson_ci95": [0.34, 1.0]}


def row(metro_id: str, studio: tuple[int, int, int], baseline: tuple[int, int], *, handoff: bool) -> dict[str, Any]:
    """One hand-made per-scenario row (the shape scenario_run.run_scenario returns)."""
    figures, sourced, external = studio
    base_figures, base_cited = baseline
    return {
        "scenario_id": f"GOLDEN-{metro_id}-motel",
        "metro_id": metro_id,
        "metro_mode": "synthetic" if metro_id == "synthetic_metro" else "limited",
        "site_scored": metro_id == "synthetic_metro",
        "path_status": "no_path_within_bounds",
        "complete_package": True,
        "seconds": {
            "scenario": 1.0,
            "build_package": 1.0,
            "render_pdf": 2.0,
            "audit": 1.0,
            "package_total": 5.0,
            "handoff": 1.5 if handoff else None,
        },
        "studio": {"figures": figures, "sourced": sourced, "external": external},
        "baseline": {"figures": base_figures, "sourced": base_cited, "external": base_cited},
        "baseline_groups": {"founder": 5, "input": 20, "result": 12},
        "founder_figures": {
            "figures": 5,
            "studio_backed": base_cited,
            "baseline_backed": base_cited,
            "studio_conflicting": 0,
        },
        "studio_figure_breakdown": {"MODEL_ESTIMATE/model/sourced": sourced},
        "narrative": {"provider": "template", "no_llm_called": True},
        "handoff": (
            {
                "run": True,
                "reached_checkpoint": True,
                "decision_recorded_without_human": False,
                "memo_check_passed": True,
                "unsupported_numbers": 0,
            }
            if handoff
            else {"run": False}
        ),
    }


def cited() -> dict[str, Any]:
    """Hand-made cited blocks, as cited.py returns them."""
    header = {"source_file": "results/x.json", "source_section": "s", "source_provenance": {}}
    return {
        "handoff": {
            "traceability": {**header, "package_numbers_read_back": 10},
            "handoff": {
                **header,
                "golden_founder_scenarios": {"reached_human_checkpoint": RATE, "decisions_recorded_without_a_human": 0},
            },
        },
        "coverage": {**header, "metros": {"m1": {"min_coverage_share": 0.68, "mode": "limited"}}},
        "paths": {"status": "not measured here: results/studio_path_metrics.json is missing"},
    }


def test_the_headline_copies_the_blocks() -> None:
    rows = [
        row("synthetic_metro", (100, 90, 10), (30, 1), handoff=True),
        row("houston_tx", (100, 80, 5), (30, 0), handoff=True),
    ]
    blocks = metric_blocks(rows, cited())
    headline = blocks["headline"]
    rule_s = headline["evidence_coverage_rule_s"]
    assert rule_s["studio"]["value"] == 0.85 and rule_s["baseline_b0"]["value"] == round(1 / 60, 4)
    assert rule_s["difference"]["value"] == round(0.85 - 1 / 60, 4)
    assert headline["evidence_coverage_rule_e"]["studio"]["value"] == 0.075
    assert headline["time_to_complete_package_median_s"]["value"] == 5.0
    assert headline["data_coverage_min_share_by_metro_cited"] == {"m1": {"min_coverage_share": 0.68, "mode": "limited"}}
    assert headline["path_found_rate_cited"].startswith("not measured here")
    assert headline["agent_handoff_reached_checkpoint_cited"] == RATE
    assert headline["llm_cost_usd_per_package"] == 0.0 and headline["compute_cost"] == COMPUTE_COST
    assert headline["usability_test"] == USABILITY
    assert blocks["usability_test"]["n"] is None and blocks["usability_test"]["raw_times_s"] is None


def test_the_real_metro_subset_and_each_metro_are_reported() -> None:
    rows = [
        row("synthetic_metro", (100, 90, 10), (30, 1), handoff=False),
        row("houston_tx", (100, 80, 5), (30, 0), handoff=False),
    ]
    coverage = metric_blocks(rows, cited())["evidence_coverage"]["rule_s_checkable_source"]
    assert coverage["real_metro_scenarios"]["studio"]["value"] == 0.8
    assert set(coverage["by_metro"]) == {"synthetic_metro", "houston_tx"}
    only_synthetic = metric_blocks(rows[:1], cited())
    assert only_synthetic["evidence_coverage"]["rule_s_checkable_source"]["real_metro_scenarios"].startswith("no ")
    assert only_synthetic["time_to_complete_package"]["package_total_s_real_metro_scenarios"].startswith("no ")


def test_the_handoff_check_reports_this_run_or_says_it_was_skipped() -> None:
    ran = metric_blocks([row("houston_tx", (10, 5, 1), (5, 0), handoff=True)], cited())["agent_handoff"]
    assert ran["this_run_consistency_check"]["reached_human_checkpoint"]["k"] == 1
    assert ran["this_run_consistency_check"]["decisions_recorded_without_a_human"] == 0
    skipped = metric_blocks([row("houston_tx", (10, 5, 1), (5, 0), handoff=False)], cited())
    assert skipped["agent_handoff"]["this_run_consistency_check"].startswith("not run")
    assert skipped["time_to_complete_package"]["handoff_s"] == "not run in this execution"
    assert skipped["agent_handoff"]["baseline_b0"].startswith("not applicable")


def test_the_first_handoff_is_reported_apart_because_it_loads_the_lists() -> None:
    rows = [row("houston_tx", (10, 5, 1), (5, 0), handoff=True) for _ in range(3)]
    rows[0]["seconds"]["handoff"] = 90.0
    time = metric_blocks(rows, cited())["time_to_complete_package"]
    assert time["first_scenario_handoff_s"] == 90.0 and time["handoff_s"]["max"] == 90.0
    assert time["handoff_s_without_first_scenario"]["max"] == 1.5
    assert time["handoff_s_without_first_scenario"]["n"] == 2
    assert "screening lists" in time["handoff_note"]
    single = metric_blocks(rows[:1], cited())["time_to_complete_package"]
    assert single["handoff_s_without_first_scenario"] == "fewer than two handoffs run"


def test_the_founder_figures_and_conflicts_are_reported_for_both_plans() -> None:
    rows = [row("houston_tx", (10, 5, 1), (5, 1), handoff=False)]
    founder = metric_blocks(rows, cited())["evidence_coverage"]["founder_figures_backed_by_a_document"]
    assert founder["studio"]["value"] == founder["baseline_b0"]["value"] == 0.2
    assert founder["equal_in_every_scenario"] is True and founder["conflicts_surfaced"]["studio"] == 0
    assert founder["conflicts_surfaced"]["baseline_b0"].startswith("none by definition")


def test_the_llm_cost_is_not_reported_as_zero_when_a_narrative_was_not_the_template() -> None:
    rows = [row("houston_tx", (10, 5, 1), (5, 0), handoff=False)]
    rows[0]["narrative"] = {"provider": "ollama", "no_llm_called": False}
    cost = cost_per_package(rows)
    assert cost["llm_cost_usd_per_package"] is None and cost["llm_cost_basis"] == LLM_COST_NOT_REPORTED


def test_cited_value_reads_nested_keys_or_the_missing_status() -> None:
    assert cited_value({"source_file": "f", "a": {"b": 3}}, "a", "b") == 3
    assert cited_value({"status": "not measured here"}, "a") == "not measured here"
