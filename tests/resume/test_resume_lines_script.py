"""Tests for scripts/resume_lines.py: stdout holds the bullets, stderr what was left out; bad config exits 2."""

from pathlib import Path

import pytest
from resume_fixtures import fake_repo, write_results

import resume_lines


def run(tmp_path: Path, contributions: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run main() on the fixture results folder with the given contributions file text."""
    results = write_results(tmp_path / "results")
    repo = fake_repo(tmp_path / "repo")
    path = tmp_path / "contributions.yaml"
    path.write_text(contributions, encoding="utf-8")
    code = resume_lines.main(["--results-dir", str(results), "--repo-root", str(repo), "--contributions", str(path)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_main_prints_bullets_and_omissions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(tmp_path, "owners:\n  Rahul Vajja: []\n  Bhavani Adula: []\n", capsys)
    assert code == 0
    assert out.startswith("Projects\n")
    assert "- Credit default model on Lending Club loans" in out
    assert "Per-person lines need the owners to fill in config/contributions.yaml" in out
    assert "Left out (not measured or not claimable):" in err
    assert "Left out" not in out


def test_main_prints_per_person_lines_for_listed_components(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(tmp_path, "owners:\n  Bhavani Adula: [studio_siteiq]\n", capsys)
    assert code == 0
    person = out.split("Per-person lines (from config/contributions.yaml)")[1]
    assert "Bhavani Adula\n- SiteIQ site scoring from public data" in person
    assert "Credit default model" not in person


def test_main_exits_2_on_an_unknown_component(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(tmp_path, "owners:\n  Rahul Vajja: [all_of_it]\n", capsys)
    assert code == 2
    assert out == ""
    assert "unknown component ids" in err
