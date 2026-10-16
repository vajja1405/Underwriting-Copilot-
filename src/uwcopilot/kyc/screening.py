"""screen(request) -> ScreeningResult: the sanctions-screening tool used by the agent and the MCP server.

The tool registry (uwcopilot.tools.registry) calls screen_entity(), which screens with a default matcher:
- the full watchlist from data/raw (OFAC SDN + OpenSanctions), loaded and indexed on first use (about a
  minute and a couple of GB of memory at full size), then reused for every later call in the process;
- the operating point chosen on the validation split by scripts/eval_kyc.py and stored in
  results/kyc_metrics.json under "selected_operating_point" (threshold, and the reranker weight; a weight of
  0 means rules only, and then no embedding model is loaded).

If that file is missing, screening refuses to run (ScreeningNotConfigured) instead of guessing a threshold.
Tests and demos can install a ready-made matcher with set_default_matcher() or pass one to screen().

Which lists the default matcher loads is a setting, UWC_SCREENING_LISTS (read like every project setting: a
non-empty value in the repo .env file wins over the process environment):
- unset or "all": OFAC SDN + OpenSanctions (the default; local runs and the evaluation use it);
- "ofac": the OFAC SDN and alternate-name files only. The web demo in the Hugging Face Space uses this, because the
  OpenSanctions file (about 445 MB, CC BY-NC 4.0) is not put in the container image;
- a comma-separated list of "ofac" and "opensanctions" says the same thing explicitly.
The operating point (threshold) was chosen on the full lists; how well it works on OFAC alone is not measured.
Every result names the files it was screened against (list_snapshots), so a reader always sees which lists ran.

Every result carries the list snapshots (file checksums) it was screened against. A "potential_match" result
always requires human review; nothing here can clear or confirm a match.
"""

import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from uwcopilot.common.paths import ENV_FILE, RESULTS_DIR
from uwcopilot.common.settings import env_reader
from uwcopilot.kyc.matcher import MatcherConfig, SanctionsMatcher
from uwcopilot.kyc.watchlist import load_watchlist
from uwcopilot.tools.schemas import ScreeningRequest, ScreeningResult

SELECTED_CONFIG_FILE = RESULTS_DIR / "kyc_metrics.json"
SCREENING_LISTS_VARIABLE = "UWC_SCREENING_LISTS"
# The list families the default matcher can load, in the order load_watchlist() reads them.
SCREENING_LIST_NAMES: tuple[str, ...] = ("ofac", "opensanctions")
ALL_LISTS = "all"

_default_matcher: SanctionsMatcher | None = None
_default_matcher_lock = threading.Lock()


class ScreeningNotConfigured(RuntimeError):
    """Raised when no validated threshold exists yet (run `make eval-kyc` first)."""


class ScreeningListsError(ValueError):
    """UWC_SCREENING_LISTS names a list that does not exist (or names none)."""


def parse_screening_lists(raw: str | None) -> tuple[str, ...]:
    """The list families to load: None or "all" -> every family; "ofac" -> ("ofac",); "ofac, opensanctions" -> both.

    Names are compared without case. An unknown name, or an empty list, is an error (never silently ignored).
    """
    if raw is None or raw.strip().lower() == ALL_LISTS:
        return SCREENING_LIST_NAMES
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(names) - set(SCREENING_LIST_NAMES))
    if unknown or not names:
        raise ScreeningListsError(
            f"{SCREENING_LISTS_VARIABLE} must be '{ALL_LISTS}' or a comma-separated list of "
            f"{list(SCREENING_LIST_NAMES)} (got {raw!r})"
        )
    return tuple(name for name in SCREENING_LIST_NAMES if name in names)


def configured_screening_lists(
    env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """The list families named by UWC_SCREENING_LISTS (every family when it is unset)."""
    return parse_screening_lists(env_reader(env_file, environ)(SCREENING_LISTS_VARIABLE))


def watchlist_source_arguments(lists: Sequence[str]) -> dict[str, Any]:
    """load_watchlist() keyword arguments that leave out the families not in lists (None skips a source).

    A family that is kept is not passed at all, so load_watchlist() reads it from its default file.
    """
    arguments: dict[str, Any] = {}
    if "ofac" not in lists:
        arguments.update(ofac_sdn=None, ofac_alt=None)
    if "opensanctions" not in lists:
        arguments["opensanctions"] = None
    return arguments


def load_selected_operating_point(path: Path = SELECTED_CONFIG_FILE) -> dict:
    """Read the threshold and reranker weight chosen on the validation split."""
    if not path.exists():
        raise ScreeningNotConfigured(f"{path} not found. Run `make kyc-data` and `make eval-kyc` first.")
    point = json.loads(path.read_text(encoding="utf-8")).get("selected_operating_point")
    if not point or "threshold" not in point:
        raise ScreeningNotConfigured(f"{path} has no selected_operating_point. Run `make eval-kyc` again.")
    return point


def config_from_operating_point(point: dict) -> MatcherConfig:
    """MatcherConfig with the validated threshold and reranker weight (other settings stay at their defaults)."""
    return MatcherConfig(threshold=float(point["threshold"]), reranker_weight=float(point.get("reranker_weight", 0.0)))


def build_default_matcher(
    config_file: Path = SELECTED_CONFIG_FILE, lists: Sequence[str] | None = None
) -> SanctionsMatcher:
    """Load the watchlist and the validated settings; load the embedding model only if it is used.

    lists: the list families to load (see parse_screening_lists); None reads UWC_SCREENING_LISTS, whose default is
    every family.
    """
    chosen = configured_screening_lists() if lists is None else parse_screening_lists(",".join(lists))
    config = config_from_operating_point(load_selected_operating_point(config_file))
    embedder = None
    if config.reranker_weight > 0:
        from uwcopilot.kyc.reranker import FastEmbedEmbedder  # loads onnxruntime only when needed

        embedder = FastEmbedEmbedder()
    return SanctionsMatcher(load_watchlist(**watchlist_source_arguments(chosen)), config, embedder)


def set_default_matcher(matcher: SanctionsMatcher | None) -> None:
    """Install the matcher that screen_entity() uses (None resets it, so the next call rebuilds it)."""
    global _default_matcher
    with _default_matcher_lock:
        _default_matcher = matcher


def default_matcher() -> SanctionsMatcher:
    """Return the shared matcher, building it once on first use."""
    global _default_matcher
    with _default_matcher_lock:
        if _default_matcher is None:
            _default_matcher = build_default_matcher()
        return _default_matcher


def screen(request: ScreeningRequest | dict, matcher: SanctionsMatcher | None = None) -> ScreeningResult:
    """Screen one name. A dict is validated as a ScreeningRequest first (unknown fields are rejected)."""
    validated = request if isinstance(request, ScreeningRequest) else ScreeningRequest.model_validate(request)
    return (matcher or default_matcher()).screen(validated)


def screen_entity(request: ScreeningRequest) -> ScreeningResult:
    """Handler for the allow-listed "screen_entity" tool (see uwcopilot.tools.registry)."""
    return screen(request)
