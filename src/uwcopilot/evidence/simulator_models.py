"""Result shapes and fixed settings of the evidence-request simulator (innovation S5; see simulator.py).

A SimulationResult is one (case, policy, priors variant) run: every request made, in order, with the value of
information the engine gave it just before (under the variant's priors), the recommendation before and after the
reveal, and the per-case metrics:
- n_requests and cost_weighted_requests (the catalog's documented cost weights; planning assumptions);
- n_zero_value_requests: requests whose VOI was 0 just before AND whose reveal did not change the recommendation;
- requests_to_stable: the number of requests after which the recommendation equals the full-information
  recommendation and stays equal to the end of the run (None when the run ends on a different recommendation);
- decision_error: the final recommendation differs from the full-information recommendation.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

PolicyName = Literal["voi", "voi_lookahead", "checklist", "random", "oracle"]
PriorsVariant = Literal[
    "documented",
    "latent_x0.5",
    "latent_x1.5",
    "latent_removed",
    "empirical_dev",
    "empirical_dev_smoothed",
    "oracle_shares",
]
POLICIES: tuple[str, ...] = ("voi", "voi_lookahead", "checklist", "random", "oracle")
PRIORS_VARIANTS: tuple[str, ...] = (
    "documented",
    "latent_x0.5",
    "latent_x1.5",
    "latent_removed",
    "empirical_dev",
    "empirical_dev_smoothed",
    "oracle_shares",
)
# The prior of the latent (true) option is multiplied by this factor, then all priors are renormalized to sum to 1.
PRIOR_FACTORS: dict[str, float] = {"latent_x0.5": 0.5, "latent_x1.5": 1.5}
# Variants whose priors are a table of latent shares the caller measures and passes in (simulator.py docstring):
# empirical_dev (development split, unsmoothed), empirical_dev_smoothed (development split, add-one smoothing) and
# oracle_shares (the evaluated group's own shares: an in-sample oracle reference).
TABLE_VARIANTS: tuple[str, ...] = ("empirical_dev", "empirical_dev_smoothed", "oracle_shares")
# Resolution template key -> option id -> prior (a latent share).
EmpiricalPriors = dict[str, dict[str, float]]
# Table variant -> its priors table.
PriorTables = dict[str, EmpiricalPriors]
# A run never makes more requests than this (a guard; no run is expected to reach it).
MAX_REQUESTS = 20
# The oracle brute-forces request orders only when the case starts with at most this many open requests.
ORACLE_MAX_OPEN_REQUESTS = 4
# The oracle's search never expands more positions than this (a guard; 4 open requests give at most 16 subsets).
ORACLE_MAX_POSITIONS = 500

StopReason = Literal[
    "no_nonzero_voi",  # voi / voi_lookahead: no open request has a value above zero
    "list_exhausted",  # no open item with a catalog request is left
    "oracle_reached",  # the oracle reached the full-information recommendation
    "oracle_unreachable",  # no set of open requests reaches the full-information recommendation
    "max_requests",  # the MAX_REQUESTS guard stopped the run
    "not_computed",  # the case or policy could not be run (see not_computed_reason)
]


class Frozen(BaseModel):
    """Base for simulator results: unknown fields are an error and objects are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SimStep(Frozen):
    """One request the policy made and what its reveal did."""

    request_id: str
    params: dict[str, str]
    item_ids: tuple[str, ...]
    cost_weight: float
    voi_before: float
    p_change_before: float
    recommendation_before: str
    recommendation_after: str
    revealed_problem_ids: tuple[str, ...]
    zero_value: bool


class SimulationResult(Frozen):
    """One simulated run of a policy on a case (see the module docstring for the metrics)."""

    case_id: str
    line: str
    split: str
    problem_types: tuple[str, ...]
    policy: str
    priors_variant: str
    computed: bool
    not_computed_reason: str | None = None
    initial_recommendation: str | None = None
    full_information_recommendation: str | None = None
    final_recommendation: str | None = None
    n_initial_open_requests: int = 0
    undetected_problem_ids: tuple[str, ...] = ()
    undetected_problem_types: tuple[str, ...] = ()
    steps: tuple[SimStep, ...] = ()
    stop_reason: StopReason = "not_computed"
    n_requests: int = 0
    cost_weighted_requests: float = 0.0
    n_zero_value_requests: int = 0
    requests_to_stable: int | None = None
    decision_error: bool | None = None


def requests_to_stable(recommendations: list[str], target: str) -> int | None:
    """Requests after which the recommendation equals target and stays equal to the end (None if it ends elsewhere).

    recommendations[0] is the recommendation before any request and recommendations[k] the one after k requests.
    """
    if not recommendations or recommendations[-1] != target:
        return None
    index = len(recommendations) - 1
    while index > 0 and recommendations[index - 1] == target:
        index -= 1
    return index
