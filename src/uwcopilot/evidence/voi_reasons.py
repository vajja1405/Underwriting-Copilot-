"""One-sentence reasons for ranked evidence requests, filled from the numbers the calculations produced.

Examples of the sentences (every number comes from the reference policy's calculations, never from an LLM):
- 'Requesting "Missing bank statement month" for 2026-03 could move the decision DSCR from 1.05 to 1.32, which
  changes refer -> approve (probability 0.60 under the documented priors).'
- 'Requesting "Updated debt schedule" has zero decision value: under every plausible resolution the reference
  recommendation stays refer.'
- 'Requesting "Corrected application" has zero decision value on its own (the other open items keep the
  recommendation at refer); with them resolved as most likely, it could change approve -> refer (probability 0.30
  under the documented priors).'
A request is named by its catalog title plus ids and codes (a month, a checklist code); a carrier name, which is
document text, is never written into a sentence ("from the named carrier").
"""

from uwcopilot.evidence.requests import request_catalog
from uwcopilot.evidence.voi_models import OptionOutcome, RequestValue

PRIORS_NOTE = "under the documented priors"


def format_metric(name: str, value: float | None) -> str:
    """A decision metric for a sentence: a loss ratio as a percent with 1 decimal, a DSCR with 2 decimals."""
    if value is None:
        return "not available"
    if "loss ratio" in name:
        return f"{value:.1f}%"
    return f"{value:.2f}"


def request_phrase(value: RequestValue) -> str:
    """The quoted catalog title plus what is asked for (ids and codes only, never document text)."""
    phrase = f'"{request_catalog()[value.request_id].title}"'
    params = value.request_params
    if "month" in params:
        phrase += f" for {params['month']}"
    if "document" in params:
        phrase += f" ({params['document']})"
    if "carrier" in params:
        phrase += " from the named carrier"
    return phrase


def representative(value: RequestValue) -> OptionOutcome | None:
    """The changing resolution with the highest prior (the first one on a tie)."""
    changing = [outcome for outcome in value.outcomes if outcome.changes_recommendation]
    if not changing:
        return None
    return max(changing, key=lambda outcome: outcome.prior)


def change_clause(value: RequestValue) -> str | None:
    """'could move the <metric> from A to B, which changes X -> Y (probability p ...)' or 'could change X -> Y
    (probability p ...)'; None when no resolution changes the recommendation."""
    example = representative(value)
    if example is None:
        return None
    now = value.current_recommendation
    probability = f"(probability {value.p_change:.2f} {PRIORS_NOTE})"
    if value.current_metric is not None and example.metric is not None and example.metric != value.current_metric:
        before = format_metric(value.metric_name, value.current_metric)
        after = format_metric(value.metric_name, example.metric)
        return (
            f"could move the {value.metric_name} from {before} to {after}, which changes {now} -> "
            f"{example.recommendation} {probability}"
        )
    return f"could change the recommendation {now} -> {example.recommendation} {probability}"


def reason_sentence(value: RequestValue, given_others: RequestValue | None = None) -> str:
    """The one-sentence reason of a request (given_others: its lookahead value, for a zero-value request)."""
    phrase = request_phrase(value)
    if not value.computable:
        why = value.not_computable_reason or "no plausible resolution could be built from the case values"
        return f"{phrase[0].upper()}{phrase[1:]} is not scored: {why}."
    clause = change_clause(value)
    if clause is not None:
        return f"Requesting {phrase} {clause}."
    lookahead = change_clause(given_others) if given_others is not None else None
    if lookahead is not None:
        return (
            f"Requesting {phrase} has zero decision value on its own (the other open items keep the recommendation "
            f"at {value.current_recommendation}); with them resolved as most likely, it {lookahead}."
        )
    return (
        f"Requesting {phrase} has zero decision value: under every plausible resolution the reference "
        f"recommendation stays {value.current_recommendation}."
    )


def options_summary(value: RequestValue) -> tuple[dict, ...]:
    """A compact list of the resolutions: id, prior, recommendation, metric, change, error."""
    return tuple(
        {
            "option_id": outcome.option_id,
            "prior": outcome.prior,
            "recommendation": outcome.recommendation,
            "metric": outcome.metric,
            "changes_recommendation": outcome.changes_recommendation,
            "error": outcome.error,
        }
        for outcome in value.outcomes
    )
