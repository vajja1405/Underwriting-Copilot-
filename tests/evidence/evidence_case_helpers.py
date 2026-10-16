"""SYNTHETIC cases for the evidence tests: committed applications and JSON-only generated packets.

Generated packets use the Phase 2 generator with draw=False (no PDF), packet ids PKT-*-401 to 440 and master seed
4040: outside the evaluation set (seed 42) and the simulator (seed 4242), so nothing here touches a test split.
The tests read the generator's oracle extraction (what a perfect reader of the documents would extract) and run
the real checks and contradiction builder on it. Ground truth is used only to choose which packet to test.
"""

from dataclasses import dataclass
from functools import cache

from uwcopilot.agent.applications import application_inputs, load_application
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.contradictions import ContradictionReport, build_contradictions
from uwcopilot.docintel.schemas import PacketExtraction, parse_extraction
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.truth import PacketTruth
from uwcopilot.evidence.state import CaseState, from_packet

APPLICATIONS_DIR = REPO_ROOT / "eval" / "applications"
STARTUP_DIR = REPO_ROOT / "eval" / "startup_applications"
CASE_SEED = 4040
CASE_NUMBERS = range(401, 441)
LINE_PREFIX = {"credit": "C", "insurance": "I"}


def application_dict(application_id: str) -> dict:
    """A committed SYNTHETIC application as JSON, without its ground truth."""
    directory = STARTUP_DIR if application_id.startswith("APP-S-") else APPLICATIONS_DIR
    return application_inputs(load_application(directory / f"{application_id}.json"))


@cache
def generated_truth(packet_id: str, line: str) -> PacketTruth:
    """The truth of one JSON-only generated packet (cached: the generator is deterministic)."""
    _, truth = build_packet(packet_id, line, packet_seed(CASE_SEED, packet_id), "dev", draw=False)
    return truth


@cache
def single_problem_packet(line: str, problem_type: str) -> PacketTruth:
    """The first generated packet whose only injected problem has this type (else the first with the type)."""
    with_type = []
    for number in CASE_NUMBERS:
        truth = generated_truth(f"PKT-{LINE_PREFIX[line]}-{number}", line)
        types = [problem.problem_type for problem in truth.problems]
        if problem_type in types:
            if len(types) == 1:
                return truth
            with_type.append(truth)
    if not with_type:
        raise LookupError(f"no generated {line} packet has a {problem_type} problem")
    return with_type[0]


@dataclass(frozen=True)
class OracleCase:
    """A generated packet run through the real checks on its oracle extraction."""

    truth: PacketTruth
    extraction: PacketExtraction
    checks: list[CheckResult]
    report: ContradictionReport
    state: CaseState


def oracle_case(truth: PacketTruth) -> OracleCase:
    """Checks, contradictions and the case state of a packet's oracle extraction."""
    extraction = parse_extraction(truth.oracle_extraction)
    checks = run_checks(extraction)
    report = build_contradictions(extraction, checks)
    state = from_packet(extraction, checks, report)
    return OracleCase(truth=truth, extraction=extraction, checks=checks, report=report, state=state)
