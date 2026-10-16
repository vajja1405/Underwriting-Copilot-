"""Result shapes of the value-of-information score (voi.py): one resolution's outcome, the value of one request,
and one ranked request with its reason."""

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    """Base for VOI models: unknown fields are an error and objects are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OptionOutcome(Frozen):
    """One plausible resolution (or combination, for a merged request) and what the reference policy then says."""

    option_id: str
    description: str
    prior: float
    recommendation: str | None
    metric: float | None
    changes_recommendation: bool
    size: float
    weighted: float  # prior x 1[change] x size (before the cost weight)
    error: str | None = None


class RequestValue(Frozen):
    """The value of information of one request (one or more items that share it)."""

    request_id: str
    request_params: dict[str, str]
    item_ids: tuple[str, ...]
    cost_weight: float
    current_recommendation: str
    metric_name: str
    current_metric: float | None
    outcomes: tuple[OptionOutcome, ...]
    p_change: float
    voi: float
    dropped_option_ids: tuple[str, ...] = ()
    computable: bool = True
    not_computable_reason: str | None = None


class RankedRequest(Frozen):
    """One recommended evidence request, its score and a one-sentence reason filled from the numbers.

    voi_given_others, p_change_given_others and recommendation_given_others are the LOOKAHEAD tie-breaker of a
    zero-value request (voi.py): the same score after every other open item is resolved as most likely. They are
    None when not computed (the request has a nonzero VOI, or it is the only request).
    """

    rank: int
    request_id: str
    title: str
    params: dict[str, str]
    item_ids: tuple[str, ...]
    voi: float
    p_change: float
    cost_weight: float
    options_summary: tuple[dict, ...]
    reason: str
    voi_given_others: float | None = None
    p_change_given_others: float | None = None
    recommendation_given_others: str | None = None
