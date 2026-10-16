"""Tests for the founder checks (round-trip audit and bound check) and their mutation test on founder narratives.

The narratives are rendered from SYNTHETIC golden scenarios with the template; the mutations come from
provenance.mutations, seeded.
"""

import pytest

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.provenance.renderer import RenderedMemo, round_trip_audit
from uwcopilot.studio.narrative import ids, render_narrative
from uwcopilot.studio.narrative.checker import (
    bound_check,
    evidence_text,
    founder_sentences,
    marker_map,
    score_scenario,
    trace_count,
    with_claim_citations,
)
from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.narrative.models import NarrativeResult

N_PATHS = 200


@pytest.fixture(scope="module")
def scenario() -> GoldenScenario:
    """A SYNTHETIC retail plan in the SYNTHETIC metro (no path search: the checks do not need one)."""
    return build_golden_scenario("synthetic_metro", "retail_storefront", n_paths=N_PATHS, with_path=False)


@pytest.fixture(scope="module")
def ledger(scenario: GoldenScenario) -> Ledger:
    """The scenario's founder ledger."""
    return scenario.ledger()


@pytest.fixture(scope="module")
def narrative(ledger: Ledger) -> NarrativeResult:
    """The template narrative."""
    return render_narrative(ledger)


def sentence_with(body: str, fragment: str) -> str:
    """The first sentence of the body that contains the fragment."""
    for sentence in body.split(". "):
        if fragment in sentence:
            return sentence + "."
    raise AssertionError(fragment)


def test_evidence_text_puts_the_threshold_comparator_first(ledger: Ledger) -> None:
    assert evidence_text(ledger.claim(ids.PROGRAM_MIN_DSCR)) == "minimum 1.25"  # type: ignore[arg-type]
    assert evidence_text(ledger.claim(ids.TARGET_P_CASH)).startswith("at most ")  # type: ignore[arg-type]
    loan = ledger.claim(ids.FOUNDER_LOAN)
    assert evidence_text(loan).startswith("$")  # type: ignore[arg-type]


def test_markers_become_claim_citations(narrative: NarrativeResult) -> None:
    markers = marker_map(narrative.claims_used)
    assert markers["1"] == narrative.claims_used[0]
    assert f"[{narrative.claims_used[0]}]" in with_claim_citations(narrative.body, markers)


def test_the_rendered_narrative_traces_by_both_checks(ledger: Ledger, narrative: NarrativeResult) -> None:
    counts = trace_count(narrative.body, ledger, narrative.claims_used)
    assert counts.numbers > 20
    assert counts.untraced_round_trip == 0 and counts.untraced_bound == 0


def test_a_value_moved_to_another_sentence_is_caught_only_by_the_bound_check(
    ledger: Ledger, narrative: NarrativeResult
) -> None:
    loan_sentence = sentence_with(narrative.body, "startup term loan of")
    equity = ledger.claim(ids.FOUNDER_EQUITY)
    loan = ledger.claim(ids.FOUNDER_LOAN)
    assert equity is not None and loan is not None
    moved = loan_sentence.replace(f"${loan.value:,.0f}", f"${equity.value:,.0f}")
    assert moved != loan_sentence
    assert round_trip_audit(RenderedMemo(body=moved), ledger) == []  # the value is in the ledger ...
    assert bound_check(moved, ledger, narrative.claims_used).untraceable  # ... but not in this sentence's claims


def test_a_flipped_comparator_is_caught_by_the_bound_check(ledger: Ledger, narrative: NarrativeResult) -> None:
    sentence = sentence_with(narrative.body, "program's minimum of 1.25")
    flipped = sentence.replace("program's minimum of", "program's maximum of")
    assert round_trip_audit(RenderedMemo(body=flipped), ledger) == []
    assert bound_check(flipped, ledger, narrative.claims_used).comparator_mismatches


def test_a_number_without_a_marker_is_untraceable(ledger: Ledger, narrative: NarrativeResult) -> None:
    assert bound_check("The loan is $123,456.", ledger, narrative.claims_used).untraceable


def test_the_founder_sentence_set_keeps_only_sentences_with_numbers(ledger: Ledger, narrative: NarrativeResult) -> None:
    sentences = founder_sentences("S", narrative.body, ledger, narrative.claims_used)
    assert sentences.refs and all(ref.section_id in sentences.sections for ref in sentences.refs)
    assert all(sentences.sections[ref.section_id] for ref in sentences.refs)


def test_the_mutation_test_flags_mutations_and_never_the_controls(
    scenario: GoldenScenario, ledger: Ledger, narrative: NarrativeResult
) -> None:
    scores, mutations = score_scenario("S", narrative.body, ledger, narrative.claims_used, seed=scenario.seed)
    assert mutations and scores.n_sentences > 5
    for control in ("unmutated", "equivalent_rewrite"):
        assert scores.round_trip[control].flagged == 0, control
        assert scores.bound[control].flagged == 0, control
    for kind in ("value_perturb", "magnitude_x10", "cross_section"):
        assert scores.bound[kind].n > 0 and scores.bound[kind].flagged == scores.bound[kind].n, kind
    # The ledger-wide audit cannot see a value moved between sentences: it is in the ledger.
    assert scores.round_trip["cross_section"].flagged == 0
    assert "wrong_row" not in scores.bound  # founder narratives have no tables
    again, _ = score_scenario("S", narrative.body, ledger, narrative.claims_used, seed=scenario.seed)
    assert again == scores
