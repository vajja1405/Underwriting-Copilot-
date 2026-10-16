"""One SYNTHETIC packet evaluated in one mode (Phase 4 F) -> a JSON-simple record of counts and per-item rows.

Modes:
- "oracle": the engine starts from the generator's perfect extraction (truth.oracle_extraction), with the oracle page
  text scanned for embedded instructions, exactly as the innovation S5 simulator prepares a case
  (simulator_cases.prepare_case). It isolates the engine from extraction errors.
- "pipeline": the engine starts from the Phase 2 pipeline's output for the packet PDF (rules extractor, Tesseract OCR
  for scanned pages): end to end.

The engine (evidence.engine.run_engine) never sees the truth. The truth is used here, after the run, to score:
claim statuses (evaluation.py), benign vs unsupported explanations of the matched contradictions (docintel
evaluation_problems, restated), the circular next-best-evidence agreement and the top-request reveal
(evaluation_requests.py), calculation agreement with the truth's application_as_documented (evaluation_calcs.py) and
scenario / stability counts. The reference for the expected claim table and for calculation agreement is the SAME
reference policy run on truth.application_as_documented with the clean screening assumption and no document flags
(reference_result): calculations do not depend on flags.

This is EVALUATION code (it reads ground truth); no engine module imports it.
"""

from collections import Counter
from typing import TYPE_CHECKING, Any

from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.evaluation_problems import contradiction_counts, match_contradictions
from uwcopilot.evidence.engine import EngineResult, run_engine
from uwcopilot.evidence.evaluation import (
    claim_counts,
    expected_claims,
    expected_patterns,
    mismatches,
    problem_claim_counts,
    score_claims,
    unobservable_problems,
)
from uwcopilot.evidence.evaluation_calcs import calc_counts, calc_rows
from uwcopilot.evidence.evaluation_requests import (
    document_id_map,
    nbe_counts,
    nbe_rows,
    top_request_reveal,
    translate_items,
)
from uwcopilot.evidence.open_item_models import packet_context
from uwcopilot.evidence.simulator_cases import SimCase, prepare_case
from uwcopilot.evidence.state import IncompleteApplicationError, evaluate, from_application, from_packet

if TYPE_CHECKING:  # the pipeline imports the PDF and OCR stack; only its result type is needed here
    from uwcopilot.docintel.pipeline import PacketResult

ORACLE = "oracle"
PIPELINE = "pipeline"
MODES: tuple[str, ...] = (ORACLE, PIPELINE)


def oracle_case(truth: Any) -> SimCase:
    """The engine's starting point from the perfect extraction (simulator_cases.prepare_case)."""
    return prepare_case(truth)


def pipeline_case(truth: Any, result: "PacketResult") -> SimCase:
    """The engine's starting point from the Phase 2 pipeline output of the packet PDF."""
    state = from_packet(
        result.extraction, result.checks, result.contradictions, result.injection_hits, case_id=truth.packet_id
    )
    context = packet_context(result.extraction, result.checks, result.contradictions)
    return SimCase(truth=truth, extraction=result.extraction, context=context, initial=state)


def reference_result(truth: Any) -> PolicyResult | None:
    """The reference policy on truth.application_as_documented (clean screening assumption, no document flags);
    None when that application does not validate."""
    state = from_application(
        dict(truth.application_as_documented), document_checks=[], injection_flags=[], case_id=truth.packet_id
    )
    try:
        return evaluate(state)
    except IncompleteApplicationError:
        return None


def page_documents(truth: Any) -> dict[int, str]:
    """Truth page number -> truth document id."""
    return {page.page_number: page.doc_id for page in truth.pages}


def explanation_rows(truth: Any, contradictions: list[Any], page_docs: dict[int, str]) -> tuple[list, Counter]:
    """docintel's contradiction matching restated on this mode's contradictions: per-problem rows and counters
    (problems.* recall, explanations.* benign vs unsupported)."""
    doc_types = {document.doc_id: document.doc_type for document in truth.documents}
    problems, found = match_contradictions(list(truth.problems), contradictions, page_docs, doc_types)
    return problems, contradiction_counts(problems, found)


def family_flip(family: Any) -> str:
    """The first flipping scenario of a family, 'none' when every tested shock keeps the recommendation, or
    'untested' when no scenario of the family could be evaluated."""
    if not family.tested:
        return "untested"
    return family.first_flip or "none"


def scenario_counts(result: EngineResult) -> Counter:
    """stability.* and scenarios.* counters of an evaluable result."""
    counts: Counter = Counter()
    if result.stability is None:
        return counts
    stable = result.stability.stable
    counts["stability.packets"] += 1
    counts["stability.stable"] += int(stable is True)
    counts["stability.unstable"] += int(stable is False)
    counts["stability.undetermined"] += int(stable is None)
    for family in result.stability.families:
        counts[f"stability.first_flip.{family.family}.{family_flip(family)}"] += 1
    for scenario in result.scenarios:
        counts[f"scenarios.{scenario.scenario_id}.{scenario.status}"] += 1
        counts[f"scenarios.{scenario.scenario_id}.changed"] += int(scenario.recommendation_changed)
        if scenario.not_evaluated_code is not None:
            counts[f"scenarios_not_tested.{scenario.not_evaluated_code}"] += 1
    return counts


def stability_row(result: EngineResult) -> dict[str, Any] | None:
    """The packet's stability summary (None when not evaluable)."""
    if result.stability is None:
        return None
    return {
        "stable": result.stability.stable,
        "first_flips": {family.family: family_flip(family) for family in result.stability.families},
        "counterfactual_changes": list(result.stability.counterfactual_changes),
    }


def evaluate_case(case: SimCase, mode: str, reference: PolicyResult | None) -> dict[str, Any]:
    """Run the engine on one case and score it against the truth (module docstring)."""
    truth = case.truth
    result = run_engine(case.initial, context=case.context)
    counts: Counter = Counter({"packets.total": 1, "packets.evaluable": int(result.evaluable)})
    counts["reference.not_evaluable"] += int(reference is None)
    counts[f"recommendation.{result.recommendation or 'not_evaluable'}"] += 1
    reference_calcs = [] if reference is None else list(reference.calculations)
    predicted = {} if result.ledger is None else {claim.id: claim.status for claim in result.ledger.claims}
    scores, excluded = score_claims(predicted, expected_claims(truth, reference_calcs), expected_patterns(truth))
    counts.update(claim_counts(scores))
    counts.update(problem_claim_counts(truth, scores))
    page_docs = page_documents(truth)
    explanations, explanation_counts = explanation_rows(truth, list(case.context.contradictions), page_docs)
    counts.update(explanation_counts)
    items = translate_items(result.open_items, document_id_map(case.extraction, page_docs))
    requests = nbe_rows(truth.problems, items, result.next_best_evidence)
    counts.update(nbe_counts(requests))
    if result.evaluable:
        reveal, reveal_counts = top_request_reveal(case, items, result.next_best_evidence, result.recommendation)
    else:
        reveal, reveal_counts = {"outcome": "not_evaluable"}, Counter({"reveal.not_evaluable": 1})
    counts.update(reveal_counts)
    calculations = calc_rows(reference_calcs, result.calculations)
    counts.update(calc_counts(calculations))
    counts.update(scenario_counts(result))
    return {
        "packet_id": truth.packet_id,
        "line": truth.line,
        "scenario": truth.scenario,
        "mode": mode,
        "evaluable": result.evaluable,
        "not_evaluable_reason": result.not_evaluable_reason,
        "recommendation": result.recommendation,
        "counts": dict(sorted(counts.items())),
        "claim_mismatches": mismatches(scores),
        "unobservable_problems": unobservable_problems(truth),
        "excluded_by_design": excluded,
        "explanations": explanations,
        "next_best_evidence": requests,
        "top_request_reveal": reveal,
        "calculation_disagreements": [row for row in calculations if row["outcome"] != "agree"],
        "stability": stability_row(result),
    }
