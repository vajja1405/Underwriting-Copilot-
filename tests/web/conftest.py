"""Shared fixtures for the web tests: settings built from an explicit environment, and app clients.

Nothing here touches the network or the repo's data folder: the store and the static export live in tmp_path,
and the repo .env file is skipped (env_file=None).
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uwcopilot.web.app import create_app
from uwcopilot.web.settings import WebSettings, load_web_settings

TEST_SECRET = "test-session-secret-0123456789-abcdefghij"
SettingsFactory = Callable[..., WebSettings]


@pytest.fixture
def make_settings(tmp_path: Path) -> SettingsFactory:
    """make_settings(UWC_DEV_AUTH="1", ...) -> WebSettings with the store and static folder under tmp_path."""

    def factory(**env: str) -> WebSettings:
        environ = {
            "UWC_STORE_DIR": str(tmp_path / "store"),
            "UWC_STATIC_DIR": str(tmp_path / "out"),
            "UWC_SESSION_SECRET": TEST_SECRET,
            **env,
        }
        return load_web_settings(env_file=None, environ=environ)

    return factory


@pytest.fixture
def client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    """A client for an app with default settings (no dev login, no OAuth, local store)."""
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client


@pytest.fixture
def dev_client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    """A client for an app with the dev login turned on."""
    with TestClient(create_app(make_settings(UWC_DEV_AUTH="1"))) as test_client:
        yield test_client
