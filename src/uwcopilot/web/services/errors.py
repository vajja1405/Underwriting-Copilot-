"""Errors the web services raise; each carries the HTTP status the routers answer with.

The services do not import FastAPI. A router catches ServiceError and turns it into an HTTP error with the same
status and message (routers/studio.py and routers/lender.py, function `http_error`).
"""


class ServiceError(Exception):
    """Base class: an HTTP status code and a message for the person using the app."""

    status_code = 500

    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        """message is shown to the user; reasons (optional) list the individual problems."""
        super().__init__(message)
        self.message = message
        self.reasons = list(reasons or [])


class InputError(ServiceError):
    """The request is well-formed but its values cannot be used (422)."""

    status_code = 422


class NotFound(ServiceError):
    """No such object for this user (404; another user's object is also "not found")."""

    status_code = 404


class Conflict(ServiceError):
    """The request does not fit the current state (409), for example limited mode or a stale version."""

    status_code = 409


class Unavailable(ServiceError):
    """A dependency (an upstream API, the compute pool) cannot answer right now (503)."""

    status_code = 503
