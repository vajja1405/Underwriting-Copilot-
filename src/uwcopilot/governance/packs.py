"""Governance packs (Phase 6 E): what the two packs share - the results files they read, value lookup, number
formats, where each number came from, and the regulatory citations.

scripts/render_reports.py renders two documents from results/*.json (and from nothing else):
- docs/governance/bank_model_risk.md (governance/packs_bank.py): model inventory, materiality tiering, validation,
  ongoing monitoring, outcomes analysis and change management, aligned to the structure of SR 26-2, plus a separate
  section for the LLM extraction and the agent, which SR 26-2 footnote 3 places outside its scope;
- docs/governance/insurance_ai_program.md (governance/packs_insurance.py): an AIS Program outline mapped to the
  elements of the NAIC Model Bulletin on the Use of Artificial Intelligence Systems by Insurers.

Ground rule 1: every number in a pack is read from a results file at render time. Every format below prints
"not measured" (with the recorded reason when there is one) for a missing value, so nothing is typed by hand,
rounded up or estimated. A governance_metrics.json section that has not been generated yet prints
"not measured yet" with the make target that writes it. The only numbers in the packs' own text are dates,
identifiers and section numbers of the documents cited (tests/governance/test_packs.py checks this).

Provenance: each table names its results file and section and the git commit recorded in that file by the script
that wrote it (three layouts exist: risk/results.py update_results metadata per section, the governance/provenance.py
block, and the "versions.git" block of scripts/eval_agent.py).
"""

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uwcopilot.risk.results import is_not_measured

NOT_MEASURED = "not measured"
OWNER = "Rahul Vajja (portfolio project)"

# The results files a pack reads, by their path under results/. A file that does not exist is simply absent.
RISK = "risk_metrics.json"
GOVERNANCE = "governance_metrics.json"
AGENT = "agent_metrics.json"
DOCINTEL = "docintel_metrics.json"
INJECTION = "injection_metrics.json"
INJECTION_DETECTOR = "injection/injection_metrics_detector_only.json"
PACK_FILES: tuple[str, ...] = (RISK, GOVERNANCE, AGENT, DOCINTEL, INJECTION, INJECTION_DETECTOR)
# Key under which load_pack_sources lists the LLM migration certificates found (scripts/llm_migration_gate.py).
MIGRATION_CERTIFICATES = "llm_migration_certificates"
MIGRATION_CERTIFICATE_GLOB = "llm_migration_*.json"

# The make target that writes each section of results/governance_metrics.json (for "not measured yet").
GOVERNANCE_PRODUCERS: dict[str, str] = {
    "lineage": "make feedback-report",
    "feedback": "make feedback-report",
    "monitoring": "make monitor",
    "champion_challenger": "make champion-challenger",
    "outcomes": "make champion-challenger",
    "release_gate": "make release-gate ARGS=--write-results",
}

# (generated at, git commit, uncommitted changes) paths of the three provenance layouts, tried in this order.
PROVENANCE_LAYOUTS: tuple[tuple[str, str, str], ...] = (
    ("metadata.generated_at_utc", "metadata.git_commit", "metadata.git_worktree_dirty"),
    ("provenance.generated_at_utc", "provenance.git_sha", "provenance.git_dirty"),
    ("generated_at", "versions.git.commit", "versions.git.uncommitted_changes"),
)

# ---------------------------------------------------------------------------
# Regulatory references (checked as described in each pack's "Regulatory basis")
# ---------------------------------------------------------------------------

SR_26_2 = (
    'SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026, "Revised Guidance on Model Risk Management" '
    "(Federal Reserve, OCC and FDIC, April 17, 2026), which supersedes SR 11-7 and SR 21-8"
)
SR_26_2_LINKS = (
    "[Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm), "
    "[OCC](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html), "
    "[FDIC](https://www.fdic.gov/news/financial-institution-letters/2026/"
    "agencies-revise-interagency-model-risk-management-guidance)"
)
SR_26_2_LOCAL_COPY = "data/raw/regulatory/frb_sr_26_2.pdf"
# Quoted from the local copy of the attachment, section II (Purpose and Scope), footnote 3.
SR_26_2_FOOTNOTE_3 = (
    "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of "
    "this guidance. Nonetheless, a banking organization's risk management and governance practices should guide the "
    "determination of appropriate governance and controls for any tools, processes, or systems not covered in this "
    "document."
)
# Quoted from the same section: what the guidance does not count as a model.
SR_26_2_NOT_A_MODEL = (
    "simple arithmetic calculations, such as those found within spreadsheets, as well as deterministic rule-based "
    "processes and software where there are no statistical, economic, or financial theories underpinning their "
    "design or use"
)
NAIC_BULLETIN = (
    'NAIC Model Bulletin, "Use of Artificial Intelligence Systems by Insurers" (adopted December 4, 2023; '
    "[bulletin](https://content.naic.org/sites/default/files/"
    "cmte-h-big-data-artificial-intelligence-wg-ai-model-bulletin.pdf.pdf))"
)
NAIC_ADOPTION_MAP = (
    "[adoption map](https://content.naic.org/sites/default/files/legal-adoption-map-ai-model-bulletin.pdf)"
)
NY_DFS_CL7 = (
    "New York DFS Insurance Circular Letter No. 7 (July 11, 2024), use of AI systems and external consumer data in "
    "insurance underwriting and pricing ([circular](https://www.dfs.ny.gov/industry-guidance/circular-letters/"
    "cl2024-07))"
)
NY_DFS_LOCAL_COPY = "data/raw/regulatory/nydfs_circular_letter_2024_07.html"

APPLICABILITY = (
    "Applicability of any guidance cited here depends on the institution and its risk profile. This project is a "
    "demonstration, not a compliance program, and nothing in it claims compliance with any regulation or guidance."
)
DATA_STATEMENT = (
    "All data are public (Lending Club, freMTPL2 from OpenML 41214/41215, UCI Statlog German Credit, the OFAC SDN list "
    "and OpenSanctions) or SYNTHETIC / SIMULATED (the document packets, the agent applications, the reviewer "
    "decisions, the monitoring stream's drift, the champion/challenger loans and their outcomes). The public loan and "
    "policy datasets are de-identified records of real loans, credit applicants and motor policies; the OFAC SDN list "
    "and OpenSanctions name real sanctioned persons and entities and are used only as screening references. Every "
    "application the agent screens, every reviewer and every decision here is SYNTHETIC or SIMULATED."
)

# ---------------------------------------------------------------------------
# Reading the results files
# ---------------------------------------------------------------------------


def load_pack_sources(results_dir: Path) -> dict[str, Any]:
    """Every file of PACK_FILES that exists under results_dir, parsed, keyed by its PACK_FILES name.

    Also lists (by name only) the LLM migration certificates under MIGRATION_CERTIFICATES.
    """
    sources: dict[str, Any] = {}
    for name in PACK_FILES:
        path = Path(results_dir) / name
        if path.exists():
            sources[name] = json.loads(path.read_text(encoding="utf-8"))
    sources[MIGRATION_CERTIFICATES] = sorted(path.name for path in Path(results_dir).glob(MIGRATION_CERTIFICATE_GLOB))
    return sources


def lookup(document: Any, path: str) -> Any:
    """Value at a dotted path ("credit.results.models.lightgbm"), or None if any part is missing.

    List items are addressed by number ("rows.0.id"). Unlike scripts/render_reports.py, nothing is implied:
    a section's "results" level is written out.
    """
    value = document
    for key in path.split("."):
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def value_in(sources: Mapping[str, Any], file: str, path: str) -> Any:
    """Value at a dotted path inside one results file (None if the file or the path is missing)."""
    return lookup(sources.get(file), path)


def section_results(sources: Mapping[str, Any], file: str, section: str) -> Any:
    """The "results" payload of one update_results section, or None when the section is absent."""
    return value_in(sources, file, f"{section}.results")


def missing_section_text(file: str, section: str) -> str:
    """'not measured yet' for a section that has not been generated, with what generates it."""
    producer = GOVERNANCE_PRODUCERS.get(section) if file == GOVERNANCE else None
    how = f"; `{producer}` writes it" if producer else ""
    return f"{NOT_MEASURED} yet: `results/{file}` has no section `{section}`{how}"


def run_status(sources: Mapping[str, Any], section: str, done: str) -> str:
    """done when results/governance_metrics.json holds the section, else 'not measured yet' (for status columns)."""
    if section_results(sources, GOVERNANCE, section) is not None:
        return done
    producer = GOVERNANCE_PRODUCERS.get(section)
    return f"{NOT_MEASURED} yet" + (f" (`{producer}`)" if producer else "")


def full_stop(sentence: str) -> str:
    """The sentence with a final period, unless it already ends with one (also inside a closing parenthesis)."""
    return sentence if sentence.endswith((".", ".)")) else sentence + "."


# ---------------------------------------------------------------------------
# Formats: every one returns "not measured" for a missing value
# ---------------------------------------------------------------------------


def present(value: Any) -> bool:
    """True if value is a real result: not missing, not a "not measured" record or text, not NaN or infinite."""
    if value is None or is_not_measured(value):
        return False
    if isinstance(value, str) and value.startswith(NOT_MEASURED):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True


def is_number(value: Any) -> bool:
    """A JSON number (bool is not a number here)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and present(value)


def not_measured_text(value: Any = None) -> str:
    """'not measured', with the reason if value is a not_measured() record or a 'not measured: ...' text."""
    if is_not_measured(value) and value.get("reason"):
        return f"{NOT_MEASURED} ({value['reason']})"
    if isinstance(value, str) and value.startswith(NOT_MEASURED):
        return value
    return NOT_MEASURED


def number(value: Any, digits: int = 4) -> str:
    """Fixed decimals, e.g. 0.7151."""
    return f"{value:.{digits}f}" if is_number(value) else not_measured_text(value)


def integer(value: Any) -> str:
    """Whole number with thousands separators, e.g. 205,520 (a float is rounded to the nearest unit)."""
    return f"{round(value):,}" if is_number(value) else not_measured_text(value)


def percent(value: Any, digits: int = 1) -> str:
    """A share as a percentage, e.g. 0.2096 -> 21.0%."""
    return f"{100 * value:.{digits}f}%" if is_number(value) else not_measured_text(value)


def text(value: Any) -> str:
    """A string or other value as text ("yes"/"no" for a bool); 'not measured' if missing."""
    if not present(value):
        return not_measured_text(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def interval(entry: Any, digits: int = 4) -> str:
    """'estimate [ci_low, ci_high]' from a bootstrap result (risk/bootstrap.py), or 'not measured'."""
    keys = ("estimate", "ci_low", "ci_high")
    if not isinstance(entry, Mapping) or not all(is_number(entry.get(key)) for key in keys):
        return not_measured_text(entry)
    return f"{entry['estimate']:.{digits}f} [{entry['ci_low']:.{digits}f}, {entry['ci_high']:.{digits}f}]"


def _counts(entry: Mapping[str, Any]) -> str:
    """'k/n' or 'n=N' from whichever count fields a share records, else ''."""
    for top, bottom in (("numerator", "denominator"), ("k", "n")):
        if is_number(entry.get(top)) and is_number(entry.get(bottom)):
            return f"{integer(entry[top])}/{integer(entry[bottom])}"
    if is_number(entry.get("n")):
        return f"n={integer(entry['n'])}"
    return ""


def share(entry: Any, digits: int = 4) -> str:
    """A rate with its interval and counts: 'value [ci_low, ci_high] (k/n)', or 'not measured'.

    Reads either {value, ci_low, ci_high} or a bootstrap result {estimate, ci_low, ci_high}. A zero-width interval
    (every resample gave the same share, as for 60 of 60) would overstate the certainty, so it is not shown as an
    interval: the exact interval is shown when the file records one (exact_ci_low, exact_ci_high), otherwise the
    text says it was not recorded.
    """
    if not isinstance(entry, Mapping):
        return not_measured_text(entry)
    point = entry.get("value", entry.get("estimate"))
    low, high = entry.get("ci_low"), entry.get("ci_high")
    if not all(is_number(item) for item in (point, low, high)):
        return not_measured_text(entry)
    counts = _counts(entry)
    if entry.get("bootstrap_degenerate") is True or low == high:
        exact_low, exact_high = entry.get("exact_ci_low"), entry.get("exact_ci_high")
        if is_number(exact_low) and is_number(exact_high):
            shown = f"{point:.{digits}f} exact [{exact_low:.{digits}f}, {exact_high:.{digits}f}]"
        else:
            note = "zero-width bootstrap interval, exact interval not recorded"
            return f"{point:.{digits}f} ({counts + '; ' if counts else ''}{note})"
    else:
        shown = f"{point:.{digits}f} [{low:.{digits}f}, {high:.{digits}f}]"
    return f"{shown} ({counts})" if counts else shown


def wilson_share(entry: Any, digits: int = 4) -> str:
    """'rate [low, high] (successes/n, Wilson 95%)' from an entry with rate, wilson_ci95 and counts."""
    if not isinstance(entry, Mapping):
        return not_measured_text(entry)
    bounds = entry.get("wilson_ci95")
    rate, successes, total = entry.get("rate"), entry.get("successes"), entry.get("n")
    if not (isinstance(bounds, list) and len(bounds) == 2 and all(is_number(item) for item in (rate, *bounds))):
        return not_measured_text(entry)
    counts = f"{integer(successes)}/{integer(total)}, " if is_number(successes) and is_number(total) else ""
    return f"{rate:.{digits}f} [{bounds[0]:.{digits}f}, {bounds[1]:.{digits}f}] ({counts}Wilson 95%)"


def model_label(name: Any) -> str:
    """Readable model name for a JSON model key."""
    labels = {
        "glm": "GLM",
        "lgbm": "LightGBM",
        "lightgbm": "LightGBM",
        "logistic_scorecard": "logistic scorecard",
        "logistic_regression": "logistic regression",
    }
    return labels.get(str(name), text(name))


def table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    """Markdown table lines (pipes and line breaks inside cells are escaped)."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = (str(cell).replace("|", "\\|").replace("\n", " ") for cell in row)
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def bullets(items: Iterable[str]) -> list[str]:
    """Markdown bullet lines."""
    return [f"- {item}" for item in items]


# ---------------------------------------------------------------------------
# Where a number came from
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """What a results file records about the run that wrote it."""

    generated_at: Any
    git_commit: Any
    uncommitted_changes: Any


def provenance(sources: Mapping[str, Any], file: str, section: str | None = None) -> Provenance | None:
    """The recorded provenance of a whole file or of one update_results section; None when nothing is recorded."""
    document = sources.get(file)
    if section is not None:
        document = lookup(document, section)
    if not isinstance(document, Mapping):
        return None
    for generated_path, commit_path, dirty_path in PROVENANCE_LAYOUTS:
        commit = lookup(document, commit_path)
        if isinstance(commit, str) and commit:
            return Provenance(lookup(document, generated_path), commit, lookup(document, dirty_path))
    return None


def source_note(sources: Mapping[str, Any], file: str, section: str | None = None) -> str:
    """'Source: `results/<file>`, section `<section>`, written at git commit `<hash>` (...).' for a table."""
    where = f"`results/{file}`" + (f", section `{section}`" if section else "")
    if file not in sources:
        return f"Source: {where}: the file is absent, so every value from it is {NOT_MEASURED}."
    if section is not None and lookup(sources[file], section) is None:
        return f"Source: {where}: {missing_section_text(file, section)}."
    recorded = provenance(sources, file, section)
    if recorded is None:
        return f"Source: {where} (no git commit recorded)."
    return (
        f"Source: {where}, written at git commit `{recorded.git_commit}` "
        f"(uncommitted changes recorded by the script: {text(recorded.uncommitted_changes)})."
    )


def sources_table(sources: Mapping[str, Any], used: Sequence[tuple[str, str | None, str]]) -> list[str]:
    """One row per (file, section, where used): generated time, git commit and the uncommitted-changes flag."""
    rows = []
    for file, section, where in used:
        name = f"`results/{file}`"
        part = f"`{section}`" if section else "whole file"
        if file not in sources:
            rows.append([name, part, where, "absent", NOT_MEASURED, NOT_MEASURED])
            continue
        if section is not None and lookup(sources[file], section) is None:
            rows.append([name, part, where, f"section absent ({NOT_MEASURED} yet)", NOT_MEASURED, NOT_MEASURED])
            continue
        recorded = provenance(sources, file, section)
        if recorded is None:
            rows.append([name, part, where, "present", "not recorded", "not recorded"])
            continue
        rows.append(
            [
                name,
                part,
                where,
                text(recorded.generated_at),
                f"`{recorded.git_commit}`",
                text(recorded.uncommitted_changes),
            ]
        )
    header = ["Results file", "Section", "Used in", "Generated (UTC)", "Git commit", "Uncommitted changes"]
    return table(header, rows)


def generated_note(script: str = "scripts/render_reports.py") -> list[str]:
    """The 'do not edit by hand' banner at the top of a pack."""
    return [
        f'> Generated by `{script}` from `results/*.json` (the files in "Sources of the numbers"). Do not edit by',
        "> hand: every number is read from those files at render time, and anything not measured says so. Rerun",
        "> `make governance-packs` (or `make reports`) after any results run.",
    ]
