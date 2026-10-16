"""Evaluation of the next-best-evidence ranking on SYNTHETIC packets (Phase 4 F): agreement with the truth's
resolving request (a CIRCULAR metric) and what happens when the top-ranked request is answered with the truth.

This is EVALUATION code: it reads the generator's ground truth through evidence.simulator_cases, the same module
the innovation S5 simulator uses, so the two evaluations link open items to planted problems by ONE rule
(simulator_cases.matches) and reveal latent truths in ONE order (simulator_cases.revealed_state). No engine module
imports this one.

1. Agreement with the resolving request (nbe_rows / nbe_counts). LABELLED CIRCULAR: the generator writes each
   problem's resolving_request and the engine's request rules (open_items.py) were written by the same author, so
   agreement mostly shows that the two tables match, not that the ranking is useful. innovation S5's simulator is
   the non-circular evaluation. Definitions, per planted problem:
   - its items: the open items that stand for it (simulator_cases.matches, after translating the item's document
     ids to the truth's: translate_items);
   - eligible: at least one of its items belongs to a ranked request with VOI above 0 ("problems whose items have
     nonzero VOI", the spec's population);
   - top-1 / top-3 agreement: the truth's resolving request is the rank-1 request / one of the first three ranked
     requests (request id equal, and every parameter the truth gives agrees: request_agrees);
   - mapping agreement (problems with at least one item): the request of one of its items agrees with the truth's
     resolving request, whatever its rank.
2. Top-request reveal (top_request_reveal): the rank-1 request is answered with the latent (true) resolution of the
   planted problems its items stand for, the reference policy is re-run, and the recommendation is compared with the
   current one. A rank-1 request that stands for no planted problem has no latent truth to reveal (counted apart);
   a reveal whose levers cannot be applied is counted as failed, never guessed.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.simulator_cases import SimCase, matches, revealed_state
from uwcopilot.evidence.state import evaluate
from uwcopilot.evidence.voi_models import RankedRequest

TOP_K = 3
DOCUMENT_PARAM = "document"
CIRCULAR_LABEL = (
    "CIRCULAR: the generator's resolving_request and the engine's request rules were written by the same author, "
    "so agreement mainly shows that two hand-written tables match. It is not evidence that the ranking is useful; "
    "the innovation S5 simulator (section 'simulator') is the non-circular evaluation."
)
NBE_DEFINITION = (
    "Per planted problem. Its items are the open items that stand for it (evidence.simulator_cases.matches). "
    "Eligible: one of its items belongs to a ranked request with VOI > 0. top_1 / top_3: the truth's resolving "
    "request (id, and every parameter the truth gives; a multi-document request agrees when it lists the truth's "
    "document) is ranked first / in the first three. mapping: among problems with an item, the request of one of "
    "its items agrees with the truth's, whatever the rank."
)
REVEAL_DEFINITION = (
    "Per packet with at least one ranked request: the rank-1 request is answered with the latent (true) resolution "
    "of the planted problems its items stand for (applied in the simulator's canonical order), the reference policy "
    "is re-run, and changed = the recommendation differs from the current one. Packets whose rank-1 request stands "
    "for no planted problem, and reveals whose levers could not be applied, are counted apart and are not in the "
    "denominator. A reveal applies the generator's latent substitutions, which are not always the template option "
    "the engine valued (insurance problems that change claims also rewrite the stated claim history: simulator "
    "diagnostics 'coupling'), so 'changed when the top VOI is 0' is a measurement, not 0 by construction."
)


def normalized(text: Any) -> str:
    """Text casefolded with single spaces (a carrier name read by OCR may differ only in case or spacing)."""
    return " ".join(str(text).split()).casefold()


def request_agrees(problem: Any, request_id: str | None, params: dict[str, str]) -> bool:
    """True when a request equals the problem's resolving request: same id, and every parameter the truth gives
    agrees. A "document" parameter agrees when the request lists the truth's code (one request asks for every
    missing document: "code1, code2"); other parameters agree when equal after normalization."""
    if request_id != problem.resolving_request:
        return False
    for key, wanted in problem.resolving_request_params.items():
        given = params.get(key)
        if given is None:
            return False
        if key == DOCUMENT_PARAM:
            if wanted not in [part.strip() for part in given.split(",")]:
                return False
        elif normalized(given) != normalized(wanted):
            return False
    return True


def document_id_map(extraction: Any, page_docs: dict[int, str]) -> dict[str, str]:
    """Extraction document id -> truth document id (the truth document of the document's first page).

    The pipeline numbers the documents it segments; an oracle extraction already uses the truth's ids (identity).
    """
    mapping = {}
    for document in extraction.documents:
        if document.pages and document.pages[0] in page_docs:
            mapping[document.doc_id] = page_docs[document.pages[0]]
    return mapping


def translate_items(items: Iterable[OpenItem], doc_map: dict[str, str]) -> list[OpenItem]:
    """The items with their document ids translated to the truth's (unmapped ids are kept)."""
    return [item.model_copy(update={"doc_ids": tuple(doc_map.get(doc, doc) for doc in item.doc_ids)}) for item in items]


def ranked_for_items(ranked: list[RankedRequest], item_ids: set[str]) -> list[RankedRequest]:
    """The ranked requests that contain any of the item ids."""
    return [request for request in ranked if item_ids & set(request.item_ids)]


def truth_request_rank(problem: Any, ranked: list[RankedRequest]) -> int | None:
    """The best rank of a ranked request that agrees with the problem's resolving request (None when none does)."""
    ranks = [request.rank for request in ranked if request_agrees(problem, request.request_id, request.params)]
    return min(ranks) if ranks else None


def nbe_rows(problems: Iterable[Any], items: list[OpenItem], ranked: list[RankedRequest]) -> list[dict[str, Any]]:
    """One JSON-simple row per planted problem (definitions in the module docstring)."""
    rows = []
    for problem in problems:
        own = [item for item in items if matches(item, problem)]
        containing = ranked_for_items(ranked, {item.item_id for item in own})
        rows.append(
            {
                "problem_id": problem.problem_id,
                "problem_type": problem.problem_type,
                "resolving_request": problem.resolving_request,
                "item_ids": [item.item_id for item in own],
                "item_requests": [item.request_id for item in own],
                "nonzero_voi": any(request.voi > 0 for request in containing),
                "mapping_agrees": any(request_agrees(problem, item.request_id, item.request_params) for item in own),
                "truth_request_rank": truth_request_rank(problem, ranked),
            }
        )
    return rows


def nbe_counts(rows: list[dict[str, Any]]) -> Counter:
    """nbe.* counters of one packet's rows (nbe_rows)."""
    counts: Counter = Counter()
    for row in rows:
        counts["nbe.problems"] += 1
        if not row["item_ids"]:
            continue
        counts["nbe.with_item"] += 1
        counts["nbe.mapping_agrees"] += int(row["mapping_agrees"])
        if not row["nonzero_voi"]:
            continue
        rank = row["truth_request_rank"]
        counts["nbe.eligible"] += 1
        counts["nbe.top_1"] += int(rank == 1)
        counts[f"nbe.top_{TOP_K}"] += int(rank is not None and rank <= TOP_K)
    return counts


def top_request_reveal(
    case: SimCase, items: list[OpenItem], ranked: list[RankedRequest], current: str
) -> tuple[dict[str, Any], Counter]:
    """Answer the rank-1 request with the latent truth and re-run the reference policy (module docstring).

    items must already carry truth document ids (translate_items). Returns the packet's row and reveal.* counters.
    """
    counts: Counter = Counter()
    if not ranked:
        counts["reveal.no_request"] += 1
        return {"outcome": "no_request"}, counts
    top = ranked[0]
    top_items = [item for item in items if item.item_id in top.item_ids]
    problem_ids = sorted({p.problem_id for p in case.problems for item in top_items if matches(item, p)})
    row: dict[str, Any] = {"request_id": top.request_id, "voi": top.voi, "problem_ids": problem_ids}
    if not problem_ids:
        counts["reveal.no_truth_problem"] += 1
        return {**row, "outcome": "no_truth_problem"}, counts
    try:
        recommendation = evaluate(revealed_state(case, problem_ids)).recommendation
    except ValueError as error:  # docintel LeverError and IncompleteApplicationError are ValueErrors
        counts["reveal.failed"] += 1
        return {**row, "outcome": "failed", "error": type(error).__name__}, counts
    changed = recommendation != current
    group = "voi_positive" if top.voi > 0 else "voi_zero"
    for name in ("reveal.all", f"reveal.{group}"):
        counts[f"{name}.revealed"] += 1
        counts[f"{name}.changed"] += int(changed)
    return {**row, "outcome": "revealed", "recommendation_after": recommendation, "changed": changed}, counts
