"""SourceRecord provenance, connector errors and API-key handling."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from uwcopilot.studio.data.errors import MissingApiKey
from uwcopilot.studio.data.http import FetchResult
from uwcopilot.studio.data.keys import KEY_PURPOSES, key_status, read_key, require_key
from uwcopilot.studio.data.provenance import SourceInfo, SourceRecord, public_params

SHA = "a" * 64
INFO = SourceInfo(source_id="x", publisher="P", dataset="D", license_note="to verify")


def fetched(from_cache: bool = False) -> FetchResult:
    return FetchResult(b"{}", SHA, datetime(2026, 10, 12, tzinfo=UTC), "https://h/x", {"a": "1"}, 200, "", from_cache)


def test_record_from_a_fetch() -> None:
    record = INFO.record(fetched(), vintage="2024", geography="48201")
    assert record.origin == "live" and record.sha256 == SHA and record.params == {"a": "1"}
    assert INFO.record(fetched(from_cache=True)).origin == "cache"
    assert record.data_label == "PUBLIC"


def test_record_refuses_secret_parameter_names() -> None:
    with pytest.raises(ValidationError, match="secret"):
        SourceRecord(
            source_id="x",
            publisher="P",
            dataset="D",
            url="u",
            params={"api_key": "v"},
            retrieved_at=datetime.now(UTC),
            sha256=SHA,
            license_note="n",
        )


def test_record_needs_utc_time_and_a_real_sha256() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        SourceRecord(
            source_id="x",
            publisher="P",
            dataset="D",
            url="u",
            retrieved_at=datetime(2026, 1, 1),
            sha256=SHA,
            license_note="n",
        )
    with pytest.raises(ValidationError):
        SourceRecord(
            source_id="x",
            publisher="P",
            dataset="D",
            url="u",
            retrieved_at=datetime.now(UTC),
            sha256="abc",
            license_note="n",
        )


def test_public_params_drops_secrets() -> None:
    assert public_params({"key": "s", "Token": "t", "q": 3}) == {"q": "3"}


def test_missing_key_names_the_variable_and_never_a_value() -> None:
    with pytest.raises(MissingApiKey, match="CENSUS_API_KEY") as caught:
        require_key("CENSUS_API_KEY", env_file=None, environ={})
    assert caught.value.variable == "CENSUS_API_KEY"


def test_key_is_read_from_the_environment() -> None:
    assert require_key("FRED_API_KEY", env_file=None, environ={"FRED_API_KEY": " abc "}) == "abc"
    assert read_key("FRED_API_KEY", env_file=None, environ={"FRED_API_KEY": "  "}) is None


def test_env_file_wins(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("HUD_USER_TOKEN=from_file\n", encoding="utf-8")
    assert read_key("HUD_USER_TOKEN", env_file=env, environ={"HUD_USER_TOKEN": "from_env"}) == "from_file"


def test_unknown_key_name_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown Studio key"):
        read_key("AWS_SECRET_ACCESS_KEY", env_file=None, environ={})


def test_key_status_reports_presence_only() -> None:
    status = key_status(env_file=None, environ={"BLS_API_KEY": "zzz"})
    assert set(status) == set(KEY_PURPOSES)
    assert status["BLS_API_KEY"] is True and status["CENSUS_API_KEY"] is False
    assert "zzz" not in str(status)
