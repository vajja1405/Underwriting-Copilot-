"""Write results/studio_path_metrics.json: the path-to-approval search (Studio S5) on seeded SYNTHETIC plans.

    uv run python scripts/eval_studio_paths.py          # or: make studio-paths

For every metro (the SYNTHETIC metro and each real metro in its current mode, usually "limited") and every business
type, PLANS_PER_CELL SYNTHETIC plans are generated (path_to_approval/synthetic_plans.py; seeded). Each plan is searched:
1. full mode (1,000 paths), the three default targets: the headline (path-found rate, levers changed, runtime), with
   the share of paths whose changed plan also meets every startup-program rule (NSP-3.1, NSP-8.1);
2. quick mode (the same 1,000 paths and seed, 4 grid values per side instead of 8, at most 250 evaluated variants):
   what the website calls, through the same call (the metro's data fabric passed in), on a cold outcome cache, plus a
   repeat of the same request on the warm cache (a founder reloading the page);
3. full mode with the stress target switched off (only the two targets of the original Prompt S5): how often a path
   found without the simulated stress target misses it, and how often it also fails the lender's single base-case
   NSP-4.2 check;
4. checks: every returned change-set is re-simulated with S3's own simulate() (same seed, 1,000 paths) and must give
   the same three probabilities; its applied plan is compared field by field with the founder's plan, with this
   script's own table of what each lever may write (not guardrails.py), to count any other field that moved;
5. the guardrail check with facts: the same plan with SYNTHETIC founder facts (credit score, experience, sometimes a
   seller note, some figures marked verified; synthetic_plans.with_synthetic_facts) is searched in quick mode, and
   every returned change-set must leave the facts, the verified figures and every field outside its levers alone.
On a subsample (every business type on the SYNTHETIC metro; car wash and laundromat, where paths occur, on every
metro) the heuristic search (quick mode, as the website runs it) is compared with a brute-force reference that tries
every single value and every pair of values on the same grid with the same draws (exhaustive.py), to count paths the
heuristic missed or found larger than needed.

Rates and means come with bootstrap 95% CIs (1,000 resamples over plans, seed 42). Runtimes are wall time in a warm
process (fabric, profile and mode loaded once, as in the web app) on a shared machine; the load average is in the
provenance block. Every probability is "simulated, based on stated assumptions"; no plan is real and no number here is
a claim about real-world approval (rule E, rule 9). Every number in the file is computed here.
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from uwcopilot.agent.policy_credit import REVENUE_STRESS_MIN_DSCR, REVENUE_STRESS_SHARE
from uwcopilot.common.paths import CONFIG_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance
from uwcopilot.risk.bootstrap import bootstrap_ci
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.feasibility.assumptions import ASSUMPTIONS_FILE
from uwcopilot.studio.feasibility.result import SIMULATOR_VERSION
from uwcopilot.studio.feasibility.simulate import simulate
from uwcopilot.studio.metros import list_metros
from uwcopilot.studio.path_to_approval import synthetic_plans
from uwcopilot.studio.path_to_approval.evaluate import EvaluationCache
from uwcopilot.studio.path_to_approval.exhaustive import exhaustive_search
from uwcopilot.studio.path_to_approval.levers import LEVERS_FILE
from uwcopilot.studio.path_to_approval.models import PATH_ENGINE_VERSION, ChangeSet, PathResult, PlanCase
from uwcopilot.studio.path_to_approval.program import failed_checks
from uwcopilot.studio.path_to_approval.search import (
    FULL_SETTINGS,
    QUICK_SETTINGS,
    default_assumptions,
    fabric_for,
    find_paths,
    mode_for,
    prepare_search,
    profile_for,
)
from uwcopilot.studio.path_to_approval.targets import custom_target, default_target, describe
from uwcopilot.studio.profiles.loader import PROFILES_DIR, load_all_profiles

OUTPUT: Path = RESULTS_DIR / "studio_path_metrics.json"
GENERATED_BY = "scripts/eval_studio_paths.py"
SYNTHETIC_METRO = "synthetic_metro"
DEFAULT_PLANS_PER_CELL = 15
DEFAULT_EXHAUSTIVE_PER_TYPE = 15
# Business types also compared with the brute force on the real metros (the ones where paths occur).
EXHAUSTIVE_TYPES_ON_REAL_METROS: tuple[str, ...] = ("car_wash", "laundromat")
# What each lever may write, written HERE from the lever definitions (config/studio/levers.yaml notes and Prompt S5),
# so the field check does not trust guardrails.py or space.LEVER_WRITES.
EXPECTED_WRITES: dict[str, set[str]] = {
    "owner_equity": {"plan.owner_equity", "loan.amount"},
    "loan_amount": {"loan.amount"},
    "build_out_cost": {"plan.capex", "loan.amount"},
    "working_capital_reserve": {"plan.owner_equity"},
    "price": {"plan.price"},
    "days_open_per_week": {"plan.days_open_per_week", "plan.paid_hours_per_month"},
    "loan_term_months": {"loan.term_months"},
    "site": {"site", "site_label", "plan.monthly_rent"},
}
SEED = 42
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
PROBABILITY_FIELDS: tuple[str, ...] = (
    "p_dscr_at_least_target_year2",
    "p_stressed_dscr_at_least_min_year2",
    "p_cash_negative_within_months",
)
PROGRAM_FILE: Path = REPO_ROOT / "corpus" / "programs" / "northwind_startup_program.md"
SCOPE_NOTE = (
    "Seeded SYNTHETIC plans (src/uwcopilot/studio/path_to_approval/synthetic_plans.py) searched on the SYNTHETIC metro "
    "and on each real metro in its current mode. The path-found rate depends on how the plans are generated and on "
    "the editable targets and lever bounds; it describes the search on these plans, not any real founder's chances."
)


def metro_ids(requested: Sequence[str] | None) -> list[str]:
    """The SYNTHETIC metro first, then the real metros in config order (or the requested ones)."""
    known = list_metros()
    if requested:
        unknown = sorted(set(requested) - set(known))
        if unknown:
            raise SystemExit(f"unknown metro(s) {unknown}; known: {known}")
        return list(requested)
    return [SYNTHETIC_METRO] + sorted(item for item in known if item != SYNTHETIC_METRO)


def top_levers(result: PathResult) -> list[str]:
    """The levers of the rank-1 change-set (empty when there is none)."""
    return [change.lever for change in result.change_sets[0].changes] if result.change_sets else []


def top_size(result: PathResult) -> list[float] | None:
    """[levers changed, effort] of the rank-1 change-set (None when there is none)."""
    if not result.change_sets:
        return None
    top = result.change_sets[0]
    return [top.n_levers, top.effort]


def change_signature(change_set: ChangeSet) -> list[list[Any]]:
    """[[lever, value], ...] of a change-set, for comparing two results."""
    return [[change.lever, change.to_value] for change in change_set.changes]


def flat_fields(case: PlanCase) -> dict[str, Any]:
    """Every field of a plan as {path: value}: ids, the site's demand, the facts, and each Plan and Loan field."""
    fields: dict[str, Any] = {
        "profile_id": case.profile_id,
        "metro_id": case.metro_id,
        "site_label": case.site_label,
        "site": case.site.model_dump(),
        "facts": case.facts.model_dump(),
    }
    fields.update({f"plan.{name}": value for name, value in case.plan.model_dump().items()})
    fields.update({f"loan.{name}": value for name, value in case.loan.model_dump().items()})
    return fields


def facts_moved(before: PlanCase, change_set: ChangeSet) -> list[str]:
    """Fields that moved but may not: every field outside EXPECTED_WRITES of the change-set's levers (the facts, the
    site's demand unless the site lever moved, every assumption, rate and date field ...), plus any verified figure."""
    old, new = flat_fields(before), flat_fields(change_set.applied_plan)
    allowed = set().union(*(EXPECTED_WRITES[change.lever] for change in change_set.changes))
    changed = [path for path in old if old[path] != new[path]]
    moved = [path for path in changed if path not in allowed]
    moved += [f"verified {path}" for path in changed if path in before.facts.verified_fields]
    return moved


def s3_gaps(change_set: ChangeSet) -> list[float]:
    """|reported - S3 simulate()| for the three probabilities of a change-set's applied plan (same seed and paths)."""
    plan = change_set.applied_plan
    result = simulate(
        plan.profile_id,
        fabric_for(plan.metro_id),
        plan.site,
        plan.plan,
        plan.loan,
        n=change_set.after.n_paths,
        seed=change_set.after.seed,
        with_tornado=False,
    )
    revenue_stress = next(item for item in result.stress if item.name == "revenue_stress")
    simulated = (
        result.p_dscr_at_least_target["year_2"],
        revenue_stress.p_dscr_year2_at_least_min,
        result.p_cash_negative_within_24_months,
    )
    reported = [getattr(change_set.after, name) for name in PROBABILITY_FIELDS]
    return [abs(a - b) for a, b in zip(reported, simulated, strict=True)]


def facts_row(plan: synthetic_plans.SyntheticPlan, seed: int) -> dict[str, Any]:
    """The guardrail check with SYNTHETIC founder facts: quick mode (as the website runs it) on the plan with facts."""
    case = synthetic_plans.with_synthetic_facts(plan.case, seed)
    fabric = fabric_for(case.metro_id)
    result = find_paths(case, list(plan.candidates), mode="quick", cache=EvaluationCache(), fabric=fabric)
    returned = list(result.change_sets) + ([result.closest] if result.closest is not None else [])
    return {
        "facts_variant_status": result.status,
        "facts_variant_other_debt": bool(case.facts.other_debt),
        "facts_variant_verified_fields": list(case.facts.verified_fields),
        "facts_variant_change_sets": len(returned),
        "facts_variant_moved": [field for item in returned for field in facts_moved(case, item)],
    }


def run_plan(plan: synthetic_plans.SyntheticPlan, metro_id: str, plan_index: int, seed: int) -> dict[str, Any]:
    """Search one plan in every configuration and return one row of facts about it.

    Every call passes the metro's data fabric, as the website does (web/services/studio_runs.py).
    """
    case, candidates = plan.case, list(plan.candidates)
    fabric = fabric_for(metro_id)
    full = find_paths(case, candidates, mode="full", cache=EvaluationCache(), fabric=fabric)
    quick_cache = EvaluationCache()
    quick = find_paths(case, candidates, mode="quick", cache=quick_cache, fabric=fabric)
    repeat = find_paths(case, candidates, mode="quick", cache=quick_cache, fabric=fabric)
    two_target = custom_target(p_stressed_min=0.0)
    two_targets = find_paths(case, candidates, two_target, mode="full", cache=EvaluationCache(), fabric=fabric)
    returned = list(full.change_sets) + ([full.closest] if full.closest is not None else [])
    gaps = [s3_gaps(item) for item in returned]
    moved = [field for item in returned for field in facts_moved(case, item)]
    two_top = two_targets.change_sets[0] if two_targets.change_sets else None
    stress_min = full.target.p_stressed_min
    compliant = [item for item in full.change_sets if not item.program_rules_not_met]
    return {
        "metro_id": metro_id,
        "profile_id": case.profile_id,
        "plan_index": plan_index,
        "band": plan.band,
        "target_stressed_dscr_one_forecast": round(plan.target_stressed_dscr, 4),
        "anchored_by": plan.anchored_by,
        "staffing_plan": plan.staffing_plan,
        "before": {name: getattr(full.before, name) for name in PROBABILITY_FIELDS},
        "before_targets_missed": full.before.targets_missed,
        "status_full": full.status,
        "status_quick": quick.status,
        "status_two_targets": two_targets.status,
        "n_change_sets": len(full.change_sets),
        "levers_per_change_set": [item.n_levers for item in full.change_sets],
        "top_levers": top_levers(full),
        "all_levers": [[change.lever for change in item.changes] for item in full.change_sets],
        "top_signature_full": change_signature(full.change_sets[0]) if full.change_sets else None,
        "top_signature_quick": change_signature(quick.change_sets[0]) if quick.change_sets else None,
        "top_size_full": top_size(full),
        "top_size_quick": top_size(quick),
        "runtime_ms_full": full.search.runtime_ms,
        "runtime_ms_quick": quick.search.runtime_ms,
        "runtime_ms_quick_repeat": repeat.search.runtime_ms,
        "evaluations_full": full.search.evaluations,
        "evaluations_quick": quick.search.evaluations,
        "budget_exhausted_full": full.search.budget_exhausted,
        "budget_exhausted_quick": quick.search.budget_exhausted,
        "blocked_by_program_full": full.search.blocked_by_program,
        "two_targets_top_meets_stress": None
        if two_top is None
        else two_top.after.p_stressed_dscr_at_least_min_year2 >= stress_min,
        "two_targets_top_stressed_dscr_base_case": None if two_top is None else two_top.after.stressed_dscr_base_case,
        "s3_check_change_sets": len(gaps),
        "s3_check_max_gap": max((max(item) for item in gaps), default=None),
        "facts_moved": moved,
        "closest_shortfall": full.closest.after.shortfall if full.closest is not None else None,
        "program_rules_failed_before": failed_checks(full.plan_before.checks),
        "top_program_rules_not_met": full.change_sets[0].program_rules_not_met if full.change_sets else None,
        "program_compliant_change_sets": len(compliant),
    } | facts_row(plan, seed)


def exhaustive_row(plan: synthetic_plans.SyntheticPlan, plan_index: int) -> dict[str, Any]:
    """Heuristic search vs the brute-force reference, both with quick mode's settings (grid, paths, seed)."""
    settings = QUICK_SETTINGS
    case, candidates = plan.case, list(plan.candidates)
    cache = EvaluationCache()
    heuristic = find_paths(case, candidates, settings=settings, cache=cache)
    started = time.perf_counter()
    prepared = prepare_search(case, candidates, settings=settings, cache=cache)
    reference = exhaustive_search(prepared)
    best = reference.best
    top = heuristic.change_sets[0] if heuristic.change_sets else None
    larger = (
        top is not None and best is not None and (top.n_levers, top.effort) > (best.n_levers, round(best.effort, 6))
    )
    return {
        "metro_id": case.metro_id,
        "plan_index": plan_index,
        "profile_id": case.profile_id,
        "band": plan.band,
        "status": heuristic.status,
        "heuristic_found": top is not None,
        "reference_found": best is not None,
        "already_meets_target": heuristic.status == "already_meets_target",
        "heuristic_levers": None if top is None else top.n_levers,
        "reference_levers": None if best is None else best.n_levers,
        "heuristic_effort": None if top is None else top.effort,
        "reference_effort": None if best is None else round(best.effort, 6),
        "heuristic_top_larger_than_reference": larger,
        "reference_levers_named": None if best is None else [change.lever for change in best.changes],
        "reference_feasible_change_sets": len(reference.feasible),
        "reference_tried": reference.tried,
        "heuristic_evaluations": heuristic.search.evaluations,
        "heuristic_budget_exhausted": heuristic.search.budget_exhausted,
        "reference_seconds": round(time.perf_counter() - started, 2),
    }


def ci(metric: Callable[[np.ndarray], float], values: Sequence[float]) -> dict[str, Any] | str:
    """A bootstrap CI of metric over the values (rounded), or "not measured" when there are none."""
    data = np.asarray(list(values), dtype=float)
    if data.size == 0:
        return "not measured (no plans in this group)"
    result = bootstrap_ci(metric, data, n_resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED)
    return {key: round(value, 4) if isinstance(value, float) else value for key, value in result.items()} | {
        "n": int(data.size)
    }


def p50(values: np.ndarray) -> float:
    """Median."""
    return float(np.percentile(values, 50))


def p95(values: np.ndarray) -> float:
    """95th percentile (NumPy's default linear interpolation)."""
    return float(np.percentile(values, 95))


def path_found_block(rows: list[dict[str, Any]], status_key: str = "status_full") -> dict[str, Any]:
    """Status counts and the path-found rate among plans that do not meet the targets as they are.

    In full mode also the rate of a path whose changed plan meets every startup-program rule (at least one returned
    change-set with no rule in program_rules_not_met), and how many plans already fail a rule at the start.
    """
    statuses = Counter(row[status_key] for row in rows)
    needing = [row for row in rows if row[status_key] != "already_meets_target"]
    block = {
        "n_plans": len(rows),
        "status_counts": {
            name: statuses.get(name, 0) for name in ("already_meets_target", "paths_found", "no_path_within_bounds")
        },
        "path_found_rate_among_plans_needing_a_change": ci(
            np.mean, [row[status_key] == "paths_found" for row in needing]
        ),
        "already_meets_target_rate": ci(np.mean, [row[status_key] == "already_meets_target" for row in rows]),
    }
    if status_key == "status_full":
        block["path_found_and_program_compliant_rate_among_plans_needing_a_change"] = ci(
            np.mean,
            [row["status_full"] == "paths_found" and row["program_compliant_change_sets"] > 0 for row in needing],
        )
        block["plans_failing_a_program_rule_at_the_start"] = sum(
            bool(row["program_rules_failed_before"]) for row in rows
        )
    return block


def levers_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How many levers the returned change-sets change, and which levers they use."""
    found = [row for row in rows if row["status_full"] == "paths_found"]
    all_sets = [count for row in found for count in row["levers_per_change_set"]]
    usage_top = Counter(lever for row in found for lever in row["top_levers"])
    usage_all = Counter(lever for row in found for levers in row["all_levers"] for lever in levers)
    return {
        "n_plans_with_a_path": len(found),
        "share_top_change_set_meets_every_program_rule": ci(
            np.mean, [not row["top_program_rules_not_met"] for row in found]
        ),
        "program_rules_the_top_change_sets_still_fail": dict(
            Counter(rule for row in found for rule in row["top_program_rules_not_met"])
        ),
        "average_levers_changed_top_change_set": ci(np.mean, [len(row["top_levers"]) for row in found]),
        "average_levers_changed_all_returned_change_sets": ci(np.mean, all_sets),
        "share_single_lever_top_change_set": ci(np.mean, [len(row["top_levers"]) == 1 for row in found]),
        "change_sets_returned_per_plan": dict(sorted(Counter(row["n_change_sets"] for row in found).items())),
        "lever_use_in_top_change_sets": dict(usage_top.most_common()),
        "lever_use_in_all_returned_change_sets": dict(usage_all.most_common()),
    }


def runtime_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Runtime p50 and p95 (ms) for full, quick and the repeated quick request, with bootstrap CIs."""
    block: dict[str, Any] = {}
    for key, label in (
        ("runtime_ms_full", "full"),
        ("runtime_ms_quick", "quick"),
        ("runtime_ms_quick_repeat", "quick_repeat_warm_cache"),
    ):
        values = [row[key] for row in rows]
        block[label] = {"p50_ms": ci(p50, values), "p95_ms": ci(p95, values), "max_ms": round(max(values), 2)}
    block["call_path"] = (
        "find_paths(..., fabric=<the metro's data fabric>) for every run, the call the website makes "
        "(web/services/studio_runs.py); the fabric's mode is computed once per fabric object (search.mode_of)"
    )
    block["simulations_per_search"] = {
        "full_median": statistics.median(row["evaluations_full"] for row in rows),
        "quick_median": statistics.median(row["evaluations_quick"] for row in rows),
        "full_budget_exhausted": sum(row["budget_exhausted_full"] for row in rows),
        "quick_budget_exhausted": sum(row["budget_exhausted_quick"] for row in rows),
    }
    return block


def quick_vs_full(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often quick mode (what the website calls) gives the same answer as full mode."""
    both = [row for row in rows if row["top_signature_full"] is not None and row["top_signature_quick"] is not None]
    full_found = [row for row in rows if row["status_full"] == "paths_found"]
    quick_found = [row for row in rows if row["status_quick"] == "paths_found"]
    return {
        "status_agreement": ci(np.mean, [row["status_full"] == row["status_quick"] for row in rows]),
        "quick_finds_a_path_when_full_does": ci(np.mean, [row["status_quick"] == "paths_found" for row in full_found]),
        "full_finds_a_path_when_quick_does": ci(np.mean, [row["status_full"] == "paths_found" for row in quick_found]),
        "same_levers_in_top_change_set_when_both_find_one": ci(
            np.mean,
            [
                [item[0] for item in row["top_signature_full"]] == [item[0] for item in row["top_signature_quick"]]
                for row in both
            ],
        ),
        "identical_top_change_set_when_both_find_one": ci(
            np.mean, [row["top_signature_full"] == row["top_signature_quick"] for row in both]
        ),
        "quick_top_change_set_larger_than_full_when_both_find_one": ci(
            np.mean, [row["top_size_quick"] > row["top_size_full"] for row in both]
        ),
        "quick_top_change_set_smaller_than_full_when_both_find_one": sum(
            row["top_size_quick"] < row["top_size_full"] for row in both
        ),
        "note": "quick mode searches a subset of full mode's grid on the same paths and seed, so for single-lever "
        "change-sets quick can only return the same or a larger change; a smaller one can only come from the pair "
        "heuristic",
    }


def stress_target_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """With only the two plain targets: how often a path exists, how often it misses the simulated stress target,
    and how often it fails the lender's own single base-case NSP-4.2 check."""
    two_found = [row for row in rows if row["status_two_targets"] == "paths_found"]
    needing = [row for row in rows if row["status_two_targets"] != "already_meets_target"]
    with_ratio = [row for row in two_found if row["two_targets_top_stressed_dscr_base_case"] is not None]
    return {
        "question": "If the search used only P(DSCR >= 1.25) and P(cash < 0) (the original Prompt S5 targets), how "
        "often would the change-set it shows miss the simulated stress target (P(stressed DSCR >= 1.00) >= 0.80, a "
        "design choice), and how often would it fail the lender's single base-case NSP-4.2 check (year-2 median "
        "stressed DSCR >= 1.00)?",
        "path_found_rate_two_targets": ci(np.mean, [row["status_two_targets"] == "paths_found" for row in needing]),
        "two_target_top_change_set_fails_stress_target": ci(
            np.mean, [not row["two_targets_top_meets_stress"] for row in two_found]
        ),
        "two_target_top_change_set_fails_lender_base_case_nsp_4_2": ci(
            np.mean,
            [row["two_targets_top_stressed_dscr_base_case"] < REVENUE_STRESS_MIN_DSCR for row in with_ratio],
        ),
        "two_target_top_change_set_misses_stress_target_but_passes_lender_base_case": sum(
            not row["two_targets_top_meets_stress"]
            and row["two_targets_top_stressed_dscr_base_case"] >= REVENUE_STRESS_MIN_DSCR
            for row in with_ratio
        ),
        "plans_already_meeting_two_targets_but_not_the_stress": sum(
            row["status_two_targets"] == "already_meets_target" and row["status_full"] != "already_meets_target"
            for row in rows
        ),
    }


def checks_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The independent checks: S3 re-simulation and facts that moved (both expected to be zero)."""
    gaps = [row["s3_check_max_gap"] for row in rows if row["s3_check_max_gap"] is not None]
    return {
        "s3_resimulation": {
            "change_sets_checked": sum(row["s3_check_change_sets"] for row in rows),
            "max_abs_gap_any_probability": max(gaps) if gaps else "not measured (no change-set returned)",
            "method": "each returned change-set's applied plan is run through feasibility.simulate() with the same "
            "seed and number of paths; the three probabilities must equal the search's (common random numbers)",
        },
        "facts_moved": {
            "count": sum(len(row["facts_moved"]) for row in rows),
            "fields": dict(Counter(field for row in rows for field in row["facts_moved"])),
            "method": "every field of every applied plan (ids, the site's demand, the facts, every Plan and Loan "
            "field) is compared with the founder's plan; a field that moved is counted unless this script's own "
            "table (EXPECTED_WRITES, not guardrails.py) lets one of the change-set's levers write it. The headline "
            "plans carry no founder facts (as in the S3 worlds), so this part cannot catch hidden debt or an edited "
            "verified figure; facts_variant below does",
        },
        "facts_variant": {
            "plans": len(rows),
            "plans_with_other_debt": sum(row["facts_variant_other_debt"] for row in rows),
            "plans_with_a_verified_figure": sum(bool(row["facts_variant_verified_fields"]) for row in rows),
            "verified_figures_by_field": dict(
                Counter(field for row in rows for field in row["facts_variant_verified_fields"])
            ),
            "status_counts": dict(Counter(row["facts_variant_status"] for row in rows)),
            "change_sets_checked": sum(row["facts_variant_change_sets"] for row in rows),
            "fields_moved": sum(len(row["facts_variant_moved"]) for row in rows),
            "fields_moved_by_name": dict(Counter(field for row in rows for field in row["facts_variant_moved"])),
            "method": "each plan again with SYNTHETIC founder facts (synthetic_plans.with_synthetic_facts: credit "
            "score, experience, a seller note in about half of the plans, each typed figure verified with "
            "probability 0.25), searched in quick mode as the website does; every returned change-set and closest "
            "result is checked as above (facts unchanged, no verified figure moved, nothing outside its levers). "
            "The S3 re-simulation check is not run on these plans because S3's simulate() has no other debt",
        },
        "blocked_by_program_checks_full_mode": sum(row["blocked_by_program_full"] for row in rows),
    }


def exhaustive_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic vs brute force on the subsample."""
    needing = [row for row in rows if not row["already_meets_target"]]
    both = [row for row in needing if row["heuristic_found"] and row["reference_found"]]
    larger = [row for row in both if row["heuristic_top_larger_than_reference"]]
    return {
        "method": "quick mode's settings for both (grid, 1,000 paths, seed; the heuristic also has quick mode's "
        "budget); the brute force tries every single value and every pair of values of the available levers with "
        "the same guardrails, program checks and random draws (path_to_approval/exhaustive.py); every business type "
        f"on the SYNTHETIC metro and {', '.join(EXHAUSTIVE_TYPES_ON_REAL_METROS)} on every real metro",
        "plans_by_metro": dict(Counter(row["metro_id"] for row in rows)),
        "n_plans": len(rows),
        "n_plans_needing_a_change": len(needing),
        "reference_found_a_path": sum(row["reference_found"] for row in needing),
        "heuristic_found_a_path": sum(row["heuristic_found"] for row in needing),
        "heuristic_missed_a_path": sum(row["reference_found"] and not row["heuristic_found"] for row in needing),
        "heuristic_found_a_path_the_reference_did_not": sum(
            row["heuristic_found"] and not row["reference_found"] for row in needing
        ),
        "same_number_of_levers_when_both_found": sum(
            row["heuristic_levers"] == row["reference_levers"] for row in both
        ),
        "heuristic_top_larger_than_reference_when_both_found": len(larger),
        "larger_cases": [
            {key: row[key] for key in ("metro_id", "profile_id", "plan_index", "heuristic_effort", "reference_effort")}
            | {"reference_levers": row["reference_levers_named"]}
            for row in larger
        ],
        "heuristic_effort_over_reference_effort_when_both_found": [
            round(row["heuristic_effort"] / row["reference_effort"], 4) if row["reference_effort"] else None
            for row in both
        ],
        "reference_seconds_median": statistics.median(row["reference_seconds"] for row in rows) if rows else None,
        "rows": rows,
    }


def grouped(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """path_found_block per value of one row field."""
    values = sorted({row[key] for row in rows})
    return {value: path_found_block([row for row in rows if row[key] == value]) for value in values}


def metro_context(metro_id: str) -> dict[str, Any]:
    """The metro's mode, data label, snapshot and the status of the simulator's metro inputs (per business type)."""
    fabric = fabric_for(metro_id)
    mode, reason = mode_for(metro_id)
    snapshot = getattr(fabric, "snapshot", None)
    inputs = {}
    for profile_id in load_all_profiles():
        values = fabric.metro_inputs(profile_for(profile_id, metro_id))
        inputs[profile_id] = {name: value.status for name, value in values.items()}
    return {
        "mode": mode,
        "mode_reason": reason,
        "data_label": fabric.data_label,
        "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot is not None else None,
        "snapshot_fingerprint": snapshot.fingerprint() if snapshot is not None else None,
        "metro_input_status_by_business_type": inputs,
    }


def margin_check(profile_ids: list[str]) -> dict[str, Any]:
    """Per business type: the profile's cost ratios added up, and whether the NSP-4.2 stress can pass at the modes.

    The revenue stress removes REVENUE_STRESS_SHARE of revenue from NOI dollar for dollar. If the cost ratios alone
    (before labor and rent) leave less than that share per revenue dollar, stressed NOI is negative at ANY demand when
    every input is at its most likely value. Base profiles (no metro overrides); config values, added up here.
    """
    profiles = load_all_profiles()
    block: dict[str, Any] = {
        "stress_share": REVENUE_STRESS_SHARE,
        "method": "sum of the profile's cost ratios (low, mode, high); margin before labor and rent = 1 - sum at the "
        "modes; the stress can pass at the modes only when that margin exceeds the stress share (necessary, not "
        "sufficient: labor, rent and debt service still have to be covered)",
    }
    for profile_id in profile_ids:
        ratios = profiles[profile_id].cost_ratios.values()
        low = sum(item.low for item in ratios)
        mode = sum(item.point() for item in ratios)
        high = sum(item.high for item in ratios)
        block[profile_id] = {
            "cost_ratio_sum_low": round(low, 4),
            "cost_ratio_sum_mode": round(mode, 4),
            "cost_ratio_sum_high": round(high, 4),
            "margin_before_labor_and_rent_at_modes": round(1.0 - mode, 4),
            "stress_can_pass_at_modes_before_labor_and_rent": 1.0 - mode > REVENUE_STRESS_SHARE,
        }
    return block


def anchoring_by_type(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """How each business type's plans were anchored (demand, rent_quote, none)."""
    types = sorted({row["profile_id"] for row in rows})
    return {
        profile_id: {
            lever: sum(row["profile_id"] == profile_id and row["anchored_by"] == lever for row in rows)
            for lever in ("demand", "rent_quote", "none")
        }
        for profile_id in types
    }


def evaluate(
    metros: list[str], profile_ids: list[str], plans_per_cell: int, exhaustive_per_type: int
) -> dict[str, Any]:
    """Generate and search every plan; summarize."""
    assumptions = default_assumptions()
    all_ids = list(load_all_profiles())
    rows: list[dict[str, Any]] = []
    exhaustive_rows: list[dict[str, Any]] = []
    contexts = {}
    for metro_index, metro_id in enumerate(metros):
        fabric = fabric_for(metro_id)
        contexts[metro_id] = metro_context(metro_id)
        started = time.perf_counter()
        for profile_id in profile_ids:
            profile_index = all_ids.index(profile_id)
            profile = profile_for(profile_id, metro_id)
            inputs = fabric.metro_inputs(profile)
            for plan_index in range(plans_per_cell):
                seed = synthetic_plans.plan_seed(SEED, metro_index, profile_index, plan_index)
                plan = synthetic_plans.generate_plan(profile, metro_id, fabric.data_label, inputs, assumptions, seed)
                if plan_index == 0 and profile_id == profile_ids[0]:
                    # warm-up per metro, not timed: loads the metro's fabric, its mode and the first profile once
                    find_paths(plan.case, list(plan.candidates), cache=EvaluationCache(), fabric=fabric)
                rows.append(run_plan(plan, metro_id, plan_index, seed))
                compared = metro_id == SYNTHETIC_METRO or profile_id in EXHAUSTIVE_TYPES_ON_REAL_METROS
                if compared and plan_index < exhaustive_per_type:
                    exhaustive_rows.append(exhaustive_row(plan, plan_index))
        print(f"  {metro_id:<22} {len(profile_ids) * plans_per_cell} plans in {time.perf_counter() - started:.1f} s")
    return {"rows": rows, "exhaustive_rows": exhaustive_rows, "contexts": contexts}


def headline(rows: list[dict[str, Any]], synthetic_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers quoted in the walkthrough."""
    overall = path_found_block(rows)
    levers = levers_block(rows)
    runtime = runtime_block(rows)
    return {
        "n_plans": overall["n_plans"],
        "status_counts_full": overall["status_counts"],
        "path_found_rate_full": overall["path_found_rate_among_plans_needing_a_change"],
        "path_found_and_program_compliant_rate_full": overall[
            "path_found_and_program_compliant_rate_among_plans_needing_a_change"
        ],
        "path_found_rate_full_synthetic_metro": path_found_block(synthetic_rows)[
            "path_found_rate_among_plans_needing_a_change"
        ],
        "average_levers_changed_top_change_set": levers["average_levers_changed_top_change_set"],
        "runtime_full_p50_ms": runtime["full"]["p50_ms"],
        "runtime_full_p95_ms": runtime["full"]["p95_ms"],
        "runtime_quick_p50_ms": runtime["quick"]["p50_ms"],
        "runtime_quick_p95_ms": runtime["quick"]["p95_ms"],
    }


def build_report(
    metros: list[str], profile_ids: list[str], plans_per_cell: int, exhaustive_per_type: int
) -> dict[str, Any]:
    """The whole results document (without the provenance block)."""
    evaluated = evaluate(metros, profile_ids, plans_per_cell, exhaustive_per_type)
    rows = evaluated["rows"]
    synthetic_rows = [row for row in rows if row["metro_id"] == SYNTHETIC_METRO]
    target = default_target()
    return {
        "title": "Studio S5 path to approval: the search on seeded SYNTHETIC plans",
        "data_label": SYNTHETIC_LABEL,
        "simulation_label": SIMULATION_LABEL,
        "disclaimer": DISCLAIMER,
        "scope": SCOPE_NOTE,
        "engine_version": PATH_ENGINE_VERSION,
        "simulator_version": SIMULATOR_VERSION,
        "settings": {
            "metros": metros,
            "business_types": profile_ids,
            "plans_per_metro_and_business_type": plans_per_cell,
            "seed": SEED,
            "targets": describe(target),
            "target_sources": target.sources,
            "full_mode": FULL_SETTINGS.model_dump(),
            "quick_mode": QUICK_SETTINGS.model_dump(),
            "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "unit": "plan", "alpha": 0.05},
            "plan_generator": {
                "stressed_dscr_target_one_forecast": list(synthetic_plans.STRESSED_DSCR_TARGET),
                "bands": [list(band) for band in synthetic_plans.TARGET_BANDS],
                "staffing_plan_share": synthetic_plans.STAFFING_PLAN_SHARE,
                "candidate_sites_per_plan": synthetic_plans.N_CANDIDATES,
                "candidate_demand_factor": list(synthetic_plans.CANDIDATE_DEMAND_FACTOR),
                "candidate_rent_quote_share": synthetic_plans.CANDIDATE_RENT_QUOTE_SHARE,
                "facts_variant": {
                    "credit_score": list(synthetic_plans.FACTS_CREDIT_SCORE),
                    "experience_months": list(synthetic_plans.FACTS_EXPERIENCE_MONTHS),
                    "other_debt_share": synthetic_plans.FACTS_OTHER_DEBT_SHARE,
                    "other_debt_of_loan": list(synthetic_plans.FACTS_OTHER_DEBT_OF_LOAN),
                    "other_debt_monthly_rate": synthetic_plans.FACTS_OTHER_DEBT_MONTHLY_RATE,
                    "verified_share": synthetic_plans.FACTS_VERIFIED_SHARE,
                },
                "status": "design choices of the project author (generator parameters, not data)",
            },
        },
        "definitions": {
            "path_found_rate": "share of plans that do not meet the three targets as they are for which the search "
            "returns at least one change-set of one or two levers inside the default bounds that meets all three",
            "path_found_and_program_compliant_rate": "the same, counting only plans where at least one returned "
            "change-set also leaves the plan meeting every startup-program rule the search checks (NSP-3.1 equity "
            "injection, NSP-8.1 loan amount and term)",
            "levers_changed": "number of levers in a returned change-set (1 or 2)",
            "runtime": "wall time of one find_paths() call in a warm process, in ms (search.runtime_ms), called with "
            "the metro's data fabric as the website calls it",
            "band": "band of the plan's drawn one-forecast stressed year-2 DSCR (synthetic_plans.TARGET_BANDS)",
            "anchored_by": "how the plan was anchored to its drawn target: demand, rent_quote or none (not anchored)",
        },
        "headline": headline(rows, synthetic_rows),
        "overall_full": path_found_block(rows),
        "overall_quick": path_found_block(rows, "status_quick"),
        "by_metro": grouped(rows, "metro_id"),
        "by_business_type": grouped(rows, "profile_id"),
        "by_band": grouped(rows, "band"),
        "by_anchoring": grouped(rows, "anchored_by"),
        "anchoring_by_business_type": anchoring_by_type(rows),
        "structural_margin_check": margin_check(profile_ids),
        "targets_missed_before": dict(Counter("+".join(row["before_targets_missed"]) or "none" for row in rows)),
        "levers": levers_block(rows),
        "runtime": runtime_block(rows),
        "runtime_by_metro": {
            metro: {
                "full_p50_ms": round(p50(np.array([r["runtime_ms_full"] for r in rows if r["metro_id"] == metro])), 2),
                "quick_p50_ms": round(
                    p50(np.array([r["runtime_ms_quick"] for r in rows if r["metro_id"] == metro])), 2
                ),
            }
            for metro in metros
        },
        "quick_vs_full": quick_vs_full(rows),
        "stress_target_effect": stress_target_block(rows),
        "checks": checks_block(rows),
        "heuristic_vs_brute_force": exhaustive_block(evaluated["exhaustive_rows"]),
        "metros": evaluated["contexts"],
        "real_world_accuracy": "not measured: no real founder outcomes exist; every plan is SYNTHETIC",
        "rows": rows,
    }


def config_inputs() -> list[Path]:
    """The config files that shape the numbers (hashed into the provenance block)."""
    files = [LEVERS_FILE, ASSUMPTIONS_FILE, PROGRAM_FILE]
    files += sorted(PROFILES_DIR.glob("*.yaml"))
    files += sorted((CONFIG_DIR / "studio" / "metros").glob("*.yaml"))
    return files


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation, add provenance, write the JSON and print the headline."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plans", type=int, default=DEFAULT_PLANS_PER_CELL, help="plans per metro x business type")
    parser.add_argument(
        "--exhaustive", type=int, default=DEFAULT_EXHAUSTIVE_PER_TYPE, help="brute-force plans per business type"
    )
    parser.add_argument("--metros", nargs="*", default=None, help="metro ids (default: all)")
    parser.add_argument("--business-types", nargs="*", default=None, help="profile ids (default: all)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    run = RunProvenance.start(GENERATED_BY)
    metros = metro_ids(args.metros)
    known_types = list(load_all_profiles())
    profile_ids = args.business_types or known_types
    unknown = sorted(set(profile_ids) - set(known_types))
    if unknown:
        raise SystemExit(f"unknown business type(s) {unknown}; known: {known_types}")
    report = build_report(metros, profile_ids, args.plans, args.exhaustive)
    snapshots = {metro: block["snapshot_fingerprint"] for metro, block in report["metros"].items()}
    report["provenance"] = run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        eval_sets=config_inputs(),
        data_snapshots={"studio_snapshots": snapshots, "plan_seed": SEED},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(args.output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report["headline"], indent=2))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
