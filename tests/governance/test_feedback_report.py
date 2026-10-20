"""Tests for scripts/feedback_report.py: the SIMULATED reviewer and the two results sections (offline)."""

import json
import random
from pathlib import Path

import feedback_report
from feedback_report import Draws, SimulationParameters, draw, simulated_choice
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.governance.feedback import override_reason_codes

PARAMS = SimulationParameters()
REASONS = ("data_error_in_model_input", "policy_exception_approved")


def review(
    recommendation: str = "approve", required: str = "underwriter", allowed: tuple[str, ...] | None = None
) -> dict:
    """The parts of a review request the simulated reviewer reads."""
    actions = allowed if allowed is not None else ("approve", "decline", "refer", "request_more_information")
    return {"reference_recommendation": recommendation, "required_reviewer_role": required, "allowed_actions": actions}


def draws(**values: float) -> Draws:
    """Draws that accept the recommendation as an underwriter unless changed."""
    base = {"role": 0.99, "override": 0.99, "pick": 0.0, "second_reason": 0.99, "reason_text": 0.99}
    return Draws(**(base | values), reasons=REASONS)


def test_an_accept_takes_the_matching_action() -> None:
    choice = simulated_choice(review("decline_for_review", required="senior_underwriter"), draws(), PARAMS)
    assert choice["action"] == "decline" and choice["role"] == "senior_underwriter"
    assert "reason_codes" not in choice and choice["reviewer_id"] == "SIMULATED-senior_underwriter"
    plain = simulated_choice(review(), draws(), PARAMS)
    assert plain["action"] == "approve" and plain["role"] == "underwriter"
    assert simulated_choice(review(), draws(role=0.1), PARAMS)["role"] == "senior_underwriter"


def test_an_override_gives_reason_codes_and_sometimes_a_note() -> None:
    choice = simulated_choice(review("refer"), draws(override=0.01, pick=0.9), PARAMS)
    assert choice["action"] == "decline" and choice["reason_codes"] == [REASONS[0]]
    assert "reason_text" not in choice
    two = simulated_choice(review("refer"), draws(override=0.01, second_reason=0.1, reason_text=0.1), PARAMS)
    assert two["action"] == "approve" and two["reason_codes"] == list(REASONS) and two["reason_text"]


def test_no_override_when_the_checkpoint_allows_none() -> None:
    blocked = review("refer", allowed=("refer", "request_more_information"))
    choice = simulated_choice(blocked, draws(override=0.01), PARAMS)
    assert choice["action"] == "refer" and "reason_codes" not in choice


def test_draws_are_reproducible_and_always_the_same_count() -> None:
    codes = override_reason_codes()
    first = [draw(random.Random(42), codes) for _ in range(3)]
    again = [draw(random.Random(42), codes) for _ in range(3)]
    assert first == again
    rng = random.Random(3)
    draw(rng, codes)
    reference = random.Random(3)
    reference.sample(codes, 2)
    for _ in range(5):
        reference.random()
    assert rng.random() == reference.random()  # one sample of two codes plus five uniforms, whatever the branch


def run(tmp_path: Path, name: str, limit: int = 5) -> tuple[dict, list[dict]]:
    """Run the script on the first applications with outputs in tmp_path."""
    log = tmp_path / f"{name}.jsonl"
    output = tmp_path / f"{name}.json"
    assert (
        feedback_report.main(["--limit", str(limit), "--log", str(log), "--output", str(output), "--no-data-files"])
        == 0
    )
    return json.loads(output.read_text(encoding="utf-8")), DecisionLog(log).records()


def test_the_script_writes_both_sections_from_a_simulated_log(tmp_path: Path) -> None:
    results, records = run(tmp_path, "first")
    assert set(results) == {"lineage", "feedback"}
    feedback = results["feedback"]["results"]
    assert "SIMULATED" in feedback["data_label"] and "not real reviewers" in feedback["interpretation"]
    assert feedback["applications"]["count"] == 5 and feedback["decision_log"]["chain_problems"] == 0
    assert feedback["summary"]["decisions"] == 5 and feedback["summary"]["by_reviewer_label"] == {"SIMULATED": 5}
    assert feedback["summary"]["override_rate"]["n_resamples"] == 1000
    assert len(records) == 5 and all(record["reviewer_label"] == "SIMULATED" for record in records)
    assert all(record["lineage"]["lineage_version"] == "lineage-v1" for record in records)
    lineage = results["lineage"]["results"]
    assert lineage["checks"]["records_with_lineage"] == 5 and lineage["checks"]["stale_items"] == {}
    assert results["lineage"]["metadata"]["git_commit"]  # stamped by update_results


def test_the_same_seed_gives_the_same_decisions(tmp_path: Path) -> None:
    _, first = run(tmp_path, "a", limit=8)
    _, second = run(tmp_path, "b", limit=8)
    summary = [(record["application_id"], record["reviewer"]["action"], record["reviewer"]["role"]) for record in first]
    assert summary == [
        (record["application_id"], record["reviewer"]["action"], record["reviewer"]["role"]) for record in second
    ]
