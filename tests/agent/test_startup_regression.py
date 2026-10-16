"""Regression guard: adding the startup line must not change the policy output for any existing application.

The fixture tests/agent/fixtures/startup_regression_policy_snapshot.json.gz was recorded BEFORE the startup program
was added to the code: the canonical JSON (sorted keys, no spaces) of evaluate_policy's PolicyResult for each of the
65 committed eval/applications files, under three input variants:
- "designed": the evaluation's designed screening, the real injection detector and no risk score;
- "all_clear_with_risk": every party screened clear and a fixed risk score (exercises the NBC-8.1 path);
- "empty_inputs": PolicyInputs() (every party unscreened).
Every output must still be byte-identical.
"""

import gzip
import json
from pathlib import Path

from agent_helpers import APPLICATIONS_DIR

from uwcopilot.agent.applications import AnyApplication, canonical_json, load_applications, screened_parties
from uwcopilot.agent.evaluation import ground_truth_screening
from uwcopilot.agent.injection import screen_application
from uwcopilot.agent.policy import InjectionFlag, PolicyInputs, RiskSignal, ScreeningOutcome, evaluate_policy

SNAPSHOT = Path(__file__).parent / "fixtures" / "startup_regression_policy_snapshot.json.gz"


def variant_inputs(application: AnyApplication) -> dict[str, PolicyInputs]:
    """The three input variants the snapshot was recorded with (same construction, same order)."""
    hits = screen_application(application)
    designed = PolicyInputs(
        screening=ground_truth_screening(application),
        risk=None,
        risk_unavailable_reason="not used in the policy agreement check",
        injection_flags=[InjectionFlag(field=hit.field, patterns=hit.patterns) for hit in hits],
    )
    clear = PolicyInputs(
        screening=[
            ScreeningOutcome(
                party_id=party.party_id, status="clear", result_id=f"TR-{index + 1}", top_score=0.5, candidate_count=1
            )
            for index, party in enumerate(screened_parties(application))
        ],
        risk=RiskSignal(
            result_id="TR-9",
            product="credit",
            prediction=0.15,
            risk_band="E",
            model_version="m1",
            unknown_features=["x"],
        ),
    )
    return {"designed": designed, "all_clear_with_risk": clear, "empty_inputs": PolicyInputs()}


def test_policy_output_for_the_65_eval_applications_is_byte_identical() -> None:
    snapshot = json.loads(gzip.decompress(SNAPSHOT.read_bytes()).decode("utf-8"))
    applications = load_applications(APPLICATIONS_DIR)
    assert len(applications) == 65 and sorted(snapshot) == [application.application_id for application in applications]
    for application in applications:
        for name, inputs in variant_inputs(application).items():
            current = canonical_json(evaluate_policy(application, inputs).model_dump(mode="json"))
            assert current == canonical_json(snapshot[application.application_id][name]), (
                application.application_id,
                name,
            )
