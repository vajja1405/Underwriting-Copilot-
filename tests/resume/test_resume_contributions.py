"""Tests for uwcopilot.resume.contributions: per-person lines only for components listed by the owners."""

from pathlib import Path

import pytest
from resume_fixtures import fake_repo, write_results

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.resume.contributions import (
    COMPONENTS,
    FILL_IN_NOTE,
    Contributions,
    ContributionsError,
    load_contributions,
    parse_contributions,
    render_per_person,
)
from uwcopilot.resume.report import ResumeReport, build_report


@pytest.fixture()
def report(tmp_path: Path) -> ResumeReport:
    """Every bullet built from the complete fixture results folder."""
    return build_report(write_results(tmp_path / "results"), fake_repo(tmp_path / "repo"))


def test_committed_file_is_empty_by_default_and_explains_why() -> None:
    path = CONFIG_DIR / "contributions.yaml"
    contributions = load_contributions(path)
    assert contributions.found
    assert set(contributions.owners) == {"Rahul Vajja", "Bhavani Adula"}
    assert all(components == () for components in contributions.owners.values())
    text = path.read_text(encoding="utf-8")
    assert "git history cannot say who built what" in text
    for component in COMPONENTS:
        assert component in text


def test_component_ids_match_the_bullets(report: ResumeReport) -> None:
    assert [bullet.bullet_id for bullet in report.bullets] == list(COMPONENTS)


def test_empty_file_prints_the_fill_in_note_and_no_person_bullets(report: ResumeReport) -> None:
    empty = Contributions(owners={"Rahul Vajja": (), "Bhavani Adula": ()}, found=True)
    lines, notes = render_per_person(empty, report.bullets, report.omissions)
    assert FILL_IN_NOTE in lines
    assert not any(line.startswith("- ") for line in lines)
    assert notes == []


def test_missing_file_counts_as_empty(tmp_path: Path, report: ResumeReport) -> None:
    contributions = load_contributions(tmp_path / "absent.yaml")
    assert contributions == Contributions(owners={}, found=False)
    lines, notes = render_per_person(contributions, report.bullets, report.omissions)
    assert FILL_IN_NOTE in lines
    assert notes == ["config/contributions.yaml does not exist: no per-person lines"]


def test_each_person_gets_only_the_listed_components(tmp_path: Path, report: ResumeReport) -> None:
    path = tmp_path / "contributions.yaml"
    path.write_text(
        "owners:\n  Rahul Vajja: [credit_model, studio_siteiq]\n  Bhavani Adula: [studio_siteiq]\n",
        encoding="utf-8",
    )
    lines, notes = render_per_person(load_contributions(path), report.bullets, report.omissions)
    rahul = lines[lines.index("Rahul Vajja") + 1 : lines.index("Bhavani Adula")]
    bhavani = lines[lines.index("Bhavani Adula") + 1 :]
    by_id = {bullet.bullet_id: bullet.text for bullet in report.bullets}
    assert rahul[:2] == [f"- {by_id['credit_model']}", f"- {by_id['studio_siteiq']} [joint with Bhavani Adula]"]
    assert bhavani == [f"- {by_id['studio_siteiq']} [joint with Rahul Vajja]"]
    assert not any(by_id["kyc_screening"] in line for line in lines)
    assert notes == []


def test_listed_component_without_a_bullet_goes_to_stderr(tmp_path: Path) -> None:
    results = write_results(tmp_path / "results")
    (results / "kyc_metrics.json").unlink()
    report = build_report(results, fake_repo(tmp_path / "repo"))
    contributions = Contributions(owners={"Rahul Vajja": ("kyc_screening",)}, found=True)
    lines, notes = render_per_person(contributions, report.bullets, report.omissions)
    assert not any(line.startswith("- Sanctions") for line in lines)
    assert len(notes) == 1 and notes[0].startswith("Rahul Vajja: kyc_screening has no bullet")


def test_unknown_component_or_bad_shape_is_an_error() -> None:
    with pytest.raises(ContributionsError, match="unknown component ids"):
        parse_contributions({"owners": {"Rahul Vajja": ["credit_model", "everything"]}})
    with pytest.raises(ContributionsError):
        parse_contributions({"owners": ["Rahul Vajja"]})
    with pytest.raises(ContributionsError):
        parse_contributions({"owners": {"Rahul Vajja": "credit_model"}})


def test_parse_keeps_file_order_and_drops_repeats() -> None:
    parsed = parse_contributions({"owners": {"B": ["studio_path", "credit_model", "studio_path"], "A": None}})
    assert parsed == {"B": ("studio_path", "credit_model"), "A": ()}
    assert parse_contributions(None) == {}
    assert parse_contributions({"owners": None}) == {}
