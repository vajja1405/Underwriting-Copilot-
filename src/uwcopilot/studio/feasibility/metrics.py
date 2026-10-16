"""Summaries of the simulated paths: DSCR by year, cash shortfall, break-even month and P10/P50/P90 bands.

Definitions (shown in docs/studio/walkthrough_s3.md):
- DSCR of year y = the year's NOI / the year's debt service, per path, rounded to two decimals with the lender's
  own coverage_ratio (agent/credit_calculations.py), then compared with the target ("at least" includes it).
- P(DSCR >= target) = the share of paths whose DSCR meets the target.
- P(cash < 0 within 24 months) = the share of paths whose cash balance goes below zero in months 1-24.
- Break-even month = the first month that starts a run of BREAK_EVEN_RUN months in a row with net income
  (NOI - debt service) of at least zero (a design choice: one good month in a noisy series is not break-even).
- Bands = the 10th, 50th and 90th percentiles across paths (NumPy's default linear interpolation).
"""

import numpy as np

from uwcopilot.agent.credit_calculations import coverage_ratio
from uwcopilot.studio.feasibility.model import MONTHS_PER_YEAR, PathResults

BREAK_EVEN_RUN = 3
CASH_CHECK_MONTHS = 24
PERCENTILES: tuple[int, int, int] = (10, 50, 90)


def year_columns(year: int) -> slice:
    """The month columns of year 1, 2, ... (months 1-12, 13-24, ...)."""
    return slice(MONTHS_PER_YEAR * (year - 1), MONTHS_PER_YEAR * year)


def annual_total(matrix: np.ndarray, year: int) -> np.ndarray:
    """Per path, the sum over one year's months."""
    columns = year_columns(year)
    if matrix.shape[1] < columns.stop:
        raise ValueError(f"year {year} is beyond the simulated horizon of {matrix.shape[1]} months")
    return matrix[:, columns].sum(axis=1)


def dscr_by_year(paths: PathResults, year: int) -> np.ndarray:
    """Per path, the year's DSCR rounded like the lender rounds it (NaN when the year has no debt service)."""
    noi = annual_total(paths.noi, year)
    debt = annual_total(paths.debt_service, year)
    ratios = [coverage_ratio(float(a), float(b)) for a, b in zip(noi, debt, strict=True)]
    return np.array([np.nan if ratio is None else ratio for ratio in ratios])


def share_at_least(values: np.ndarray, threshold: float) -> float:
    """Share of paths with a value at least the threshold (NaN values count as not meeting it)."""
    return float(np.mean(np.nan_to_num(values, nan=-np.inf) >= threshold))


def p_dscr_at_least(paths: PathResults, year: int, target: float) -> float:
    """P(DSCR of the year >= target) across paths."""
    return share_at_least(dscr_by_year(paths, year), target)


def cash_negative_within(paths: PathResults, months: int = CASH_CHECK_MONTHS) -> np.ndarray:
    """Per path, True when the cash balance goes below zero in the first `months` months."""
    return np.any(paths.cash[:, :months] < 0, axis=1)


def p_cash_negative(paths: PathResults, months: int = CASH_CHECK_MONTHS) -> float:
    """P(cash < 0 within `months` months) across paths."""
    return float(np.mean(cash_negative_within(paths, months)))


def break_even_month(net_income: np.ndarray, run_length: int = BREAK_EVEN_RUN) -> np.ndarray:
    """Per path, the 1-based month that starts the first run of `run_length` months with net income >= 0.

    0 means no such run within the horizon.
    """
    good = net_income >= 0
    if good.shape[1] < run_length:
        return np.zeros(good.shape[0], dtype=int)
    windows = np.lib.stride_tricks.sliding_window_view(good, run_length, axis=1).all(axis=2)
    found = windows.any(axis=1)
    first = windows.argmax(axis=1) + 1
    return np.where(found, first, 0)


def bands(matrix: np.ndarray) -> np.ndarray:
    """(3 x months) P10, P50 and P90 across paths for every month."""
    return np.percentile(matrix, PERCENTILES, axis=0)


def percentiles_of(values: np.ndarray) -> tuple[float, float, float]:
    """P10, P50 and P90 of one value per path (finite values only; NaN when none)."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan"), float("nan"), float("nan")
    low, mid, high = np.percentile(finite, PERCENTILES)
    return float(low), float(mid), float(high)
