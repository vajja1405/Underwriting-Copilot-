"""Tests for uwcopilot.resume.text: number formatting, bullet assembly and the wording guard."""

import pytest

from uwcopilot.resume.results import Estimate
from uwcopilot.resume.text import (
    BulletDraft,
    WordingError,
    check_wording,
    compact,
    count,
    interval,
    num,
    signed,
    years_text,
)


def test_numbers_keep_four_decimals_and_counts_get_separators() -> None:
    assert num(0.94117647) == "0.9412"
    assert signed(0.0141) == "+0.0141"
    assert signed(-0.68650) == "-0.6865"
    assert count(30681) == "30,681"
    assert compact(1.25) == "1.25"
    assert compact(1.1) == "1.1"


def test_interval_marks_wilson_intervals() -> None:
    assert interval(Estimate(0.9412, 0.8723, 1.0, "bootstrap")) == "0.9412 [0.8723, 1.0000]"
    assert interval(Estimate(0.2667, 0.1713, 0.3901, "wilson")) == "0.2667 [Wilson 0.1713, 0.3901]"
    assert interval(Estimate(103.77, 89.94, 118.87, "bootstrap"), decimals=1) == "103.8 [89.9, 118.9]"
    assert interval(Estimate(0.0141, 0.013, 0.0152, "bootstrap"), with_sign=True) == "+0.0141 [+0.0130, +0.0152]"


def test_years_text_gives_one_year_or_a_range() -> None:
    assert years_text([2016]) == "2016"
    assert years_text([2018, 2017]) == "2017-2018"


@pytest.mark.parametrize(
    "text",
    [
        "100% of injected prompt-injection attempts blocked",
        "every injection is blocked",
        "deployed on a cloud with Terraform",
        "accuracy in production",
        "real-world savings of 30 hours",
        "a model card compliant with SR 26-2",
        "SR 11-7 model risk",
        "$0.01 per packet",
        "2.1 s p95 per packet",
        "runs on AWS Bedrock",
        "memos written by Claude",
    ],
)
def test_check_wording_rejects_claims_this_project_cannot_make(text: str) -> None:
    with pytest.raises(WordingError):
        check_wording(text)


def test_check_wording_accepts_measured_wording() -> None:
    check_wording("field F1 0.9936 [0.9889, 0.9971] on 60 SYNTHETIC packets; a blocking referral is added")


def test_draft_with_a_measured_clause_prints_context_in_order() -> None:
    draft = BulletDraft("credit_model", "lender", "Credit model")
    draft.add("AUC 0.7151 [0.7123, 0.7177]", "risk_metrics.json")
    draft.add_context("SHAP reason codes")
    bullet, omissions = draft.finish()
    assert bullet is not None
    assert bullet.text == "Credit model: AUC 0.7151 [0.7123, 0.7177]; SHAP reason codes."
    assert bullet.sources == ("risk_metrics.json",)
    assert omissions == []


def test_draft_with_only_context_is_left_out() -> None:
    draft = BulletDraft("credit_model", "lender", "Credit model")
    draft.add_context("SHAP reason codes")
    bullet, omissions = draft.finish()
    assert bullet is None
    assert [item.part for item in omissions] == ["whole bullet"]


def test_a_missing_core_metric_takes_the_whole_bullet() -> None:
    draft = BulletDraft("kyc_screening", "lender", "Sanctions screening")
    draft.add("false-positive rate 0.0887 [0.0760, 0.1030]", "kyc_metrics.json")
    draft.omit("recall", "not measured: test", core=True)
    bullet, omissions = draft.finish()
    assert bullet is None
    assert [item.part for item in omissions] == ["recall", "whole bullet"]


def test_finish_refuses_forbidden_wording() -> None:
    draft = BulletDraft("governance", "lender", "Governance")
    draft.add("deployed to 3 regions", "deployment_metrics.json")
    with pytest.raises(WordingError):
        draft.finish()
