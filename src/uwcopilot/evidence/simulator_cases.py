"""Simulator cases (innovation S5): one SYNTHETIC packet truth prepared for the evidence-request simulator.

This is EVALUATION code. It reads the generator's hidden ground truth (docintel/truth.py), which the
Evidence-to-Action Engine itself never sees: no engine module (state, open_items, voi, engine, ...) imports this
module or the truth models.

What a simulator case holds:
- the engine's starting point, built exactly as for a real packet but from the PERFECT (oracle) extraction:
  truth.oracle_extraction -> docintel.checks.run_checks -> docintel.contradictions.build_contradictions ->
  evidence.state.from_packet. The page text a perfect reader would see (truth.pages) is scanned for embedded
  instructions with docintel.injection_scan.scan_lines and passed to the checks as page hits, as the Phase 2
  pipeline does with OCR text. No PDF is drawn (JSON-only cases), so extraction accuracy is NOT part of any
  simulator result;
- the hidden truth: every planted problem with its latent (true) resolution.

Revealing: when the simulator "requests" an open item, the planted problems that item stands for (matching_problems)
are revealed by applying their latent_substitutions to the starting state. The state after any set of reveals is
always rebuilt from the starting state in ONE canonical order (revealed_state): problem-id order, except that
problems whose substitutions insert loss-run years go last, because an inserted year shifts the year indexes other
substitutions use. That is the order the generator uses for application_latent, so revealing every problem gives the
full-information state (full_information_state) whatever order the requests came in.

Matching an open item to planted problems (matching_problems), by the item's problem type:
- revenue_gap, name/fein/owner mismatch, balance_discontinuity, debt_payment_mismatch, prompt_injection,
  tiv_mismatch, claims_history_mismatch: every planted problem of the same type (a packet has at most one);
- missing_pages: the planted missing month with the same month;
- missing_document: planted missing documents whose checklist code the item covers;
- missing_loss_run: planted missing loss runs (resolving request missing_loss_run) of the same carrier;
- loss_run_total_mismatch: the planted problem on the same loss-run document;
- paid_reserve_mismatch: the planted problem on the same claim line (application path).
An open item that matches no planted problem (a check that fired without a planted cause) reveals nothing: the
full-information state keeps it as documented, so requesting it never moves the case toward or away from the
full-information recommendation. A planted problem that no open item matches is "undetected": no request can reveal
it, so the full-information recommendation may be out of every policy's reach (counted, never hidden).

Everything here is SYNTHETIC: Northwind Business Credit and Harborline Mutual are fictional institutions.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.contradictions import build_contradictions
from uwcopilot.docintel.injection_scan import DocumentInjectionHit, ScanLine, scan_lines
from uwcopilot.docintel.schemas import PacketExtraction, parse_extraction
from uwcopilot.docintel.truth import InjectedProblem, PacketTruth
from uwcopilot.evidence.open_item_models import OpenItem, PacketContext, packet_context
from uwcopilot.evidence.state import CaseState, apply, from_packet

YEAR_INSERT_LEVER = "+loss_runs.years"
DOCUMENT_CLAIM_PREFIX = "DOC-"
# Item problem types matched to planted problems of the same type (a packet has at most one of each).
SAME_TYPE_PROBLEMS: frozenset[str] = frozenset(
    {
        "revenue_gap",
        "name_mismatch",
        "fein_mismatch",
        "owner_mismatch",
        "balance_discontinuity",
        "debt_payment_mismatch",
        "prompt_injection",
        "tiv_mismatch",
        "claims_history_mismatch",
    }
)


@dataclass(frozen=True)
class SimCase:
    """One SYNTHETIC case as the simulator uses it: the hidden truth and the engine's starting point."""

    truth: PacketTruth
    extraction: PacketExtraction
    context: PacketContext
    initial: CaseState

    @property
    def case_id(self) -> str:
        """The packet id (PKT-C-001, ...)."""
        return self.truth.packet_id

    @property
    def problems(self) -> list[InjectedProblem]:
        """The planted problems, in problem-id order."""
        return list(self.truth.problems)


# --------------------------------------------------------------------------------------------------------------
# Preparing a case
# --------------------------------------------------------------------------------------------------------------


def page_lines(truth: PacketTruth) -> list[ScanLine]:
    """Every printed line of the packet as (doc_id, page, 1-based line number, text): the oracle page text."""
    lines = []
    for page in truth.pages:
        for number, text in enumerate(page.text_lines, start=1):
            lines.append(ScanLine(page.doc_id, page.page_number, number, text))
    return lines


def page_hits(truth: PacketTruth) -> list[DocumentInjectionHit]:
    """Embedded-instruction hits on the oracle page text (the same scanner the Phase 2 pipeline runs on OCR text)."""
    return scan_lines(page_lines(truth))


def prepare_case(truth: PacketTruth) -> SimCase:
    """The engine's starting point for a truth: oracle extraction -> checks -> contradictions -> case state."""
    extraction = parse_extraction(truth.oracle_extraction)
    hits = page_hits(truth)
    checks = run_checks(extraction, hits)
    report = build_contradictions(extraction, checks)
    state = from_packet(extraction, checks, report, hits, case_id=truth.packet_id)
    return SimCase(
        truth=truth,
        extraction=extraction,
        context=packet_context(extraction, checks, report),
        initial=state,
    )


# --------------------------------------------------------------------------------------------------------------
# Revealing planted problems
# --------------------------------------------------------------------------------------------------------------


def inserts_years(problem: InjectedProblem) -> bool:
    """True when the problem's latent substitutions insert loss-run years (they must be applied last)."""
    return YEAR_INSERT_LEVER in problem.latent_substitutions


def canonical_order(problems: Iterable[InjectedProblem]) -> list[InjectedProblem]:
    """Problem-id order, with year-inserting problems last (the generator's application_latent order)."""
    by_id = sorted(problems, key=lambda problem: problem.problem_id)
    return [problem for problem in by_id if not inserts_years(problem)] + [
        problem for problem in by_id if inserts_years(problem)
    ]


def revealed_state(case: SimCase, problem_ids: Collection[str]) -> CaseState:
    """The starting state with the latent substitutions of the given planted problems applied (canonical order)."""
    state = case.initial
    chosen = [problem for problem in case.problems if problem.problem_id in problem_ids]
    for problem in canonical_order(chosen):
        state = apply(state, problem.latent_substitutions)
    return state


def full_information_state(case: SimCase) -> CaseState:
    """The state with EVERY planted problem revealed: the full-information state."""
    return revealed_state(case, [problem.problem_id for problem in case.problems])


# --------------------------------------------------------------------------------------------------------------
# Matching open items to planted problems
# --------------------------------------------------------------------------------------------------------------


def item_document_codes(item: OpenItem) -> set[str]:
    """The checklist codes a missing_document item covers (its DOC-<code> claim ids)."""
    return {
        claim_id[len(DOCUMENT_CLAIM_PREFIX) :]
        for claim_id in item.claim_ids
        if claim_id.startswith(DOCUMENT_CLAIM_PREFIX)
    }


def option_paths(problem: InjectedProblem) -> set[str]:
    """Every lever name used by the problem's resolution options and latent substitutions."""
    paths = set(problem.latent_substitutions)
    for option in problem.resolution_options:
        paths.update(option.substitutions)
    return paths


def matches(item: OpenItem, problem: InjectedProblem) -> bool:
    """True when the open item stands for this planted problem (rules in the module docstring)."""
    kind = item.problem_type
    if kind in SAME_TYPE_PROBLEMS:
        return problem.problem_type == kind
    if kind == "missing_pages":
        month = item.request_params.get("month")
        return problem.problem_type == "missing_pages" and problem.resolving_request_params.get("month") == month
    if kind == "missing_document":
        return (
            problem.problem_type == "missing_document"
            and problem.resolving_request == "missing_document"
            and problem.resolving_request_params.get("document") in item_document_codes(item)
        )
    if kind == "missing_loss_run":
        if problem.resolving_request != "missing_loss_run":
            return False
        carrier = item.request_params.get("carrier")
        return carrier is None or problem.resolving_request_params.get("carrier") == carrier
    if kind == "loss_run_total_mismatch":
        if problem.problem_type != kind:
            return False
        return not item.doc_ids or bool(set(item.doc_ids) & set(problem.doc_ids))
    if kind == "paid_reserve_mismatch":
        path = item.placeholders.get("claim_path")
        if problem.problem_type != kind or not path:
            return False
        return any(lever.startswith(f"{path}.") for lever in option_paths(problem))
    return False


def matching_problems(item: OpenItem, truth: PacketTruth) -> list[str]:
    """The ids of the planted problems an open item stands for (often one, sometimes none)."""
    return [problem.problem_id for problem in truth.problems if matches(item, problem)]


def latent_option_for(item: OpenItem, truth: PacketTruth) -> str | None:
    """The latent (true) template option of the first planted problem the item stands for (None when unmatched)."""
    for problem in truth.problems:
        if matches(item, problem):
            return problem.latent_option_id
    return None


def undetected_problems(truth: PacketTruth, items: Iterable[OpenItem]) -> list[str]:
    """Planted problems that no open item matches (no request can reveal them)."""
    matched: set[str] = set()
    for item in items:
        matched.update(matching_problems(item, truth))
    return [problem.problem_id for problem in truth.problems if problem.problem_id not in matched]
