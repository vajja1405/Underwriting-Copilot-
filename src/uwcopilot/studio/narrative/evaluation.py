"""Measurements of the founder ledger and narrative on the golden scenarios (scripts/eval_studio_evidence.py).

Per scenario (evaluate_scenario):
- the ledger: number of claims per status and of checks per result;
- the template narrative: does its draft pass the grammar, how many numbers the founder reads, and how many of them
  each check (round-trip audit, bound check) cannot trace;
- the evidence appendix: every claim of the ledger rendered on its own line, the same counts (the numbers a package
  can show, as the renderer prints them);
- the mutation test on the template narrative's sentences (checker.score_scenario), for both checks;
- the wrong-slot test (score_wrong_slot): one claim slot of the template DRAFT points to another claim with the same
  display format, the way a model could pick the wrong slot; which check notices (grammar, round-trip audit, bound
  check). It measures the blind spot of all three: the rendered number is a true ledger value, cited to the claim
  it came from, so only a slot owned by a different check in an assert sentence (grammar rule V10) is caught;
- when a model was asked: whether its first draft passed, whether the retry passed, the fallback and its reason,
  the validator's findings, and the same trace counts for the final text.
Aggregates (pooled over scenarios): rates with a Wilson 95% interval and a bootstrap 95% interval that resamples
scenarios (1,000 resamples, seed 42), because sentences of one narrative are not independent.
"""

import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from uwcopilot.common.stats import rate_summary
from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.llm.base import LLMClient
from uwcopilot.provenance.memo_grammar import SLOT
from uwcopilot.provenance.mutations import CONTROL_TYPES, MUTATION_TYPES
from uwcopilot.provenance.renderer import format_value, render, round_trip_audit
from uwcopilot.risk.bootstrap import bootstrap_ci
from uwcopilot.studio.narrative.checker import bound_check, score_scenario, trace_count
from uwcopilot.studio.narrative.generate import TEMPLATE_PROVIDER, check_draft, render_narrative
from uwcopilot.studio.narrative.golden import GoldenScenario
from uwcopilot.studio.narrative.models import NarrativeResult

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
CHECKS: tuple[str, ...] = ("round_trip", "bound")
WRONG_SLOT_PER_SCENARIO = 10
WRONG_SLOT_CHECKS: tuple[str, ...] = ("grammar", "round_trip", "bound", "any")
ClientFactory = Callable[[str, str | None], LLMClient]


def ledger_summary(ledger: Ledger) -> dict[str, Any]:
    """Claims per status, checks per result, and which checks are findings (False) or not evaluated (None)."""
    results = [item.result for item in ledger.assertions]
    return {
        "claims": len(ledger.claims),
        "status_counts": ledger.status_counts(),
        "assertions": len(ledger.assertions),
        "assertion_results": {
            "true": results.count(True),
            "false": results.count(False),
            "not_evaluated": results.count(None),
        },
        "findings": [item.id for item in ledger.assertions if item.result is False],
        "not_evaluated": [item.id for item in ledger.assertions if item.result is None],
    }


def ledger_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Over all scenarios: claims per ledger (min, median, max), claims per status, and how often each check was a
    finding (False) or not evaluated (None)."""
    sizes = [row["ledger"]["claims"] for row in rows]
    statuses: dict[str, int] = {}
    findings: dict[str, int] = {}
    not_evaluated: dict[str, int] = {}
    for row in rows:
        for status, count in row["ledger"]["status_counts"].items():
            statuses[status] = statuses.get(status, 0) + count
        for assertion_id in row["ledger"]["findings"]:
            findings[assertion_id] = findings.get(assertion_id, 0) + 1
        for assertion_id in row["ledger"]["not_evaluated"]:
            not_evaluated[assertion_id] = not_evaluated.get(assertion_id, 0) + 1
    return {
        "scenarios": len(rows),
        "claims_per_ledger": {
            "min": min(sizes, default=None),
            "median": float(np.median(sizes)) if sizes else None,
            "max": max(sizes, default=None),
        },
        "claims_by_status": statuses,
        "findings_by_check": dict(sorted(findings.items())),
        "not_evaluated_by_check": dict(sorted(not_evaluated.items())),
    }


def appendix_counts(ledger: Ledger) -> dict[str, int]:
    """Every claim rendered on its own line ("{{claim:ID}}"), then traced like a narrative."""
    draft = "\n\n".join("{{claim:" + claim.id + "}}" for claim in ledger.claims)
    memo = render(draft, ledger)
    counts = trace_count(memo.body, ledger, memo.claims_used)
    return {
        "claims_rendered": len(ledger.claims),
        "numbers": counts.numbers,
        "untraced_round_trip": counts.untraced_round_trip,
        "untraced_bound": counts.untraced_bound,
    }


def narrative_counts(ledger: Ledger, result: NarrativeResult) -> dict[str, Any]:
    """Numbers in a rendered narrative and the ones each check cannot trace."""
    counts = trace_count(result.body, ledger, result.claims_used)
    return {
        "numbers": counts.numbers,
        "untraced_round_trip": counts.untraced_round_trip,
        "untraced_bound": counts.untraced_bound,
        "audit_empty": not result.audit,
    }


def llm_row(ledger: Ledger, result: NarrativeResult) -> dict[str, Any]:
    """What happened when a model was asked for this narrative."""
    attempts = [item.model_dump() for item in result.attempts]
    drafts = [item for item in result.attempts if item.error is None]
    return {
        "provider": result.provider,
        "model": result.model,
        "fallback_used": result.fallback_used,
        "fallback_reason": result.fallback_reason,
        "first_try_valid": bool(drafts) and not drafts[0].violations,
        "retry_valid": len(drafts) > 1 and not drafts[1].violations,
        "call_failed": any(item.error is not None for item in result.attempts),
        "attempts": attempts,
        "final_prompt_id": result.prompt_id,
        **narrative_counts(ledger, result),
    }


def mutation_row(scores: Any) -> dict[str, dict[str, list[int]]]:
    """{check: {mutation type: [n, flagged]}} for one scenario."""
    return {
        "round_trip": {kind: [score.n, score.flagged] for kind, score in scores.round_trip.items()},
        "bound": {kind: [score.n, score.flagged] for kind, score in scores.bound.items()},
    }


def wrong_slot_drafts(draft: str, ledger: Ledger, seed: int) -> list[str]:
    """Up to WRONG_SLOT_PER_SCENARIO drafts, each with ONE claim slot pointing to another claim of the same display.

    The donor prints a different value in the same format (a rent slot shows the loan amount), so the rendered text
    reads naturally and every number in it is still a true ledger value. Slots and donors are drawn with the seed.
    """
    rng = np.random.default_rng(seed)
    slots = [match for match in SLOT.finditer(draft) if match.group("kind") == "claim"]
    drafts = []
    for index in rng.permutation(len(slots)):
        match = slots[int(index)]
        claim = ledger.claim(match.group("id"))
        if claim is None or claim.value is None:
            continue
        shown = format_value(claim.value, claim.display)
        donors = [
            other.id
            for other in ledger.claims
            if other.id != claim.id
            and other.display == claim.display
            and other.value is not None
            and format_value(other.value, other.display) != shown
        ]
        if not donors:
            continue
        donor = donors[int(rng.integers(0, len(donors)))]
        drafts.append(draft[: match.start()] + "{{claim:" + donor + "}}" + draft[match.end() :])
        if len(drafts) == WRONG_SLOT_PER_SCENARIO:
            break
    return drafts


def score_wrong_slot(draft: str, ledger: Ledger, seed: int) -> dict[str, int]:
    """How many wrong-slot drafts each check flags: the grammar, the round-trip audit, the bound check, or any."""
    counts = dict.fromkeys(("n", *WRONG_SLOT_CHECKS), 0)
    for mutated in wrong_slot_drafts(draft, ledger, seed):
        memo = render(mutated, ledger)
        flags = {
            "grammar": bool(check_draft(ledger, mutated)),
            "round_trip": bool(round_trip_audit(memo, ledger)),
            "bound": bound_check(memo.body, ledger, memo.claims_used).flagged,
        }
        flags["any"] = any(flags.values())
        counts["n"] += 1
        for check, flagged in flags.items():
            counts[check] += int(flagged)
    return counts


def wrong_slot_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall of each check on the wrong-slot drafts, pooled over scenarios (the measured blind spot)."""
    n = [row["wrong_slot"]["n"] for row in rows]
    return {check: pooled([row["wrong_slot"][check] for row in rows], n) for check in WRONG_SLOT_CHECKS}


def evaluate_scenario(
    scenario: GoldenScenario,
    provider: str = TEMPLATE_PROVIDER,
    model: str | None = None,
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """Every measurement of one scenario (module docstring)."""
    started = time.perf_counter()
    ledger = scenario.ledger()
    template = render_narrative(ledger)
    scores, _ = score_scenario(scenario.scenario_id, template.body, ledger, template.claims_used, scenario.seed)
    row: dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "metro_id": scenario.metro_id,
        "profile_id": scenario.profile_id,
        "metro_mode": scenario.metro_mode,
        "seed": scenario.seed,
        "site_scored": scenario.site_score is not None,
        "path_status": scenario.path_result.status if scenario.path_result is not None else None,
        "notes": list(scenario.notes),
        "ledger": ledger_summary(ledger),
        "template": {
            "valid": not check_draft(ledger, template.draft),
            "violations": [f"{item.code}: {item.span}" for item in check_draft(ledger, template.draft)],
            **narrative_counts(ledger, template),
        },
        "appendix": appendix_counts(ledger),
        "mutations": mutation_row(scores),
        "wrong_slot": score_wrong_slot(template.draft, ledger, scenario.seed),
        "sentences": scores.n_sentences,
        "llm": None,
    }
    if provider != TEMPLATE_PROVIDER:
        result = render_narrative(ledger, provider, model=model, client_factory=client_factory)
        row["llm"] = llm_row(ledger, result)
    row["seconds"] = round(time.perf_counter() - started, 3)
    return row


# --------------------------------------------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------------------------------------------


def pooled(k: Sequence[int], n: Sequence[int]) -> dict[str, Any]:
    """Pooled rate sum(k) / sum(n) with a Wilson 95% interval and a scenario-bootstrap 95% interval."""
    k_array, n_array = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    total_k, total_n = int(k_array.sum()), int(n_array.sum())
    summary = rate_summary(total_k, total_n)
    block: dict[str, Any] = {"k": total_k, "n": total_n, "rate": summary["rate"], "wilson_ci95": summary["wilson_ci95"]}
    if total_n == 0 or len(k_array) < 2:
        block["bootstrap_ci95"] = None
        return block

    def rate(kk: np.ndarray, nn: np.ndarray) -> float:
        return float(kk.sum() / nn.sum()) if nn.sum() > 0 else float("nan")

    ci = bootstrap_ci(rate, k_array, n_array, n_resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED)
    block["bootstrap_ci95"] = {
        "low": round(ci["ci_low"], 4),
        "high": round(ci["ci_high"], 4),
        "n_resamples": ci["n_resamples"],
        "n_valid_resamples": ci["n_valid_resamples"],
        "seed": ci["seed"],
        "resampled": "scenarios",
    }
    return block


def traced_share(rows: list[dict[str, Any]], section: str, check: str) -> dict[str, Any]:
    """Share of numbers that trace to the ledger (1 - untraced / numbers), pooled over scenarios."""
    numbers = [row[section]["numbers"] for row in rows]
    traced = [row[section]["numbers"] - row[section][f"untraced_{check}"] for row in rows]
    block = pooled(traced, numbers)
    block["untraced_total"] = sum(row[section][f"untraced_{check}"] for row in rows)
    return block


def recall_block(rows: list[dict[str, Any]], check: str) -> dict[str, Any]:
    """Recall per mutation type and false-flag rate per control, for one check."""
    recall: dict[str, Any] = {}
    for kind in MUTATION_TYPES:
        counts = [row["mutations"][check].get(kind, [0, 0]) for row in rows]
        block = pooled([flagged for _, flagged in counts], [n for n, _ in counts])
        if block["n"] == 0:
            block["note"] = "not applicable: founder narratives have no tables, so no wrong-row mutation exists"
        recall[kind] = block
    false_flags: dict[str, Any] = {}
    for kind in CONTROL_TYPES:
        counts = [row["mutations"][check].get(kind, [0, 0]) for row in rows]
        false_flags[kind] = pooled([flagged for _, flagged in counts], [n for n, _ in counts])
    return {"recall_by_type": recall, "false_flag_rate": false_flags}


NO_ANSWER_REASON = (
    "not measured: the model was asked for {asked} narratives but no call returned an answer (the provider could not "
    "be built or reached), so there is no fallback rate of a model to report"
)


def answered(item: dict[str, Any]) -> bool:
    """True when at least one call to the model returned a draft for this narrative."""
    return any(attempt["error"] is None for attempt in item["attempts"])


def llm_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fallback rate and first-try / retry validity of the model's drafts (only rows where a model was asked).

    The rates are reported only when the model answered at least once in the run: a provider that could not be
    built or reached produced no drafts, and calling its template fallbacks a "fallback rate of 1.0" would describe
    a model run that never happened. Such a run is "not measured", with the reasons.
    """
    asked = [row["llm"] for row in rows if row["llm"] is not None]
    if not asked:
        return {"measured": False}
    if not any(answered(item) for item in asked):
        return {
            "measured": False,
            "reason": NO_ANSWER_REASON.format(asked=len(asked)),
            "narratives_asked": len(asked),
            "fallback_reasons": sorted({item["fallback_reason"] for item in asked if item["fallback_reason"]}),
        }
    codes: dict[str, int] = {}
    latencies = []
    cached = 0
    for item in asked:
        for attempt in item["attempts"]:
            for line in attempt["violations"]:
                code = line.split(":", 1)[0]
                codes[code] = codes.get(code, 0) + 1
            if attempt["latency_s"] is not None:
                latencies.append(attempt["latency_s"])
            cached += int(attempt["cached"])
    ones = [1] * len(asked)
    return {
        "measured": True,
        "provider": asked[0]["provider"],
        "model": asked[0]["model"],
        "narratives_asked": len(asked),
        "fallback_rate": pooled([int(item["fallback_used"]) for item in asked], ones),
        "first_try_valid_rate": pooled([int(item["first_try_valid"]) for item in asked], ones),
        "valid_after_retry_rate": pooled([int(item["retry_valid"]) for item in asked], ones),
        "call_failed": sum(int(item["call_failed"]) for item in asked),
        "fallback_reasons": sorted({item["fallback_reason"] for item in asked if item["fallback_reason"]}),
        "violation_code_counts": dict(sorted(codes.items())),
        "llm_calls": sum(len(item["attempts"]) for item in asked),
        "cached_calls": cached,
        "latency_s_per_call": {
            "median": round(float(np.median(latencies)), 3) if latencies else None,
            "max": round(float(np.max(latencies)), 3) if latencies else None,
            "note": "wall time on a shared 4-CPU machine; cached calls take milliseconds",
        },
        "final_text_traced_share_round_trip": traced_share([{"llm": item} for item in asked], "llm", "round_trip"),
        "final_text_traced_share_bound": traced_share([{"llm": item} for item in asked], "llm", "bound"),
    }
