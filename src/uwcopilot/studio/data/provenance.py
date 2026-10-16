"""SourceRecord: where one value came from. Every connector returns its values together with one of these.

The evidence ledger (Studio S4) stores these records, so a founder's number can always be traced back to a public
dataset row (publisher, dataset, URL, query, request date, SHA-256 of the response) or to the SYNTHETIC generator.

Secrets never enter a SourceRecord: connectors pass only the public query parameters (an API key is removed before
the record is built), and build_record() refuses parameter names that look like secrets.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

DataLabel = Literal["PUBLIC", "SYNTHETIC"]
# How this run obtained the response: a live request, the on-disk cache, or the SYNTHETIC generator.
Origin = Literal["live", "cache", "synthetic"]
# Parameter names that carry secrets in the APIs Studio calls. They must never be stored.
SECRET_PARAM_NAMES: frozenset[str] = frozenset({"key", "api_key", "apikey", "registrationkey", "token", "access_token"})
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SourceRecord(BaseModel):
    """Provenance of one fetched (or generated) dataset response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, description="Short id of the connector, e.g. 'census_acs'")
    publisher: str = Field(min_length=1)
    dataset: str = Field(min_length=1, description="Dataset or table name, with its vintage when known")
    url: str = Field(min_length=1, description="Request URL without the query string and without any secret")
    params: dict[str, str] = Field(default_factory=dict, description="Public query parameters only")
    retrieved_at: datetime = Field(description="When the response was first downloaded (UTC)")
    sha256: str = Field(pattern=SHA256_PATTERN, description="SHA-256 of the response body")
    license_note: str = Field(min_length=1)
    data_label: DataLabel = "PUBLIC"
    origin: Origin = "live"
    vintage: str | None = None
    geography: str | None = Field(default=None, description="Geography the values describe, e.g. a tract GEOID")

    @field_validator("params")
    @classmethod
    def no_secret_params(cls, params: dict[str, str]) -> dict[str, str]:
        """Refuse any parameter whose name is a known secret name (the value would end up in results files)."""
        leaked = sorted(name for name in params if name.lower() in SECRET_PARAM_NAMES)
        if leaked:
            raise ValueError(f"secret parameters must be removed before building a SourceRecord: {leaked}")
        return params

    @field_validator("retrieved_at")
    @classmethod
    def must_be_utc(cls, value: datetime) -> datetime:
        """Request dates are stored in UTC so they compare across machines."""
        if value.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware (UTC)")
        return value.astimezone(UTC)


def public_params(params: Mapping[str, object] | None, secret_names: frozenset[str] = SECRET_PARAM_NAMES) -> dict:
    """A copy of the query parameters without secret names, with every value as a string."""
    return {str(name): str(value) for name, value in (params or {}).items() if str(name).lower() not in secret_names}


class SourceInfo(BaseModel):
    """The fixed facts about one connector's source; .record(...) turns a fetched response into a SourceRecord."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    publisher: str
    dataset: str
    license_note: str

    def record(self, fetched: "FetchLike", vintage: str | None = None, geography: str | None = None) -> SourceRecord:
        """A SourceRecord for one response fetched with uwcopilot.studio.data.http.PoliteClient."""
        return SourceRecord(
            source_id=self.source_id,
            publisher=self.publisher,
            dataset=self.dataset,
            url=fetched.url,
            params=public_params(fetched.params),
            retrieved_at=fetched.retrieved_at,
            sha256=fetched.sha256,
            license_note=self.license_note,
            data_label="PUBLIC",
            origin="cache" if fetched.from_cache else "live",
            vintage=vintage,
            geography=geography,
        )


class FetchLike(Protocol):
    """The fields of http.FetchResult that a SourceRecord needs (a Protocol avoids an import cycle)."""

    url: str
    params: dict[str, str]
    retrieved_at: datetime
    sha256: str
    from_cache: bool
