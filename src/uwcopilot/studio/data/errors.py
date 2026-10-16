"""The errors a Studio data connector can raise. Each message says what is missing and how to fix it.

A connector never returns a guessed value. When it cannot answer it raises one of these, and the caller marks the
factor UNKNOWN (Studio addendum rule G).
"""


class StudioDataError(Exception):
    """Base class for every Studio data error."""


class NotAvailable(StudioDataError):
    """No documented dataset exists for this request (for example no state traffic adapter yet), or the source
    returned no data for this place. reason is shown to the user as the reason the factor is UNKNOWN."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MissingApiKey(StudioDataError):
    """A connector needs a free API key that is not set. The key's value is never logged or stored."""

    def __init__(self, variable: str, purpose: str) -> None:
        message = (
            f"{variable} is not set; it is needed for {purpose}. Register a free key, put it in .env "
            f"(see .env.example) and start a new shell or session so the variable is loaded."
        )
        super().__init__(message)
        self.variable = variable


class InvalidApiKey(StudioDataError):
    """The host rejected a key that IS set (for example Census redirects to invalid_key.html).

    reason is the host's own word for it ("invalid_key" or "missing_key"); the key's value is never included.
    """

    def __init__(self, variable: str, host: str, reason: str) -> None:
        message = (
            f"{host} rejected {variable} ({reason}): the key is set but the host says it is not valid. "
            "Check that the key was activated (Census sends an activation link by email after sign-up) and copied "
            "exactly, then rerun with --refresh (`make studio-data-refresh METRO=...`)."
        )
        super().__init__(message)
        self.variable = variable
        self.host = host
        self.reason = reason


class HostUnreachable(StudioDataError):
    """The host could not be reached after the retries (blocked network, DNS failure, timeout, reset)."""

    def __init__(self, host: str, detail: str) -> None:
        super().__init__(f"Cannot reach {host}: {detail}")
        self.host = host
        self.detail = detail


class UpstreamError(StudioDataError):
    """The host answered with an error status, or with a body the connector cannot read."""

    def __init__(self, host: str, detail: str, status_code: int | None = None) -> None:
        prefix = f"{host} answered HTTP {status_code}" if status_code is not None else f"{host} answered"
        super().__init__(f"{prefix}: {detail}")
        self.host = host
        self.status_code = status_code
        self.detail = detail


class CacheCorrupted(StudioDataError):
    """A cached body no longer matches the SHA-256 recorded when it was downloaded."""
