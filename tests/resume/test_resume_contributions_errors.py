"""Tests for a malformed config/contributions.yaml: a ContributionsError (exit code 2), never a traceback or a
silently empty result."""

from pathlib import Path

import pytest
from resume_fixtures import fake_repo, write_results

import resume_lines
from uwcopilot.resume.contributions import ContributionsError, load_contributions, parse_contributions


def write(tmp_path: Path, text: str) -> Path:
    """A contributions file with the given text."""
    path = tmp_path / "contributions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_invalid_yaml_is_a_contributions_error(tmp_path: Path) -> None:
    path = write(tmp_path, "owners:\n  Rahul Vajja: [credit_model\n")
    with pytest.raises(ContributionsError, match="not valid YAML"):
        load_contributions(path)


def test_top_level_that_is_not_a_mapping_is_an_error() -> None:
    with pytest.raises(ContributionsError, match="top level must be a mapping"):
        parse_contributions(["Rahul Vajja", "credit_model"])
    with pytest.raises(ContributionsError, match="top level must be a mapping"):
        parse_contributions("owners")


def test_misspelled_or_extra_top_level_key_is_an_error() -> None:
    with pytest.raises(ContributionsError, match=r"unknown top-level keys \['owner'\]"):
        parse_contributions({"owner": {"Rahul Vajja": ["credit_model"]}})
    with pytest.raises(ContributionsError, match=r"unknown top-level keys \['notes'\]"):
        parse_contributions({"owners": {"Rahul Vajja": []}, "notes": "x"})


def test_empty_file_or_only_comments_stays_valid_and_empty(tmp_path: Path) -> None:
    assert load_contributions(write(tmp_path, "")).owners == {}
    assert load_contributions(write(tmp_path, "# only a comment\n# and another\n")).owners == {}
    assert parse_contributions({}) == {}


def run_main(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run scripts/resume_lines.py main() with a contributions file holding text."""
    results = write_results(tmp_path / "results")
    repo = fake_repo(tmp_path / "repo")
    path = write(tmp_path, text)
    code = resume_lines.main(["--results-dir", str(results), "--repo-root", str(repo), "--contributions", str(path)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("owners:\n  Rahul Vajja: [credit_model\n", "not valid YAML"),
        ("owner:\n  Rahul Vajja: [credit_model]\n", "unknown top-level keys"),
        ("- Rahul Vajja\n- credit_model\n", "top level must be a mapping"),
    ],
)
def test_main_exits_2_on_a_malformed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, message: str
) -> None:
    code, out, err = run_main(tmp_path, text, capsys)
    assert code == 2
    assert out == ""
    assert message in err
