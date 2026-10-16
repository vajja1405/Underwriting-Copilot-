"""The committed SYNTHETIC startup examples (eval/startup_applications/), their generator and their evaluation."""

import json
import random
import re
from dataclasses import replace
from pathlib import Path

import pytest
from agent_helpers import APPLICATIONS_DIR

import eval_agent
import make_startup_applications
from uwcopilot.agent import policy_startup
from uwcopilot.agent.applications import STARTUP_BUSINESS_TYPES, StartupCreditApplication, load_applications
from uwcopilot.agent.evaluation import corpus_trigger_ids, evaluate_policy_case, summarize_policy
from uwcopilot.agent.policy import POLICY_VERSION
from uwcopilot.agent.policy_startup import STARTUP_POLICY_VERSION
from uwcopilot.agent.synthetic_startup import build_startup_application, scenario_spec
from uwcopilot.common.paths import REPO_ROOT

STARTUP_DIR = REPO_ROOT / "eval" / "startup_applications"


def startup_cases() -> list:
    """The reference policy on every committed startup example."""
    return [evaluate_policy_case(application) for application in load_applications(STARTUP_DIR)]


def test_committed_examples_are_reproduced_byte_for_byte(tmp_path: Path) -> None:
    assert make_startup_applications.main(["--out-dir", str(tmp_path)]) == 0
    committed = sorted(path.name for path in STARTUP_DIR.iterdir())
    assert sorted(path.name for path in tmp_path.iterdir()) == committed
    for name in committed:
        assert (tmp_path / name).read_bytes() == (STARTUP_DIR / name).read_bytes(), name


def test_examples_are_synthetic_cover_every_business_type_and_match_the_manifest() -> None:
    applications = load_applications(STARTUP_DIR)
    count = len(applications)
    assert [application.application_id for application in applications] == [
        f"APP-S-{n:03d}" for n in range(1, count + 1)
    ]
    assert all(isinstance(application, StartupCreditApplication) for application in applications)
    assert all(application.data_label == "SYNTHETIC" for application in applications)
    assert {application.applicant.business_type for application in applications} == set(STARTUP_BUSINESS_TYPES)
    assert all(application.projections.simulation is None for application in applications)
    manifest = json.loads((STARTUP_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_label"] == "SYNTHETIC" and manifest["count"] == count
    assert sum(manifest["business_types"].values()) == count
    labels = [application.ground_truth.expected_recommendation for application in applications]
    assert manifest["expected_recommendations"] == {
        name: labels.count(name) for name in manifest["expected_recommendations"]
    }
    assert manifest["contains_injection"] == sum(
        application.ground_truth.contains_injection for application in applications
    )


def test_reference_policy_agrees_with_every_designed_label() -> None:
    cases = startup_cases()
    assert [(case.scenario, case.recommendation_match, case.rules_match) for case in cases] == [
        (case.scenario, True, True) for case in cases
    ]


def test_the_examples_fire_every_rule_the_startup_policy_can_emit() -> None:
    # Like the 65 Northwind and Harborline files, the eval set has a case for every rule (not only the unit tests).
    source = Path(policy_startup.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'context\.find\(\s*"([a-z0-9_]+)"', source))
    fired = {rule for case in startup_cases() for rule in case.predicted_rules}
    assert len(emitted) == 30 and emitted - fired == set()
    assert "embedded_instruction" in fired


def test_examples_are_internally_consistent() -> None:
    for application in load_applications(STARTUP_DIR):
        uses = application.sources_and_uses.uses
        site = application.applicant.site_description
        pledged_site = [item for item in application.collateral if item.collateral_type == "owner_occupied_cre"]
        assert site.startswith("Purchased" if uses.real_estate > 0 else "Leased"), application.application_id
        assert sum(item.value for item in pledged_site) <= uses.real_estate
        pledged_equipment = [item for item in application.collateral if item.collateral_type.endswith("equipment")]
        assert sum(item.value for item in pledged_equipment) <= uses.equipment
        rent = {"monthly_rent", "monthly_lease"} & {item.name for item in application.material_assumptions}
        assert not (rent and pledged_site), application.application_id
        evidenced = any(owner.experience_evidence_provided for owner in application.owners)
        assert evidenced == ("owner_resumes_and_references" in application.documents_provided)
    sites = {
        application.applicant.business_type: application.applicant.site_description
        for application in load_applications(STARTUP_DIR)
    }
    assert len(set(sites.values())) == len(STARTUP_BUSINESS_TYPES)


@pytest.mark.parametrize(
    ("collateral", "message"),
    [
        ((("owner_occupied_cre", 0.5),), "pays rent"),
        ((("accounts_receivable", 0.5),), "no accounts_receivable"),
        ((("new_equipment", 1.5),), "larger than the project"),
    ],
)
def test_the_generator_refuses_collateral_the_project_does_not_buy(
    collateral: tuple[tuple[str, float], ...], message: str
) -> None:
    spec = replace(scenario_spec("clean_car_wash"), collateral=collateral)
    with pytest.raises(ValueError, match=message):
        build_startup_application(spec, "APP-S-900", random.Random(7))


# --------------------------------------------------------------------------------------------------------------
# The evaluation on the startup set (scripts/eval_agent.py with --applications-dir eval/startup_applications)
# --------------------------------------------------------------------------------------------------------------


def test_trigger_coverage_counts_the_startup_program_triggers_for_startup_files() -> None:
    startup = corpus_trigger_ids(("startup_credit",))
    assert {f"R-NSP-{number:02d}" for number in range(1, 11)} <= set(startup)
    assert {"R-NBC-08", "R-NBC-10", "R-NBC-13"} <= set(startup)
    assert not any(trigger.startswith("R-HM-") for trigger in startup)
    assert "R-NBC-04" not in startup  # replaced by R-NSP-06 for startup files
    summary = summarize_policy(startup_cases(), ("startup_credit",))
    coverage = summary["referral_trigger_coverage"]
    assert {f"R-NSP-{number:02d}" for number in range(1, 11)} <= set(coverage["exercised"])
    assert len(corpus_trigger_ids()) == 31  # the Phase 5 metric is unchanged


def test_eval_agent_labels_the_startup_set_with_its_own_line_and_policy_version() -> None:
    startup = load_applications(STARTUP_DIR)
    northwind = load_applications(APPLICATIONS_DIR)
    assert eval_agent.application_lines(startup) == ("startup_credit",)
    assert eval_agent.application_lines(northwind) == ("credit", "insurance")
    assert eval_agent.application_lines(northwind[:3] + startup[:2]) == ("credit", "insurance", "startup_credit")
    assert eval_agent.startup_count(startup) == {"startup_credit": len(startup)}
    assert eval_agent.startup_count(northwind) == {}
    assert eval_agent.policy_version_for(("startup_credit",)) == STARTUP_POLICY_VERSION
    assert eval_agent.policy_version_for(("credit", "insurance")) == POLICY_VERSION
    assert eval_agent.policy_version_for(("credit", "startup_credit")) == f"{POLICY_VERSION}; {STARTUP_POLICY_VERSION}"
