"""The S12 baseline B0 (docs/studio/s12_baseline.md, section 2) on a golden SYNTHETIC-metro plan, offline.

B0 prints the founder's typed figures, the untyped one-forecast inputs and years 1-3 results; only a founder figure
that every document about it agrees with carries a source.
"""

from dataclasses import replace

import numpy as np
import pytest
from package_helpers import scenario

from uwcopilot.studio.business_metrics.baseline import (
    BaselineFigure,
    backing_documents,
    baseline_plan,
    baseline_setup,
    founder_figures,
    input_figures,
    result_figures,
    typed_fields,
    used_by_one_forecast,
)
from uwcopilot.studio.feasibility.distributions import with_mode
from uwcopilot.studio.feasibility.model import single_point_run
from uwcopilot.studio.feasibility.setup import SimulationSetup
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.path_to_approval.models import PlanCase

METRO, TYPE = "synthetic_metro", "car_wash"


def document(field: str, value: float, doc_id: str = "SYN-DOC", page: int | None = 1) -> FounderDocumentValue:
    """A SYNTHETIC document value."""
    return FounderDocumentValue(field=field, value=value, doc_id=doc_id, document_type="test_document", page=page)


def test_founder_figures_are_the_typed_fields_and_cite_nothing_without_documents() -> None:
    case = scenario(METRO, TYPE).case
    figures = founder_figures(case, [])
    assert [figure.name for figure in figures] == [field for field, _ in typed_fields(case)]
    assert figures and all(figure.group == "founder" and not figure.has_source for figure in figures)
    assert case.plan.owner_draw_per_month == 0 and "plan.owner_draw_per_month" not in {f.name for f in figures}


def test_an_agreeing_document_gives_a_citation_within_the_ledger_tolerance() -> None:
    case = scenario(METRO, TYPE).case
    equity = case.plan.owner_equity
    figures = founder_figures(case, [document("plan.owner_equity", equity + 0.99, "SYN-STATEMENTS")])
    cited = {figure.name: figure.source for figure in figures if figure.has_source}
    assert cited == {"plan.owner_equity": "SYN-STATEMENTS p1"}


def test_a_disagreeing_document_removes_the_citation() -> None:
    case = scenario(METRO, TYPE).case
    equity = case.plan.owner_equity
    documents = [document("plan.owner_equity", equity, "SYN-A"), document("plan.owner_equity", equity * 0.9, "SYN-B")]
    assert backing_documents("plan.owner_equity", equity, documents) is None
    assert not any(figure.has_source for figure in founder_figures(case, documents))


def test_a_document_about_another_field_does_not_back_a_figure() -> None:
    assert backing_documents("plan.capex", 100.0, [document("plan.owner_equity", 100.0)]) is None
    backed = backing_documents("plan.capex", 100.0, [document("plan.capex", 100.0, page=None)])
    assert backed is not None and len(backed) == 1


@pytest.mark.parametrize(
    ("name", "used"),
    [
        ("price", True),
        ("cost_ratio_cogs", True),
        ("capex_overrun_share", True),
        ("monthly_noise_sd", False),
        ("competitor_entry_annual_probability", False),
        ("competitor_entry_demand_loss", False),
        ("rate_shock_annual_probability", False),
        ("rate_shock_size_pct_points", False),
        ("one_off_shock_annual_probability", False),
        ("one_off_shock_revenue_loss_share", False),
        ("one_off_shock_repair_cost_share_of_capex", False),
    ],
)
def test_a_one_forecast_plan_uses_no_shock_or_noise_input(name: str, used: bool) -> None:
    assert used_by_one_forecast(name) is used


def variable_rate_case() -> PlanCase:
    """The golden plan with a variable-rate loan, so the setup has the rate-shock inputs."""
    case = scenario(METRO, TYPE).case
    return case.model_copy(update={"loan": case.loan.model_copy(update={"rate_type": "variable"})})


def test_a_variable_rate_plan_prints_no_rate_shock_input() -> None:
    case = variable_rate_case()
    setup = baseline_setup(case, 36)
    assert {"rate_shock_annual_probability", "rate_shock_size_pct_points"} <= set(setup.distributions)
    names = {figure.name for figure in input_figures(case, setup)}
    assert not any(name.startswith("rate_shock_") for name in names)


def one_forecast_with_mode(setup: SimulationSetup, name: str, mode: float) -> np.ndarray:
    """Revenue, NOI, debt service and cash of the one-forecast run after moving one input's most likely value."""
    moved = with_mode(setup.distributions[name], mode, "test: mode moved")
    paths = single_point_run(replace(setup, distributions={**setup.distributions, name: moved}))
    return np.concatenate([paths.revenue, paths.noi, paths.debt_service, paths.cash])


def test_an_input_left_out_of_b0_does_not_act_in_the_one_forecast_run() -> None:
    setup = baseline_setup(variable_rate_case(), 36)
    ramp = setup.distributions["ramp_starting_share"]
    base = one_forecast_with_mode(setup, "ramp_starting_share", ramp.mode)
    # The check can see a change: moving an input the one forecast uses moves the run.
    assert not np.allclose(one_forecast_with_mode(setup, "ramp_starting_share", ramp.high), base)
    left_out = [name for name in setup.distributions if not used_by_one_forecast(name)]
    assert "rate_shock_size_pct_points" in left_out and setup.distributions["rate_shock_size_pct_points"].varies
    for name in left_out:
        item = setup.distributions[name]
        for mode in (item.low, item.high):
            assert np.allclose(one_forecast_with_mode(setup, name, mode), base), name


def test_inputs_are_the_untyped_one_forecast_inputs_at_their_mode() -> None:
    case = scenario(METRO, TYPE).case
    setup = baseline_setup(case, 36)
    figures = input_figures(case, setup)
    names = [figure.name for figure in figures]
    assert names and all(used_by_one_forecast(name) for name in names if name != "note_rate_pct")
    assert all(not figure.has_source and figure.group == "input" for figure in figures)
    for figure in figures:
        if figure.name in setup.distributions:
            assert figure.value == pytest.approx(setup.distributions[figure.name].mode)
    assert ("note_rate_pct" in names) is (case.loan.annual_rate_pct is None)


def test_the_note_rate_is_an_input_when_the_founder_did_not_type_it() -> None:
    case = scenario(METRO, TYPE).case
    untyped = case.model_copy(update={"loan": case.loan.model_copy(update={"annual_rate_pct": None})})
    setup = baseline_setup(untyped, 36)
    rates = [figure for figure in input_figures(untyped, setup) if figure.name == "note_rate_pct"]
    assert len(rates) == 1 and rates[0].value == pytest.approx(setup.loan.annual_rate_pct)


def test_results_are_the_one_forecast_years_one_to_three() -> None:
    case = scenario(METRO, TYPE).case
    setup = baseline_setup(case, 36)
    figures = {figure.name: figure.value for figure in result_figures(case, setup)}
    paths = single_point_run(setup)
    assert figures["y1.revenue"] == pytest.approx(float(np.sum(paths.revenue[0, :12])))
    assert figures["y2.noi"] == pytest.approx(float(np.sum(paths.noi[0, 12:24])))
    assert figures["y3.dscr"] == pytest.approx(figures["y3.noi"] / figures["y3.debt_service"])
    assert len(figures) == 12


def test_a_two_year_setup_prints_two_years() -> None:
    case = scenario(METRO, TYPE).case
    names = [figure.name for figure in result_figures(case, baseline_setup(case, 24))]
    assert names and all(name.startswith(("y1.", "y2.")) for name in names)


def test_the_plan_is_founder_then_input_then_result() -> None:
    case = scenario(METRO, TYPE).case
    plan = baseline_plan(case, [], baseline_setup(case, 36))
    groups = [figure.group for figure in plan]
    assert groups == sorted(groups, key=["founder", "input", "result"].index)
    assert not any(figure.has_source for figure in plan)


def test_a_figure_with_a_source_says_so() -> None:
    assert BaselineFigure("plan.capex", "founder", 1.0, "SYN-QUOTE p1").has_source
    assert not BaselineFigure("y1.revenue", "result", 1.0).has_source
