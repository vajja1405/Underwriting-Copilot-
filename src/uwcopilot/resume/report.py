"""Build every resume bullet from a results folder and render the "Projects" text (stdout) and omissions (stderr).

The lender section follows docs/build_prompts.md Prompt 8 item 2 (4-6 bullets); the Studio section follows
docs/studio/founder_studio_build_prompts.md Prompt S12. A bullet whose core metric is not measured is left out and
listed on stderr with the reason, and so is every clause that was dropped.
"""

from dataclasses import dataclass
from pathlib import Path

from uwcopilot.resume.lender_governance import governance_bullet
from uwcopilot.resume.lender_models import credit_bullet, credit_footnotes, insurance_bullet, kyc_bullet
from uwcopilot.resume.lender_platform import documents_bullet, evidence_bullet
from uwcopilot.resume.results import ResultsFolder
from uwcopilot.resume.studio import (
    business_bullet,
    feasibility_bullet,
    path_bullet,
    platform_bullet,
    provenance_bullet,
    siteiq_bullet,
    studio_notes,
)
from uwcopilot.resume.text import LENDER, STUDIO, Bullet, Omission

LENDER_HEADING = (
    "Fundproof Lender Desk (Underwriting Copilot): portfolio project on public datasets and SYNTHETIC documents"
)
STUDIO_HEADING = "Fundproof Founder Studio: SYNTHETIC founder plans and cities; probabilities are SIMULATED"
INTERVAL_NOTE = (
    "Brackets hold the 95% interval written in the results file (percentile bootstrap with 1,000 resamples and seed "
    "42, unless marked Wilson). Every number comes from results/*.json. Re-run the producing scripts on your own "
    "machine before putting a number on a resume (docs/build_prompts.md, Notes for Rahul)."
)
MIN_LENDER_BULLETS = 4


@dataclass(frozen=True)
class ResumeReport:
    """The finished bullets, what was left out, stdout footnotes and stderr notes."""

    bullets: list[Bullet]
    omissions: list[Omission]
    footnotes: list[str]
    notes: list[str]


def build_report(results_dir: Path, repo_root: Path) -> ResumeReport:
    """Every bullet of both sections, built from results_dir (repo_root is used for the model card and CI file)."""
    results = ResultsFolder(results_dir)
    built = [
        evidence_bullet(results),
        credit_bullet(results, repo_root),
        insurance_bullet(results),
        documents_bullet(results),
        kyc_bullet(results),
        governance_bullet(results, repo_root),
        platform_bullet(results),
        feasibility_bullet(results),
        provenance_bullet(results),
        path_bullet(results),
        siteiq_bullet(results),
        business_bullet(results),
    ]
    bullets = [bullet for bullet, _ in built if bullet is not None]
    omissions = [omission for _, items in built for omission in items]
    footnotes = credit_footnotes(next((bullet for bullet in bullets if bullet.bullet_id == "credit_model"), None))
    notes = studio_notes(results)
    lender_count = sum(1 for bullet in bullets if bullet.section == LENDER)
    if lender_count < MIN_LENDER_BULLETS:
        notes.append(f"only {lender_count} lender bullets are measured (Prompt 8 asks for 4-6): do not pad them")
    return ResumeReport(bullets=bullets, omissions=omissions, footnotes=footnotes, notes=notes)


def render_projects(report: ResumeReport) -> list[str]:
    """The "Projects" section as lines: one heading per side, its bullets, then the footnotes."""
    lines = ["Projects", INTERVAL_NOTE]
    for section, heading in ((LENDER, LENDER_HEADING), (STUDIO, STUDIO_HEADING)):
        section_bullets = [bullet for bullet in report.bullets if bullet.section == section]
        lines.append("")
        lines.append(heading)
        if not section_bullets:
            lines.append("- (no measured bullet: see stderr)")
        lines.extend(f"- {bullet.text}" for bullet in section_bullets)
    if report.footnotes:
        lines.append("")
        lines.extend(f"Footnote: {footnote}" for footnote in report.footnotes)
    return lines


def render_omissions(report: ResumeReport, extra_notes: list[str]) -> list[str]:
    """Stderr lines: every bullet or clause left out with its reason, then the notes."""
    lines = ["Left out (not measured or not claimable):"]
    lines.extend(f"- {item.bullet_id} / {item.part}: {item.reason}" for item in report.omissions)
    notes = report.notes + extra_notes
    if notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in notes)
    return lines
