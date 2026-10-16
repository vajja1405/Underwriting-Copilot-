"""Tornado: which assumptions move the outcome most (one-at-a-time swings, holding the seed).

Method, for every input that is a range (fixed values cannot swing):
1. Keep every random draw of the base run: the other inputs' uniforms, the shock timings and the monthly noise
   (common random numbers, "holding the seed").
2. Set this one input to its P10 on every path and rerun the paths; then to its P90.
3. The metric is the P50 across paths of year-2 net income (NOI minus debt service), the year the lender uses
   for the projected DSCR (NSP-1.2). The swing is |metric at P90 - metric at P10|.
4. Rank by swing. The top three are the plan's "top drivers" (NSP-6.1): the inputs worth backing with a document.

The probability of a shock is swung like any other input; holding the shock's own uniform fixed, a higher
probability can only make the shock happen sooner (distributions.event_month). One-at-a-time swings ignore
interactions between inputs; that limit is stated in the method text of every result and in the walkthrough.
"""

import numpy as np

from uwcopilot.studio.feasibility.metrics import annual_total, p_dscr_at_least
from uwcopilot.studio.feasibility.model import BASE_CASE, Draws, PathResults, StressLevers, run_paths
from uwcopilot.studio.feasibility.result import Tornado, TornadoBar
from uwcopilot.studio.feasibility.setup import SimulationSetup

TORNADO_METRIC = "year-2 net income (NOI minus debt service), P50 across paths, USD"
TORNADO_METHOD = (
    "one input at a time set to its P10 and then its P90 on every path, all other draws held fixed (same seed); "
    "swing = |metric at P90 - metric at P10|; ranked by swing; one-at-a-time swings ignore interactions between inputs"
)
LOW_SHARE = 0.10
HIGH_SHARE = 0.90
TOP_DRIVERS = 3
SWING_YEAR = 2


def year2_net_income_p50(paths: PathResults) -> float:
    """P50 across paths of year-2 net income."""
    return float(np.median(annual_total(paths.net_income, SWING_YEAR)))


def swing_one(
    setup: SimulationSetup,
    values: dict[str, np.ndarray],
    draws: Draws,
    name: str,
    share: float,
    levers: StressLevers,
) -> PathResults:
    """Rerun the paths with one input fixed at the given percentile (all other draws unchanged)."""
    changed = dict(values)
    changed[name] = np.full(draws.n_paths, setup.distributions[name].percentile(share))
    return run_paths(setup, changed, draws, levers)


def tornado(
    setup: SimulationSetup,
    values: dict[str, np.ndarray],
    draws: Draws,
    dscr_target: float,
    base: PathResults,
    levers: StressLevers = BASE_CASE,
) -> Tornado:
    """Swing every ranged input between its P10 and P90 and rank the swings (see the module docstring)."""
    rows: list[dict[str, object]] = []
    for name, item in setup.distributions.items():
        if not item.varies:
            continue
        low_paths = swing_one(setup, values, draws, name, LOW_SHARE, levers)
        high_paths = swing_one(setup, values, draws, name, HIGH_SHARE, levers)
        at_low = year2_net_income_p50(low_paths)
        at_high = year2_net_income_p50(high_paths)
        rows.append(
            {
                "name": name,
                "description": item.description,
                "unit": item.unit,
                "status": item.status,
                "input_p10": item.percentile(LOW_SHARE),
                "input_p90": item.percentile(HIGH_SHARE),
                "metric_at_p10": at_low,
                "metric_at_p90": at_high,
                "swing": abs(at_high - at_low),
                "p_dscr_year2_at_p10": p_dscr_at_least(low_paths, SWING_YEAR, dscr_target),
                "p_dscr_year2_at_p90": p_dscr_at_least(high_paths, SWING_YEAR, dscr_target),
            }
        )
    rows.sort(key=lambda row: (-float(row["swing"]), str(row["name"])))
    bars = [TornadoBar.model_validate({"rank": index + 1, **row}) for index, row in enumerate(rows)]
    return Tornado(
        metric=TORNADO_METRIC,
        method=TORNADO_METHOD,
        base_metric=year2_net_income_p50(base),
        base_p_dscr_year2=p_dscr_at_least(base, SWING_YEAR, dscr_target),
        bars=bars,
        top_drivers=[bar.name for bar in bars[:TOP_DRIVERS]],
    )
