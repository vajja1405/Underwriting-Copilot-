"""Shared helpers for the founder package tests (Studio S6). Everything is SYNTHETIC and offline.

Building a golden scenario and its package takes a few seconds, so the packages are built once per test process
(functools.lru_cache) with fewer simulated paths than the evaluation (TEST_PATHS). The screening handler clears
every name (no list is read, no network); the decision log goes to a temporary folder.
"""

from functools import lru_cache
from pathlib import Path

from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.package import FounderProject, Package, build_package, project_from_golden, render_pdf
from uwcopilot.tools.schemas import ListSnapshot, ScreeningCandidate, ScreeningRequest, ScreeningResult

TEST_PATHS = 300
SNAPSHOT = ListSnapshot(source="OFAC", file_name="test-fixture", sha256="c" * 64, snapshot_date="2026-10-01")


@lru_cache(maxsize=16)
def scenario(metro_id: str, profile_id: str, n_paths: int = TEST_PATHS) -> GoldenScenario:
    """A golden scenario (cached)."""
    return build_golden_scenario(metro_id, profile_id, n_paths=n_paths)


@lru_cache(maxsize=16)
def project(metro_id: str, profile_id: str, n_paths: int = TEST_PATHS) -> FounderProject:
    """The SYNTHETIC golden project of a scenario (cached)."""
    return project_from_golden(scenario(metro_id, profile_id, n_paths))


@lru_cache(maxsize=16)
def package(metro_id: str, profile_id: str, n_paths: int = TEST_PATHS) -> Package:
    """The package of a golden project (cached)."""
    return build_package(project(metro_id, profile_id, n_paths))


@lru_cache(maxsize=8)
def pdf(metro_id: str, profile_id: str, n_paths: int = TEST_PATHS) -> bytes:
    """The rendered PDF of a golden package (cached)."""
    return render_pdf(package(metro_id, profile_id, n_paths))


def screen_clear(request: ScreeningRequest) -> ScreeningResult:
    """Offline screening: every name is clear."""
    return ScreeningResult(request=request, candidates=[], status="clear", list_snapshots=[SNAPSHOT])


def screen_flagging(names: set[str]):  # noqa: ANN201 - a small factory
    """Offline screening that returns a potential match for exactly these names."""

    def screen(request: ScreeningRequest) -> ScreeningResult:
        candidates = []
        if request.name in names:
            candidates.append(
                ScreeningCandidate(
                    list_entry_id="OFAC-T1",
                    list_name="OFAC SDN (TEST)",
                    source="OFAC",
                    matched_name=request.name,
                    score=0.97,
                    explanation=["test fixture match"],
                )
            )
        return ScreeningResult(
            request=request,
            candidates=candidates,
            status="potential_match" if candidates else "clear",
            list_snapshots=[SNAPSHOT],
        )

    return screen


def offline_handlers() -> dict:
    """The only tool a startup file's plan calls, offline."""
    return {"screen_entity": screen_clear}


def log_dir(tmp_path: Path) -> Path:
    """A folder for the (never written) decision log."""
    folder = tmp_path / "decisions"
    folder.mkdir(exist_ok=True)
    return folder
