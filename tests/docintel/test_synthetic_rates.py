"""Problem rates of the SYNTHETIC generator on 100 JSON-only packets per line stay near their documented targets."""

import math
from collections import Counter

import pytest

from uwcopilot.agent.injection import detect
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.synthetic.injections import CREDIT_INJECTIONS, INSURANCE_INJECTIONS
from uwcopilot.docintel.synthetic.problems import CREDIT_RATES, INSURANCE_RATES
from uwcopilot.docintel.truth import PacketTruth

PACKETS = 100
SEED = 7


def drawn_types(truth: PacketTruth) -> set[str]:
    """Problem types drawn for a packet (the CPA-financials missing_document problem follows from the amount)."""
    return {
        problem.problem_type
        for problem in truth.problems
        if problem.resolving_request_params.get("document") != "cpa_reviewed_financials"
    }


def observed(line: str) -> tuple[Counter, int]:
    """Drawn problem-type counts and the number of packets without drawn problems over PACKETS JSON-only packets."""
    letter = "C" if line == "credit" else "I"
    counts: Counter = Counter()
    clean = 0
    for number in range(1, PACKETS + 1):
        packet_id = f"PKT-{letter}-{number:03d}"
        _, truth = build_packet(packet_id, line, packet_seed(SEED, packet_id), "dev", draw=False)
        types = drawn_types(truth)
        counts.update(types)
        clean += not types
    return counts, clean


def tolerance(rate: float) -> float:
    """About 3.5 binomial standard deviations for 100 packets, never below 0.08."""
    return max(0.08, 3.5 * math.sqrt(rate * (1 - rate) / PACKETS))


@pytest.mark.parametrize("line,rates", [("credit", CREDIT_RATES), ("insurance", INSURANCE_RATES)])
def test_problem_rates_near_targets(line: str, rates: dict[str, float]) -> None:
    counts, clean = observed(line)
    for problem_type, rate in rates.items():
        share = counts[problem_type] / PACKETS
        assert abs(share - rate) <= tolerance(rate), f"{line} {problem_type}: {share:.2f} vs target {rate:.2f}"
    assert 0.15 <= clean / PACKETS <= 0.45
    assert counts["fein_mismatch"] == 0 or line == "credit"


def test_about_half_of_the_injection_techniques_evade_the_current_detector() -> None:
    texts = [
        text for catalog in (CREDIT_INJECTIONS, INSURANCE_INJECTIONS) for items in catalog.values() for text in items
    ]
    detected = sum(1 for text in texts if detect(text))
    assert 0.3 <= detected / len(texts) <= 0.7
