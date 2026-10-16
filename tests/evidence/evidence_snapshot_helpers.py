"""Policy-output snapshot for the document_checks regression test (tests/evidence/test_policy_integration.py).

The fixture fixtures/policy_snapshot_before_document_checks.json.gz was recorded BEFORE PolicyInputs gained the
document_checks field (Phase 4). It holds, for every committed SYNTHETIC application (the 65 files in
eval/applications/ and the startup examples in eval/startup_applications/):
- inputs_sha256: the hash of the application inputs, so a regenerated application is reported as such;
- outputs: the canonical JSON (sorted keys, no spaces) of evaluate_policy's PolicyResult under three input
  variants, built exactly as tests/agent/test_startup_regression.py builds them:
  "designed" (designed screening, the real injection detector, no risk score), "all_clear_with_risk" (every party
  clear and a fixed risk score) and "empty_inputs" (PolicyInputs(), every party unscreened).

Re-record only on purpose (the point of the file is that it does not change):
    uv run python -c "import sys; sys.path.insert(0, 'tests/evidence'); \
        import evidence_snapshot_helpers as h; h.write_snapshot(h.SNAPSHOT_PATH)"
"""

import gzip
import json
from pathlib import Path

from uwcopilot.agent.applications import (
    AnyApplication,
    canonical_json,
    inputs_sha256,
    load_applications,
    screened_parties,
)
from uwcopilot.agent.evaluation import ground_truth_screening
from uwcopilot.agent.injection import screen_application
from uwcopilot.agent.policy import InjectionFlag, PolicyInputs, RiskSignal, ScreeningOutcome, evaluate_policy
from uwcopilot.common.paths import REPO_ROOT

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "policy_snapshot_before_document_checks.json.gz"
APPLICATION_DIRS: tuple[Path, ...] = (
    REPO_ROOT / "eval" / "applications",
    REPO_ROOT / "eval" / "startup_applications",
)


def committed_applications() -> list[AnyApplication]:
    """Every committed SYNTHETIC application: eval/applications/ then eval/startup_applications/."""
    applications: list[AnyApplication] = []
    for directory in APPLICATION_DIRS:
        applications.extend(load_applications(directory))
    return applications


def variant_inputs(application: AnyApplication) -> dict[str, PolicyInputs]:
    """The three PolicyInputs variants of the snapshot (same construction as the startup regression test)."""
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


def policy_outputs(application: AnyApplication) -> dict[str, str]:
    """Canonical JSON of evaluate_policy's result for each input variant."""
    return {
        name: canonical_json(evaluate_policy(application, inputs).model_dump(mode="json"))
        for name, inputs in variant_inputs(application).items()
    }


def build_snapshot() -> dict[str, dict]:
    """{application_id: {"inputs_sha256": ..., "outputs": {variant: canonical json}}} for every application."""
    return {
        application.application_id: {
            "inputs_sha256": inputs_sha256(application),
            "outputs": policy_outputs(application),
        }
        for application in committed_applications()
    }


def write_snapshot(path: Path) -> None:
    """Write the snapshot as gzip-compressed canonical JSON (mtime 0, so the bytes are reproducible)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(build_snapshot())
    path.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, dict]:
    """The recorded snapshot."""
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
