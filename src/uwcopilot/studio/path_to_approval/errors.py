"""Errors of the path-to-approval search.

GuardrailViolation: a change that is not a plan choice (a fact, an assumption range, the site's demand estimate, the
note rate, a verified figure, a site in another metro) or a change-set that would edit anything outside its levers'
own fields. The search never produces one; it is raised when code or a request tries.
LeverError: a valid lever with an unusable value (outside its bounds, not available for this plan, unknown site).
"""


class GuardrailViolation(ValueError):
    """The change would misstate a fact or edit something that is not a plan choice."""


class LeverError(ValueError):
    """A plan-choice lever with a value or combination the plan cannot take."""


class OutOfBounds(LeverError):
    """A lever value, or an amount it moves, falls outside the bounds."""
