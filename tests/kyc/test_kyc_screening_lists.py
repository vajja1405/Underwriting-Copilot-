"""Tests for the UWC_SCREENING_LISTS setting: which watchlist families the default matcher loads.

The default (unset) must stay "every list", so local runs and the evaluation are unchanged; "ofac" leaves the
OpenSanctions file out (the web demo in the Space). No data/raw file is read: load_watchlist is replaced.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from uwcopilot.kyc import screening
from uwcopilot.kyc.screening import (
    SCREENING_LIST_NAMES,
    ScreeningListsError,
    build_default_matcher,
    configured_screening_lists,
    parse_screening_lists,
    watchlist_source_arguments,
)


def test_unset_and_all_mean_every_list() -> None:
    assert parse_screening_lists(None) == SCREENING_LIST_NAMES == ("ofac", "opensanctions")
    assert parse_screening_lists("all") == SCREENING_LIST_NAMES
    assert parse_screening_lists(" ALL ") == SCREENING_LIST_NAMES


def test_ofac_only_and_explicit_lists() -> None:
    assert parse_screening_lists("ofac") == ("ofac",)
    assert parse_screening_lists("OFAC") == ("ofac",)
    assert parse_screening_lists("opensanctions, ofac") == ("ofac", "opensanctions")


@pytest.mark.parametrize("raw", ["", " , ", "ofac,eu", "un"])
def test_unknown_or_empty_lists_are_refused(raw: str) -> None:
    with pytest.raises(ScreeningListsError):
        parse_screening_lists(raw)


def test_setting_is_read_from_the_environment_and_defaults_to_every_list() -> None:
    assert configured_screening_lists(env_file=None, environ={}) == SCREENING_LIST_NAMES
    assert configured_screening_lists(env_file=None, environ={"UWC_SCREENING_LISTS": "ofac"}) == ("ofac",)


def test_dotenv_value_wins_over_the_process_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("UWC_SCREENING_LISTS=ofac\n", encoding="utf-8")
    assert configured_screening_lists(env_file=env_file, environ={"UWC_SCREENING_LISTS": "all"}) == ("ofac",)


def test_source_arguments_skip_only_the_left_out_families() -> None:
    assert watchlist_source_arguments(("ofac", "opensanctions")) == {}
    assert watchlist_source_arguments(("ofac",)) == {"opensanctions": None}
    assert watchlist_source_arguments(("opensanctions",)) == {"ofac_sdn": None, "ofac_alt": None}


@pytest.fixture
def operating_point(tmp_path: Path) -> Path:
    """A results file with a rules-only operating point (no embedding model is loaded)."""
    path = tmp_path / "kyc_metrics.json"
    path.write_text(json.dumps({"selected_operating_point": {"threshold": 0.9, "reranker_weight": 0.0}}))
    return path


def capture_watchlist_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace load_watchlist and SanctionsMatcher in the screening module; return the recorded call arguments."""
    calls: list[dict[str, Any]] = []

    def fake_load_watchlist(**kwargs: Any) -> str:
        """Record which sources were asked for."""
        calls.append(kwargs)
        return "watchlist"

    monkeypatch.setattr(screening, "load_watchlist", fake_load_watchlist)
    monkeypatch.setattr(screening, "SanctionsMatcher", lambda watchlist, config, embedder: (watchlist, config))
    return calls


def test_default_matcher_loads_every_list_when_the_setting_is_unset(
    monkeypatch: pytest.MonkeyPatch, operating_point: Path
) -> None:
    calls = capture_watchlist_calls(monkeypatch)
    monkeypatch.setattr(screening, "configured_screening_lists", lambda: SCREENING_LIST_NAMES)
    build_default_matcher(operating_point)
    assert calls == [{}]


def test_default_matcher_leaves_opensanctions_out_for_ofac(
    monkeypatch: pytest.MonkeyPatch, operating_point: Path
) -> None:
    calls = capture_watchlist_calls(monkeypatch)
    monkeypatch.setattr(screening, "configured_screening_lists", lambda: ("ofac",))
    build_default_matcher(operating_point)
    assert calls == [{"opensanctions": None}]


def test_explicit_lists_argument_wins_over_the_setting(monkeypatch: pytest.MonkeyPatch, operating_point: Path) -> None:
    calls = capture_watchlist_calls(monkeypatch)
    monkeypatch.setattr(screening, "configured_screening_lists", lambda: SCREENING_LIST_NAMES)
    build_default_matcher(operating_point, lists=["ofac"])
    assert calls == [{"opensanctions": None}]
    with pytest.raises(ScreeningListsError):
        build_default_matcher(operating_point, lists=[])
