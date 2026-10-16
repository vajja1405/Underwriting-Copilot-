"""to_application (Studio S6): the founder package as the lender copilot's StartupCreditApplication, with the S6 rules:
registry metro ids, each business type's own industry code, top drivers ranked exactly 1-3, NOI never above
revenue, other debt with its payments, documents behind every evidence flag, and no free text in code fields."""

import pytest
from package_helpers import package

from uwcopilot.agent.applications import STARTUP_METRO_IDS, StartupCreditApplication
from uwcopilot.agent.policy import PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.policy_startup import projected_annual_debt_service, top_drivers
from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.metros import list_metros
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package import ApplicationNotReady, application_id_for, to_application
from uwcopilot.studio.package.models import Package

# Golden document variants (package/golden.py): index 0 none, 1 statements match, 2 statements conflict.
NO_DOCUMENTS = ("synthetic_metro", "car_wash")
MATCHING_STATEMENTS = ("synthetic_metro", "laundromat")
CONFLICTING_STATEMENTS = ("synthetic_metro", "motel")


def replace_claim_value(built: Package, claim_id: str, value: float) -> Package:
    """The package with one claim's value changed (to test what to_application refuses)."""
    claims = tuple(
        claim.model_copy(update={"value": value}) if claim.id == claim_id else claim for claim in built.ledger.claims
    )
    ledger = Ledger.model_construct(**{**built.ledger.__dict__, "claims": claims})
    return built.model_copy(update={"ledger": ledger})


@pytest.mark.parametrize("spec", [NO_DOCUMENTS, MATCHING_STATEMENTS, CONFLICTING_STATEMENTS])
def test_the_application_validates_and_uses_registry_codes(spec: tuple[str, str]) -> None:
    built = package(*spec)
    application = to_application(built)
    assert isinstance(application, StartupCreditApplication)
    assert application.schema_version == "startup-application-v1" and application.line == "startup_credit"
    assert application.applicant.metro_id in list_metros() and application.applicant.metro_id in STARTUP_METRO_IDS
    assert application.applicant.industry == application.applicant.business_type == spec[1]
    assert application.application_id == application_id_for(built)
    assert application.notes == "" and application.applicant.site_description == ""


def test_the_projection_keeps_noi_at_or_below_revenue_and_matches_the_ledger() -> None:
    built = package(*NO_DOCUMENTS)
    application = to_application(built)
    for year in application.projections.years:
        for percentile in ("p10", "p50", "p90"):
            assert getattr(year, f"noi_{percentile}") <= getattr(year, f"revenue_{percentile}")
            assert (
                getattr(year, f"noi_{percentile}")
                == built.ledger.claim(ids.year_id(year.year, f"noi_{percentile}")).value
            )
    simulation = application.projections.simulation
    assert simulation.p_dscr_ge_target_year2 == pytest.approx(built.ledger.claim(ids.SIM_P_DSCR_Y2).value / 100)


def test_noi_above_revenue_is_refused_not_clamped() -> None:
    built = package(*NO_DOCUMENTS)
    revenue = built.ledger.claim(ids.year_id(2, "revenue_p90")).value
    broken = replace_claim_value(built, ids.year_id(2, "noi_p90"), revenue + 1.0)
    with pytest.raises(ApplicationNotReady, match="NOI P90 is above revenue P90"):
        to_application(broken)


def test_the_top_drivers_are_ranks_one_to_three_exactly() -> None:
    application = to_application(package(*NO_DOCUMENTS))
    ranks = [item.tornado_rank for item in application.material_assumptions if item.tornado_rank is not None]
    assert len(ranks) == len(set(ranks)) and {1, 2, 3} <= set(ranks)
    assert [item.tornado_rank for item in top_drivers(application)] == [1, 2, 3]
    assert all(item.source.strip() for item in application.material_assumptions)


def test_other_debt_is_listed_with_its_payment_and_counted_in_debt_service() -> None:
    built = package(*NO_DOCUMENTS)
    application = to_application(built)
    debts = application.sources_and_uses.sources.other_debt
    assert len(debts) == len(built.applicant.other_debt_kinds)
    for index, debt in enumerate(debts, start=1):
        assert debt.amount == built.ledger.claim(ids.other_debt_id(index, "amount")).value
        assert debt.monthly_payment == built.ledger.claim(ids.other_debt_id(index, "monthly_payment")).value
    assert projected_annual_debt_service(application) == built.ledger.claim(ids.CALC_ANNUAL_DEBT_SERVICE).value
    assert application.sources_and_uses.sources.total() == pytest.approx(
        application.sources_and_uses.uses.total(), abs=1.0
    )


def test_evidence_flags_need_their_documents() -> None:
    plain = to_application(package(*NO_DOCUMENTS))
    assert plain.sources_and_uses.equity_verified_by_statements is False
    assert not any(owner.experience_evidence_provided for owner in plain.owners)
    matching = to_application(package(*MATCHING_STATEMENTS))
    assert matching.sources_and_uses.equity_verified_by_statements is True
    assert "equity_bank_statements" in matching.documents_provided
    assert all(owner.experience_evidence_provided for owner in matching.owners)
    conflicting = to_application(package(*CONFLICTING_STATEMENTS))
    assert conflicting.sources_and_uses.equity_verified_by_statements is False  # a CONFLICTING figure is not verified


def test_a_verified_flag_without_its_document_in_the_file_does_not_count() -> None:
    built = package(*MATCHING_STATEMENTS)
    applicant = built.applicant.model_copy(
        update={
            "documents_provided": [
                code for code in built.applicant.documents_provided if code != "equity_bank_statements"
            ]
        }
    )
    application = to_application(built.model_copy(update={"applicant": applicant}))
    assert application.sources_and_uses.equity_verified_by_statements is False


def test_a_driver_document_counts_only_when_it_is_in_the_file() -> None:
    built = package(*NO_DOCUMENTS)
    first = built.material_assumptions[0].model_copy(
        update={"supporting_document_types": ["comparable_data_with_source"]}
    )
    with_type = built.model_copy(update={"material_assumptions": [first, *built.material_assumptions[1:]]})
    assert to_application(with_type).material_assumptions[0].supporting_document_provided is False
    listed = with_type.applicant.model_copy(
        update={"documents_provided": [*with_type.applicant.documents_provided, "comparable_data_with_source"]}
    )
    application = to_application(with_type.model_copy(update={"applicant": listed}))
    assert application.material_assumptions[0].supporting_document_provided is True


def test_the_policy_reads_the_same_numbers_as_the_ledger() -> None:
    built = package(*NO_DOCUMENTS)
    application = to_application(built)
    screening = [ScreeningOutcome(party_id=party, status="clear") for party in ("APPLICANT", "O1")]
    result = evaluate_policy(application, PolicyInputs(screening=screening))
    calcs = {item.calc_id: item.value for item in result.calculations}
    assert calcs["CALC-startup_projected_dscr"] == built.ledger.claim(ids.CALC_PROJECTED_DSCR).value
    assert calcs["CALC-startup_equity_injection_pct"] == built.ledger.claim(ids.CALC_EQUITY_PCT).value


def test_missing_experience_stops_the_conversion() -> None:
    built = package(*NO_DOCUMENTS)
    owner = built.applicant.owners[0].model_copy(update={"experience_months": None})
    applicant = built.applicant.model_copy(update={"owners": [owner]})
    with pytest.raises(ApplicationNotReady, match="relevant experience"):
        to_application(built.model_copy(update={"applicant": applicant}))
