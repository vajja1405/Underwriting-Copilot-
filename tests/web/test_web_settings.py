"""Tests for web settings: defaults, the dev-login refusal inside a Space, secrets and the role allowlist."""

import logging

import pytest

from uwcopilot import studio
from uwcopilot.web.crypto import generate_key
from uwcopilot.web.routers import system
from uwcopilot.web.settings import (
    DISCLAIMER,
    PRODUCT_NAME,
    WebSettingsError,
    load_web_settings,
    parse_role_allowlist,
)

SECRET = "x" * 40
SPACE_ENV = {"SPACE_ID": "astra6/fundproof", "SPACE_HOST": "astra6-fundproof.hf.space", "UWC_SESSION_SECRET": SECRET}


def test_defaults_outside_a_space() -> None:
    settings = load_web_settings(env_file=None, environ={})
    assert settings.product_name == PRODUCT_NAME == "Fundproof"
    assert not settings.is_space and not settings.dev_auth and settings.oauth is None
    assert settings.store_backend == "local"
    assert settings.session_secret_generated  # no secret given: a random one, with a warning
    assert len(settings.session_secret.get_secret_value()) >= 32
    assert settings.trusted_proxy_hops == 0
    assert settings.space_url is None


def test_secrets_are_hidden_in_repr() -> None:
    key = generate_key()
    settings = load_web_settings(env_file=None, environ={"UWC_SESSION_SECRET": SECRET, "UWC_FERNET_KEY": key})
    assert SECRET not in repr(settings) and key not in repr(settings)


def test_dev_auth_turns_on_outside_a_space() -> None:
    assert load_web_settings(env_file=None, environ={"UWC_DEV_AUTH": "1"}).dev_auth


def test_dev_auth_is_refused_inside_a_space() -> None:
    with pytest.raises(WebSettingsError, match="refused inside a Hugging Face Space"):
        load_web_settings(env_file=None, environ={**SPACE_ENV, "UWC_DEV_AUTH": "1"})


def test_dev_auth_accepts_only_0_or_1() -> None:
    with pytest.raises(WebSettingsError, match="UWC_DEV_AUTH"):
        load_web_settings(env_file=None, environ={"UWC_DEV_AUTH": "yes"})


def test_a_space_needs_a_session_secret() -> None:
    environ = {key: value for key, value in SPACE_ENV.items() if key != "UWC_SESSION_SECRET"}
    with pytest.raises(WebSettingsError, match="UWC_SESSION_SECRET is required"):
        load_web_settings(env_file=None, environ=environ)


def test_short_session_secret_is_refused() -> None:
    with pytest.raises(WebSettingsError, match="at least 32"):
        load_web_settings(env_file=None, environ={"UWC_SESSION_SECRET": "short"})


def test_space_mode_values() -> None:
    settings = load_web_settings(env_file=None, environ=SPACE_ENV)
    assert settings.is_space and settings.space_url == "https://astra6-fundproof.hf.space"
    assert settings.trusted_proxy_hops == 1
    assert settings.oauth is None  # the Space did not inject OAuth variables


def test_oauth_settings_from_the_space_variables() -> None:
    environ = {
        **SPACE_ENV,
        "OAUTH_CLIENT_ID": "client-id",
        "OAUTH_CLIENT_SECRET": "client-secret",
        "OPENID_PROVIDER_URL": "https://huggingface.co/",
    }
    oauth = load_web_settings(env_file=None, environ=environ).oauth
    assert oauth is not None
    assert oauth.redirect_uri == "https://astra6-fundproof.hf.space/api/auth/callback"
    assert oauth.provider_url == "https://huggingface.co"
    assert oauth.scopes == "openid profile"
    assert "client-secret" not in repr(oauth)


def test_role_allowlist_parsing() -> None:
    allowlist = parse_role_allowlist(" Alice:lender_reviewer, bob:admin, alice:lender_senior ,")
    assert allowlist == {"alice": ("lender_reviewer", "lender_senior"), "bob": ("admin",)}
    assert parse_role_allowlist(None) == {}


def test_role_allowlist_entries_pinned_to_an_account_id() -> None:
    allowlist = parse_role_allowlist("Alice@6123abc:lender_senior, alice@6123abc:admin, bob:admin")
    assert allowlist == {"alice@6123abc": ("lender_senior", "admin"), "bob": ("admin",)}


def test_unpinned_allowlist_entries_are_warned_about_in_a_space(caplog: pytest.LogCaptureFixture) -> None:
    environ = dict(SPACE_ENV)
    with caplog.at_level(logging.WARNING, logger="uwcopilot.web.settings"):
        load_web_settings(env_file=None, environ={**environ, "UWC_ROLE_ALLOWLIST": "astra6@6123abc:admin"})
        assert "without an account id" not in caplog.text
        load_web_settings(env_file=None, environ={**environ, "UWC_ROLE_ALLOWLIST": "astra6:admin"})
        assert "without an account id" in caplog.text


@pytest.mark.parametrize(
    "raw", ["alice", "alice:founder", "alice:superuser", "bad name:admin", "alice@:admin", "alice@bad id:admin"]
)
def test_role_allowlist_rejects_bad_entries(raw: str) -> None:
    with pytest.raises(WebSettingsError):
        parse_role_allowlist(raw)


def test_dataset_store_needs_repo_token_and_key() -> None:
    base = {"UWC_STORE": "hf_dataset"}
    with pytest.raises(WebSettingsError, match="UWC_HF_DATASET_REPO"):
        load_web_settings(env_file=None, environ=base)
    with pytest.raises(WebSettingsError, match="HF_TOKEN"):
        load_web_settings(env_file=None, environ={**base, "UWC_HF_DATASET_REPO": "astra6/fundproof-data"})
    with_token = {**base, "UWC_HF_DATASET_REPO": "astra6/fundproof-data", "HF_TOKEN": "hf_test"}
    with pytest.raises(WebSettingsError, match="UWC_FERNET_KEY"):
        load_web_settings(env_file=None, environ=with_token)
    settings = load_web_settings(env_file=None, environ={**with_token, "UWC_FERNET_KEY": generate_key()})
    assert settings.store_backend == "hf_dataset" and len(settings.fernet_key_bytes) == 1


def test_invalid_values_are_refused() -> None:
    with pytest.raises(WebSettingsError, match="not a valid Fernet key"):
        load_web_settings(env_file=None, environ={"UWC_FERNET_KEY": "not-a-key"})
    with pytest.raises(WebSettingsError, match="must be an integer"):
        load_web_settings(env_file=None, environ={"UWC_MAX_BODY_BYTES": "ten"})
    with pytest.raises(WebSettingsError, match="invalid web setting"):
        load_web_settings(env_file=None, environ={"UWC_RATE_LIMIT_IP_BURST": "0"})
    with pytest.raises(WebSettingsError, match="UWC_STORE"):
        load_web_settings(env_file=None, environ={"UWC_STORE": "s3"})


def test_labels_match_the_studio_package() -> None:
    assert DISCLAIMER == studio.DISCLAIMER
    assert system.SYNTHETIC_LABEL == studio.SYNTHETIC_LABEL
    assert system.SIMULATION_LABEL == studio.SIMULATION_LABEL
