"""The report sections of the Phase 4 F evaluation, from the per-packet records of evaluation_packet.py.

Every rate is a ratio of counts SUMMED over packets (micro-average) with a packet-level percentile bootstrap 95%
interval (1,000 resamples, seed 42) and the exact Clopper-Pearson interval, computed by docintel's ratio_metric
(docintel/evaluation_stats.py, evaluation_intervals.py): the same statistics as the Phase 2 evaluation. Counts that
are distributions (confusion matrices, which shock flips the recommendation) are reported as counts.

paired_ratio_difference compares two modes on the SAME packets (pipeline minus oracle): both modes of a packet go
into one bootstrap row, so every resample uses the same packets for both.
"""

from collections import Counter
from typing import Any

from uwcopilot.docintel.evaluation_stats import bootstrap, counts_of, ratio_metric, safe_ratio, totals
from uwcopilot.evidence.evaluation import (
    BY_DESIGN_PATTERNS,
    BY_DESIGN_PREFIXES,
    CLAIM_STATUS_LABEL,
    EXPECTED_STATUSES,
    EXPECTED_TABLE_VERSION,
    PREDICTED_STATUSES,
    expected_table_sha256,
)
from uwcopilot.evidence.evaluation_calcs import TOLERANCES
from uwcopilot.evidence.evaluation_requests import CIRCULAR_LABEL, NBE_DEFINITION, REVEAL_DEFINITION, TOP_K

CLAIM_NOTE = (
    "Expected statuses come from the truth's planted problems and missing documents (evidence/evaluation.py, table "
    "version and sha256 below). Most claims are expected VERIFIED, so overall accuracy is dominated by them; "
    "open_expected is the accuracy on claims expected CONFLICTING or UNKNOWN. ABSENT: a claim the table requires "
    "is not in the ledger (also every required claim of a packet whose application did not validate). A packet "
    "whose application did not validate has no ledger, so its VERIFIED claims cannot be scored: read the accuracy "
    "together with 'evaluable'. The claim id scheme of the table and of the ledger were written by the same author, "
    "so this measures detection and propagation, not an independent claim taxonomy: it is agreement with the "
    "documented status rules, and a status rule that is wrong in both tables is scored as correct. Table v2 adds "
    "dependency-aware expectations (a calculation over a value no document provided is expected UNKNOWN). NOT HELD "
    "OUT: table v2 was written after the v1 test-split numbers had been seen (protocol.development_note), so the "
    "test split is no longer untouched for this table and these numbers are not a held-out measurement."
)
CALC_NOTE = (
    "The reference policy's calculations on the engine's application against the same calculations on "
    "truth.application_as_documented. A calculation of a packet whose application did not validate counts as "
    "'missing' (disagreement). In oracle mode this checks the document-to-application mapping."
)
EXPLANATION_NOTE = (
    "Restated with docintel's matching (evaluation_problems.py) on this mode's contradictions: one representative "
    "contradiction per detected benign or unsupported problem; 'unknown' counts as wrong. In pipeline mode this "
    "repeats the Phase 2 measurement."
)
SCENARIO_NOTE = (
    "Counts over evaluable packets. A family's first flip is the smallest tested shock that changes the reference "
    "recommendation ('none': every tested shock keeps it; 'untested': no scenario of the family could be tested). "
    "A scenario counts as tested only when it could show something: one that changes nothing (a frequency shock "
    "that rounds to zero whole claims) or leaves the decision metric not computable before and after with the same "
    "recommendation is NOT tested (not_tested_by_reason), so a packet with no tested shock is 'undetermined', never "
    "'stable'. Stability is a property of the reference policy on these SYNTHETIC cases."
)


def counter_group(records: list[dict[str, Any]], prefix: str) -> dict[str, int]:
    """Summed counters whose names start with prefix, keyed by the rest of the name."""
    summed = totals(counts_of(records))
    return {name[len(prefix) :]: int(value) for name, value in sorted(summed.items()) if name.startswith(prefix)}


def nested_counts(flat: dict[str, int]) -> dict[str, dict[str, int]]:
    """{"a.b": n} -> {"a": {"b": n}} (split at the first dot)."""
    nested: dict[str, dict[str, int]] = {}
    for name, value in flat.items():
        outer, _, inner = name.partition(".")
        nested.setdefault(outer, {})[inner] = value
    return nested


def ratios_by_key(records: list[dict[str, Any]], prefix: str, keys: list[str], hit: str) -> dict[str, Any]:
    """ratio_metric(prefix.<key>.<hit>, prefix.<key>.total) for every key with a nonzero total."""
    summed = totals(counts_of(records))
    return {
        key: ratio_metric(records, f"{prefix}.{key}.{hit}", f"{prefix}.{key}.total")
        for key in keys
        if summed.get(f"{prefix}.{key}.total", 0) > 0
    }


def keys_under(records: list[dict[str, Any]], prefix: str) -> list[str]:
    """The distinct name parts right after prefix + '.' in any record ("calcs.unit" -> ["pct", "ratio", ...])."""
    start = prefix + "."
    found = set()
    for record in records:
        for name in record["counts"]:
            if name.startswith(start):
                found.add(name[len(start) :].split(".", 1)[0])
    return sorted(found)


def claim_status_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Claim-status accuracy overall, on open claims, by expected and predicted status, by problem type."""
    excluded: Counter = Counter()
    for record in records:
        excluded.update(record["excluded_by_design"])
    unobservable = counter_group(records, "claims.by_problem.")
    reasons = {**BY_DESIGN_PREFIXES, **{key: reason for key, (_, reason) in BY_DESIGN_PATTERNS.items()}}
    return {
        "label": CLAIM_STATUS_LABEL,
        "note": CLAIM_NOTE,
        "expected_table": {"version": EXPECTED_TABLE_VERSION, "sha256": expected_table_sha256()},
        "accuracy": ratio_metric(records, "claims.correct", "claims.total"),
        "open_expected_accuracy": ratio_metric(records, "claims.open_expected.correct", "claims.open_expected.total"),
        "by_expected_status": ratios_by_key(records, "claims.expected", list(EXPECTED_STATUSES), "correct"),
        "by_predicted_status": ratios_by_key(records, "claims.predicted", list(PREDICTED_STATUSES), "correct"),
        "confusion_expected_x_predicted": nested_counts(counter_group(records, "claims.confusion.")),
        "by_problem_type": ratios_by_key(
            records, "claims.by_problem", keys_under(records, "claims.by_problem"), "correct"
        ),
        "unobservable_problems": {
            name.split(".")[0]: value for name, value in unobservable.items() if name.endswith(".unobservable")
        },
        "excluded_by_design": {
            key: {"claims": int(excluded.get(key, 0)), "reason": reason} for key, reason in reasons.items()
        },
    }


def explanation_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Benign vs unsupported explanations of matched problems (restated from docintel's matching)."""
    return {
        "note": EXPLANATION_NOTE,
        "problem_detection_recall": ratio_metric(records, "problems.all.detected", "problems.all.total"),
        "accuracy": ratio_metric(records, "explanations.all.correct", "explanations.all.total"),
        "by_truth_class": ratios_by_key(records, "explanations", ["benign", "unsupported"], "correct"),
        "decidable_accuracy": ratio_metric(records, "explanations.decidable.correct", "explanations.decidable.total"),
        "unknown_answers": int(totals(counts_of(records)).get("explanations.all.unknown", 0)),
        "confusion_truth_x_predicted": nested_counts(counter_group(records, "explanations.confusion.")),
    }


def nbe_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The CIRCULAR next-best-evidence agreement with the truth's resolving request."""
    summed = totals(counts_of(records))
    return {
        "label": CIRCULAR_LABEL,
        "definition": NBE_DEFINITION,
        "top_1": ratio_metric(records, "nbe.top_1", "nbe.eligible"),
        f"top_{TOP_K}": ratio_metric(records, f"nbe.top_{TOP_K}", "nbe.eligible"),
        "mapping": ratio_metric(records, "nbe.mapping_agrees", "nbe.with_item"),
        "problems": int(summed.get("nbe.problems", 0)),
        "problems_with_item": int(summed.get("nbe.with_item", 0)),
        "problems_eligible_nonzero_voi": int(summed.get("nbe.eligible", 0)),
    }


def reveal_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of packets where answering the rank-1 request with the latent truth changes the recommendation."""
    summed = totals(counts_of(records))
    return {
        "definition": REVEAL_DEFINITION,
        "changed": ratio_metric(records, "reveal.all.changed", "reveal.all.revealed"),
        "changed_when_top_voi_positive": ratio_metric(
            records, "reveal.voi_positive.changed", "reveal.voi_positive.revealed"
        ),
        "changed_when_top_voi_zero": ratio_metric(records, "reveal.voi_zero.changed", "reveal.voi_zero.revealed"),
        "packets_without_request": int(summed.get("reveal.no_request", 0)),
        "packets_top_request_without_planted_problem": int(summed.get("reveal.no_truth_problem", 0)),
        "packets_reveal_failed": int(summed.get("reveal.failed", 0)),
        "packets_not_evaluable": int(summed.get("reveal.not_evaluable", 0)),
    }


def calc_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculation agreement with the truth's application_as_documented, overall, per unit and per calc id."""
    by_id = nested_counts(counter_group(records, "calcs.id."))
    return {
        "note": CALC_NOTE,
        "tolerances": TOLERANCES,
        "agreement": ratio_metric(records, "calcs.all.agree", "calcs.all.total"),
        "by_unit": ratios_by_key(records, "calcs.unit", keys_under(records, "calcs.unit"), "agree"),
        "by_calc_id": {
            calc_id: {"agree": n.get("agree", 0), "total": n.get("total", 0)} for calc_id, n in by_id.items()
        },
        "outcomes": counter_group(records, "calcs.outcome."),
    }


def scenario_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Decision stability and per-scenario counts."""
    summed = totals(counts_of(records))
    return {
        "note": SCENARIO_NOTE,
        "stable_share": ratio_metric(records, "stability.stable", "stability.packets"),
        "stable": int(summed.get("stability.stable", 0)),
        "unstable": int(summed.get("stability.unstable", 0)),
        "undetermined": int(summed.get("stability.undetermined", 0)),
        "first_flip_by_family": nested_counts(counter_group(records, "stability.first_flip.")),
        "not_tested_by_reason": counter_group(records, "scenarios_not_tested."),
        "by_scenario": nested_counts(counter_group(records, "scenarios.")),
    }


def summarize_mode(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every section of one mode."""
    return {
        "n_packets": len(records),
        "packets_by_line": dict(sorted(Counter(record["line"] for record in records).items())),
        "evaluable": ratio_metric(records, "packets.evaluable", "packets.total"),
        "recommendations": counter_group(records, "recommendation."),
        "claim_status": claim_status_summary(records),
        "benign_vs_unsupported": explanation_summary(records),
        "next_best_evidence_agreement_circular": nbe_summary(records),
        "top_request_reveal": reveal_summary(records),
        "calculation_agreement": calc_summary(records),
        "scenarios_and_stability": scenario_summary(records),
    }


def paired_ratio_difference(
    first: list[dict[str, Any]], second: list[dict[str, Any]], numerator: str, denominator: str
) -> dict[str, Any]:
    """ratio(first) - ratio(second) on the packets both lists have (aligned by packet_id), with a paired
    packet-level bootstrap interval."""
    by_id = {record["packet_id"]: record for record in second}
    rows = []
    for record in first:
        other = by_id.get(record["packet_id"])
        if other is None:
            continue
        row = {f"a.{name}": float(record["counts"].get(name, 0)) for name in (numerator, denominator)}
        row.update({f"b.{name}": float(other["counts"].get(name, 0)) for name in (numerator, denominator)})
        rows.append(row)

    def difference(counts: dict[str, float]) -> float | None:
        a_value = safe_ratio(counts.get(f"a.{numerator}", 0), counts.get(f"a.{denominator}", 0))
        b_value = safe_ratio(counts.get(f"b.{numerator}", 0), counts.get(f"b.{denominator}", 0))
        return None if a_value is None or b_value is None else a_value - b_value

    names = [f"{side}.{name}" for side in ("a", "b") for name in (numerator, denominator)]
    return bootstrap(rows, difference, names)
