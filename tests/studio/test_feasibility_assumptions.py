"""config/studio/feasibility/assumptions.yaml and the DSCR target: labeled, editable, never retyped."""

import pytest
from pydantic import ValidationError

from uwcopilot.agent.policy_credit import DSCR_MINIMUM
from uwcopilot.studio.feasibility.assumptions import (
    FeasibilityAssumptions,
    custom_dscr_target,
    default_dscr_target,
    load_assumptions,
)


def test_every_assumption_is_an_unsourced_range_with_a_note() -> None:
    assumptions = load_assumptions()
    for name in FeasibilityAssumptions.model_fields:
        quantity = getattr(assumptions, name)
        assert quantity.status == "ASSUMPTION", f"{name} must stay an ASSUMPTION until a source is opened and cited"
        assert quantity.source is None
        assert quantity.low < quantity.high and quantity.note


def test_zero_sourced_fields() -> None:
    # Nobody may flip a label to SOURCED without a citation (Studio rule D); there is none yet.
    assumptions = load_assumptions()
    assert [
        name for name in FeasibilityAssumptions.model_fields if getattr(assumptions, name).status == "SOURCED"
    ] == []


def test_unknown_or_missing_keys_fail(tmp_path) -> None:
    good = load_assumptions().model_dump()
    with pytest.raises(ValidationError):
        FeasibilityAssumptions.model_validate({**good, "made_up_shock": good["price_elasticity"]})
    missing = dict(good)
    missing.pop("price_elasticity")
    with pytest.raises(ValidationError):
        FeasibilityAssumptions.model_validate(missing)
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_assumptions(bad)


def test_default_dscr_target_is_the_synthetic_policy_value_labeled_editable() -> None:
    target = default_dscr_target()
    assert target.value == DSCR_MINIMUM["term_loan"]  # imported, not retyped
    assert "SYNTHETIC Northwind guideline NBC-4.2" in target.label and "editable" in target.label
    assert "NBC-4.2" in target.source


def test_custom_dscr_target() -> None:
    target = custom_dscr_target(1.4)
    assert target.value == 1.4 and target.label == "user-entered DSCR target"
    with pytest.raises(ValidationError):
        custom_dscr_target(0.0)
