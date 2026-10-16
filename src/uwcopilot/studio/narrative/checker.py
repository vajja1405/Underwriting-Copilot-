"""Checking the numbers of a rendered founder narrative, and the mutation test that measures the checks.

Two checks of a rendered narrative (provenance.renderer output: every number followed by a footnote marker [^cN]):
1. the round-trip audit (provenance.renderer.round_trip_audit): every number in the body must equal SOME claim of the
   ledger in its display format. It is ledger-wide: a number moved to the wrong place still equals some claim.
2. the bound check (bound_check below): every number must equal a claim that its OWN sentence points to through a
   footnote marker, and a comparator next to a threshold must match the threshold's own comparator. The markers are
   turned into citations of the claim ids ([^c3] -> [SIM.y2.noi_p50]) and provenance.numeric_check.check_numbers
   compares each number with the cited claims' evidence text: the value as the renderer prints it, prefixed by the
   threshold's comparator for program and target thresholds ("minimum 1.25", "at least 80.0%"). A number in a
   sentence with no marker is untraceable.

The mutation test (score_scenario) reuses provenance.mutations on a FOUNDER sentence set: every sentence of a rendered
golden narrative that holds a number becomes one reference sentence, whose "section" is the evidence text of the
claims its markers point to. build_mutations then breaks one number (value_perturb, magnitude_x10, unit_swap,
comparator_flip, cross_section with a donor from another sentence of the same narrative) or keeps it (unmutated,
equivalent_rewrite). wrong_row does not apply: a founder narrative has no tables. Both checks are scored on the same
mutations: recall per mutation type (share flagged) and the false-flag rate on the controls.
"""

import re
from dataclasses import dataclass

from uwcopilot.evidence.ledger_models import Claim, Ledger
from uwcopilot.provenance.evaluation import Flagger, TypeScore, score_by_type
from uwcopilot.provenance.mutations import Mutation, build_mutations
from uwcopilot.provenance.numbers import extract_numbers
from uwcopilot.provenance.numeric_check import NumericCheck, check_numbers, split_sentences
from uwcopilot.provenance.reference import ReferenceSentence
from uwcopilot.provenance.renderer import FOOTNOTE_LINE, FOOTNOTE_MARKER, RenderedMemo, format_value, round_trip_audit

MARKER = re.compile(r"\[\^c(?P<number>\d+)\]")


def evidence_text(claim: Claim) -> str:
    """What a claim states, as the bound check reads it: its printed value, after its comparator when it has one."""
    shown = format_value(claim.value, claim.display)
    comparator = claim.provenance.details.get("comparator")
    return f"{comparator} {shown}" if comparator else shown


def marker_map(claims_used: list[str]) -> dict[str, str]:
    """Footnote number -> claim id: the renderer numbers claims in the order they are first used (c1, c2, ...)."""
    return {str(number): claim_id for number, claim_id in enumerate(claims_used, start=1)}


def with_claim_citations(text: str, markers: dict[str, str]) -> str:
    """The text with every [^cN] marker replaced by a citation of its claim id ([SIM.y2.noi_p50])."""
    return MARKER.sub(lambda match: f" [{markers.get(match.group('number'), 'unknown-marker')}]", text)


def bound_check(text: str, ledger: Ledger, claims_used: list[str]) -> NumericCheck:
    """Check every number of a rendered text against the claims its own sentence's markers point to."""
    markers = marker_map(claims_used)
    sources = {}
    for claim_id in markers.values():
        claim = ledger.claim(claim_id)
        if claim is not None:
            sources[claim_id] = evidence_text(claim)
    return check_numbers(with_claim_citations(text, markers), sources)


def body_of(text: str) -> str:
    """A rendered narrative without its footnote lines (they hold hashes and references, not claims)."""
    return FOOTNOTE_LINE.sub(" ", text)


def count_numbers(body: str) -> int:
    """How many numbers the reader sees in a rendered body (footnote markers removed first)."""
    return len(extract_numbers(FOOTNOTE_MARKER.sub(" ", body)))


@dataclass(frozen=True)
class TraceCount:
    """Numbers in a rendered text and how many each check could not trace."""

    numbers: int
    untraced_round_trip: int
    untraced_bound: int


def trace_count(body: str, ledger: Ledger, claims_used: list[str]) -> TraceCount:
    """Count the numbers of a rendered body and the ones that do not trace, by each check."""
    audit = round_trip_audit(RenderedMemo(body=body), ledger)
    bound = bound_check(body, ledger, claims_used)
    untraced_bound = len(bound.untraceable) + len(bound.comparator_mismatches)
    return TraceCount(count_numbers(body), len(audit), untraced_bound)


# --------------------------------------------------------------------------------------------------------------
# The founder sentence set and the mutation test
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FounderSentences:
    """One narrative's reference sentences and the evidence text of each sentence's claims."""

    scenario_id: str
    refs: list[ReferenceSentence]
    sections: dict[str, str]


def founder_sentences(scenario_id: str, body: str, ledger: Ledger, claims_used: list[str]) -> FounderSentences:
    """Every sentence of a rendered body that holds a number, with the evidence text of the claims it points to."""
    markers = marker_map(claims_used)
    refs: list[ReferenceSentence] = []
    sections: dict[str, str] = {}
    for _, sentence in split_sentences(body):
        sentence = sentence.strip()
        if not extract_numbers(sentence):
            continue
        lines = []
        for match in MARKER.finditer(sentence):
            claim = ledger.claim(markers.get(match.group("number"), ""))
            if claim is not None:
                lines.append(evidence_text(claim))
        ref_id = f"{scenario_id}-S{len(refs) + 1:02d}"
        refs.append(ReferenceSentence(ref_id, ref_id, "prose", sentence))
        sections[ref_id] = "\n".join(lines)
    return FounderSentences(scenario_id, refs, sections)


def round_trip_flags(ledger: Ledger) -> Flagger:
    """A flagger for score_by_type: the round-trip audit of the mutated sentence finds an untraced number."""

    def flags(mutation: Mutation, sections: dict[str, str]) -> bool:
        return bool(round_trip_audit(RenderedMemo(body=mutation.mutated), ledger))

    return flags


def bound_flags(ledger: Ledger, claims_used: list[str]) -> Flagger:
    """A flagger for score_by_type: the bound check flags the mutated sentence (untraceable or comparator)."""

    def flags(mutation: Mutation, sections: dict[str, str]) -> bool:
        return bound_check(mutation.mutated, ledger, claims_used).flagged

    return flags


@dataclass(frozen=True)
class ScenarioScores:
    """Flagged counts per mutation type for one narrative, by each check."""

    scenario_id: str
    n_sentences: int
    round_trip: dict[str, TypeScore]
    bound: dict[str, TypeScore]


def score_scenario(
    scenario_id: str, body: str, ledger: Ledger, claims_used: list[str], seed: int
) -> tuple[ScenarioScores, list[Mutation]]:
    """Build the narrative's mutations (seeded) and score both checks on them."""
    sentences = founder_sentences(scenario_id, body, ledger, claims_used)
    mutations = build_mutations(sentences.refs, sentences.sections, seed=seed)
    scores = ScenarioScores(
        scenario_id=scenario_id,
        n_sentences=len(sentences.refs),
        round_trip=score_by_type(mutations, sentences.sections, round_trip_flags(ledger)),
        bound=score_by_type(mutations, sentences.sections, bound_flags(ledger, claims_used)),
    )
    return scores, mutations
