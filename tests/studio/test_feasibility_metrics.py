"""Summaries: DSCR by year (lender rounding), cash shortfall, break-even month and bands."""

import numpy as np
import pytest

from uwcopilot.agent.credit_calculations import coverage_ratio
from uwcopilot.studio.feasibility.metrics import (
    annual_total,
    bands,
    break_even_month,
    dscr_by_year,
    p_cash_negative,
    p_dscr_at_least,
    percentiles_of,
    share_at_least,
)
from uwcopilot.studio.feasibility.model import PathResults


def make_paths(noi: np.ndarray, debt: np.ndarray, cash: np.ndarray | None = None) -> PathResults:
    """PathResults from hand-written NOI and debt arrays (other arrays are placeholders)."""
    zeros = np.zeros_like(noi)
    return PathResults(
        volume=zeros,
        revenue=zeros,
        noi=noi,
        debt_service=debt,
        net_income=noi - debt,
        cash=cash if cash is not None else np.cumsum(noi - debt, axis=1),
        starting_cash=np.zeros(noi.shape[0]),
        competitor_entry_month=np.full(noi.shape[0], -1),
        rate_shock_month=np.full(noi.shape[0], -1),
        one_off_month=np.full(noi.shape[0], -1),
    )


def test_dscr_uses_the_lender_rounding() -> None:
    # Year-2 NOI 12 x 1,040.4 = 12,484.8; debt 12 x 832 = 9,984 -> 1.2504... rounds to 1.25 (meets 1.25).
    noi = np.full((2, 24), 1040.4)
    noi[1, 12:] = 1000.0  # 12,000 / 9,984 = 1.2019 -> 1.20
    debt = np.full((2, 24), 832.0)
    paths = make_paths(noi, debt)
    ratios = dscr_by_year(paths, 2)
    assert ratios.tolist() == [coverage_ratio(12 * 1040.4, 12 * 832.0), 1.2]
    assert ratios[0] == 1.25
    assert p_dscr_at_least(paths, 2, 1.25) == 0.5
    assert annual_total(noi, 1)[0] == pytest.approx(12 * 1040.4)
    with pytest.raises(ValueError, match="beyond"):
        annual_total(noi, 3)


def test_share_at_least_counts_nan_as_not_met() -> None:
    assert share_at_least(np.array([1.3, np.nan, 1.0, 1.25]), 1.25) == 0.5


def test_cash_negative_only_within_the_window() -> None:
    cash = np.ones((3, 36))
    cash[0, 5] = -1.0  # within 24 months
    cash[1, 30] = -1.0  # after 24 months
    paths = make_paths(np.zeros((3, 36)), np.zeros((3, 36)), cash)
    assert p_cash_negative(paths) == pytest.approx(1 / 3)
    assert p_cash_negative(paths, months=36) == pytest.approx(2 / 3)


def test_break_even_needs_three_good_months_in_a_row() -> None:
    net = np.array(
        [
            [-1, -1, 1, 1, 1, -1, -1],  # starts in month 3
            [1, -1, 1, 1, -1, 1, 1],  # never three in a row
            [0, 0, 0, -5, -5, -5, -5],  # zero counts as break-even: month 1
            [-1, -1, -1, -1, 2, 2, 2],  # starts in month 5 (the last possible start)
        ],
        dtype=float,
    )
    assert break_even_month(net).tolist() == [3, 0, 1, 5]
    assert break_even_month(net[:, :2]).tolist() == [0, 0, 0, 0]


def test_bands_and_percentiles() -> None:
    matrix = np.arange(1, 101, dtype=float).reshape(100, 1) * np.ones((100, 3))
    result = bands(matrix)
    assert result.shape == (3, 3)
    assert result[:, 0] == pytest.approx(np.percentile(np.arange(1, 101), [10, 50, 90]))
    assert percentiles_of(np.array([np.nan, 1.0, 2.0, 3.0]))[1] == 2.0
    assert all(np.isnan(value) for value in percentiles_of(np.array([np.nan])))
