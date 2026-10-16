"""Startup open items and their ranking (evidence/open_items_startup.py, evidence/startup_requests.py, Studio S6).

Four problems of a startup file, each an UNKNOWN until its document is in the file: missing equity verification
(NSP-3.1), missing experience evidence (NSP-2.1), an unsupported top driver (NSP-6.1) and missing quotes for the
build-out. A flag counts only with its document listed. Requests and resolutions come from
config/studio/startup_evidence_requests.yaml; the lender catalog is untouched. Committed SYNTHETIC applications.
"""

import copy
import json
from typing import Any

import pytest

from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.evidence.open_items import PROBLEM_ORDER, detect_open_items
from uwcopilot.evidence.open_items_startup import STARTUP_PROBLEM_TYPES, driver_request
from uwcopilot.evidence.requests import catalog_version
from uwcopilot.evidence.startup_requests import (
    instantiate_startup_options,
    rank_startup_requests,
    startup_catalog,
    startup_catalog_version,
    startup_choices,
    startup_templates,
)
from uwcopilot.evidence.state import apply, evaluate, from_application

STARTUP_DIR = REPO_ROOT / "eval" / "startup_applications"


def application(number: str) -> dict[str, Any]:
    """A committed startup application as a dict (ground truth dropped by from_application)."""
    return json.loads((STARTUP_DIR / f"APP-S-{number}.json").read_text(encoding="utf-8"))


def items_of(data: dict[str, Any]) -> dict[str, Any]:
    """The open items of an application, by id."""
    state = from_application(data)
    return {item.item_id: item for item in detect_open_items(state, policy_result=evaluate(state))}


def without(data: dict[str, Any], *codes: str) -> dict[str, Any]:
    """The application with some documents removed from documents_provided."""
    changed = copy.deepcopy(data)
    changed["documents_provided"] = [code for code in changed["documents_provided"] if code not in codes]
    return changed


def test_the_startup_catalog_is_separate_and_complete() -> None:
    assert catalog_version() == "evidence-requests-v1"  # the lender catalog is unchanged
    assert startup_catalog_version() == "startup-evidence-requests-v1"
    resolved = {problem for request in startup_catalog().values() for problem in request.resolves}
    assert resolved == set(STARTUP_PROBLEM_TYPES) == set(startup_templates())
    assert all(request.cost_weight > 0 for request in startup_catalog().values())
    assert set(STARTUP_PROBLEM_TYPES) <= set(PROBLEM_ORDER)


def test_a_clean_startup_file_has_no_startup_items() -> None:
    assert not [item for item in items_of(application("001")) if item.split("-")[1] in STARTUP_PROBLEM_TYPES]


def test_equity_not_verified_is_an_unknown_with_the_statements_request() -> None:
    item = items_of(application("002"))["OI-missing_equity_verification"]
    assert item.status == "UNKNOWN" and item.request_id == "equity_bank_statements"
    choices = instantiate_startup_options(item.template_key, item.placeholders)
    assert [choice.option_id for choice in choices.options] == ["statements_confirm", "statements_show_less"]
    assert sum(choice.prior for choice in choices.options) == pytest.approx(1.0)


def test_a_verified_flag_without_its_statements_is_still_open() -> None:
    clean = application("001")
    assert "OI-missing_equity_verification" not in items_of(clean)
    assert "OI-missing_equity_verification" in items_of(without(clean, "equity_bank_statements"))


def test_missing_experience_evidence_follows_the_policy_finding() -> None:
    item = items_of(application("014"))["OI-missing_experience_evidence-O1"]
    assert item.request_id == "owner_resume_and_references" and item.request_params == {"owner": "O1"}
    clean = application("001")
    assert "OI-missing_experience_evidence-O1" in items_of(without(clean, "owner_resumes_and_references"))


def test_each_unbacked_top_driver_is_its_own_item() -> None:
    items = items_of(application("005"))
    assert set(items) >= {"OI-unsupported_material_assumption-r2"}
    assert items["OI-unsupported_material_assumption-r2"].request_params == {"driver": "2"}
    bare = without(application("001"), "lease_or_letter_of_intent", "contractor_or_equipment_quotes")
    driver_items = sorted(item for item in items_of(bare) if item.startswith("OI-unsupported_material_assumption"))
    assert driver_items == [f"OI-unsupported_material_assumption-r{rank}" for rank in (1, 2, 3)]


@pytest.mark.parametrize(
    ("name", "request_id"),
    [
        ("monthly_rent", "driver_lease_or_loi"),
        ("monthly_lease", "driver_lease_or_loi"),
        ("capex_overrun_share", "driver_quote"),
        ("cars_per_day", "driver_comparable_data"),
    ],
)
def test_the_driver_request_follows_the_name_code(name: str, request_id: str) -> None:
    assert driver_request(name) == request_id


def test_missing_quotes_only_without_quotes_in_the_file() -> None:
    clean = application("001")
    assert "OI-missing_quotes" not in items_of(clean)
    item = items_of(without(clean, "contractor_or_equipment_quotes"))["OI-missing_quotes"]
    assert item.request_id == "build_out_quotes" and item.placeholders["capex_field"] == "build_out"


def test_every_resolution_applies_and_evaluates() -> None:
    data = without(
        application("001"), "equity_bank_statements", "owner_resumes_and_references", "contractor_or_equipment_quotes"
    )
    state = from_application(data)
    items = detect_open_items(state, policy_result=evaluate(state))
    for item_id, choices in startup_choices(items).items():
        for choice in choices:
            evaluate(apply(state, choice.substitutions))  # no LeverError, the application still validates
        assert choices, item_id


def test_verified_statements_change_the_recommendation_of_a_file_waiting_for_them() -> None:
    state = from_application(application("002"))
    current = evaluate(state)
    assert current.recommendation == "request_more_information"
    ranked = rank_startup_requests(state, detect_open_items(state, policy_result=current), current)
    assert ranked[0].request_id == "equity_bank_statements" and ranked[0].voi > 0
    assert ranked[0].title == "Bank statements showing the owner cash equity"
    assert ranked[0].reason.startswith('Requesting "Bank statements showing the owner cash equity"')


def test_credit_and_insurance_files_get_no_startup_items() -> None:
    for name in ("APP-C-001", "APP-I-001"):
        data = json.loads((REPO_ROOT / "eval" / "applications" / f"{name}.json").read_text(encoding="utf-8"))
        state = from_application(data)
        types = {item.problem_type for item in detect_open_items(state, policy_result=evaluate(state))}
        assert not types & set(STARTUP_PROBLEM_TYPES)
