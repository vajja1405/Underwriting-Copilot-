"""pytest setup for tests/studio: registers the "slow" marker of the full golden package run (Studio S6).

Tests marked slow are also skipped unless UWC_RUN_SLOW_TESTS=1 is set when pytest starts (read at collection, before
the root conftest clears UWC_* variables for each test); `make studio-package-golden` sets it.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the slow marker so pytest does not warn about it."""
    config.addinivalue_line("markers", "slow: the full 36-scenario golden package run (UWC_RUN_SLOW_TESTS=1)")
