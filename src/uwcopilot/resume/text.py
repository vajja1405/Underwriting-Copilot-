"""Bullet assembly and number formatting for the resume lines, and the wording guard every bullet passes.

A bullet is a lead phrase followed by clauses. A clause with a number is "measured"; a clause without one (for
example "SHAP reason codes") is "context" and is printed only when the bullet has at least one measured clause.
Every clause that could not be written is recorded as an Omission with its reason, for stderr.

Numbers are printed with the precision the project's README table uses (4 decimals for rates and scores), so no
value is rounded to a friendlier figure. Intervals in brackets are the 95% intervals written in the results file:
percentile bootstrap unless the brackets say exact (Clopper-Pearson) or Wilson. A bootstrap interval with zero width
(or one the file flags as too narrow) and no other interval recorded is not printed in brackets: the value is
followed by a note that says so (see interval()).
"""

import re
from dataclasses import dataclass, field

from uwcopilot.resume.results import EXACT, NARROW_BOOTSTRAP, WILSON, Estimate

LENDER = "lender"
STUDIO = "studio"

# The label printed inside the brackets for each interval method (a bootstrap interval has none).
INTERVAL_LABELS: dict[str, str] = {EXACT: "exact ", WILSON: "Wilson "}
# What is printed after the value, instead of brackets, for a NARROW_BOOTSTRAP estimate.
ZERO_WIDTH_NOTE = "zero-width bootstrap interval, no other interval recorded"
FLAGGED_NARROW_NOTE = "bootstrap interval flagged as too narrow, no other interval recorded"

# Fixed names, dates and units in the bullet text that contain digits but are not metrics. The tests remove them
# before checking that every other number in a bullet comes from the results folder.
STATIC_PHRASES: tuple[str, ...] = (
    "SR 26-2",
    "April 17, 2026",
    "per 1,000",
    "P10-P90",
    "freMTPL2",
    "CC BY-NC 4.0",
    "95%",
    "L0",
    "L1",
    "L2",
)

# Wording a bullet must never contain, with the reason. Checked on every bullet by BulletDraft.finish().
FORBIDDEN_WORDING: tuple[tuple[str, str], ...] = (
    (r"100\s*%", "the retired '100% of injected prompt-injection attempts blocked' claim"),
    (r"\bblock(ed|s)?\b", "the retired injection wording: attack success is quoted per layer instead"),
    (r"\bdeploy", "nothing is deployed: the measured targets are local containers"),
    (r"\bproduction\b", "no production users and no production accuracy"),
    (r"\breal[- ]world\b", "no real-world claim: the data is public or SYNTHETIC"),
    (r"\bsavings?\b|\bsaved\b", "no savings claim"),
    (r"\bcomplian(t|ce with)\b|\bcomplies\b", "a demonstration, not a compliance program"),
    (r"\bSR 11-7\b", "superseded by SR 26-2"),
    (r"\bcloud\b|\bAWS\b|\bBedrock\b|\bAzure\b|\bGCP\b", "no cloud service ran in this repo"),
    (r"\bTerraform\b|\bIaC\b|\bCDK\b|\bKubernetes\b", "no infrastructure as code ran"),
    (r"\$", "no cost was measured"),
    (r"\bp95\b", "no per-packet p95 latency is quoted"),
    (r"\bGPT\b|\bClaude\b|\bAnthropic\b|\bOpenAI\b", "no hosted LLM ran"),
)


class WordingError(ValueError):
    """A bullet contains wording this project must not use."""


def check_wording(text: str) -> None:
    """Raise WordingError when the text matches any FORBIDDEN_WORDING pattern."""
    for pattern, reason in FORBIDDEN_WORDING:
        if re.search(pattern, text, flags=re.IGNORECASE):
            raise WordingError(f"forbidden wording /{pattern}/ ({reason}) in: {text}")


def num(value: float, decimals: int = 4) -> str:
    """A metric with a fixed number of decimals."""
    return f"{value:.{decimals}f}"


def signed(value: float, decimals: int = 4) -> str:
    """A difference with its sign, for example +0.0141."""
    return f"{value:+.{decimals}f}"


def count(value: float) -> str:
    """A whole count with thousands separators, for example 30,681."""
    return f"{int(value):,}"


def compact(value: float) -> str:
    """A short setting such as a multiplier (1.25, 1.1), without trailing zeros."""
    return f"{value:g}"


def interval(estimate: Estimate, decimals: int = 4, with_sign: bool = False) -> str:
    """'value [low, high]', with 'exact' or 'Wilson' inside the brackets for those intervals.

    A NARROW_BOOTSTRAP estimate (a zero-width or flagged bootstrap interval, and no exact or Wilson interval in the
    file) prints no brackets: 'value (zero-width bootstrap interval, no other interval recorded)', or the
    FLAGGED_NARROW_NOTE when its width is not zero, so a collapsed interval never reads as certainty.
    """
    value = signed(estimate.value, decimals) if with_sign else num(estimate.value, decimals)
    if estimate.method == NARROW_BOOTSTRAP:
        note = ZERO_WIDTH_NOTE if estimate.low == estimate.high else FLAGGED_NARROW_NOTE
        return f"{value} ({note})"
    low = signed(estimate.low, decimals) if with_sign else num(estimate.low, decimals)
    high = signed(estimate.high, decimals) if with_sign else num(estimate.high, decimals)
    label = INTERVAL_LABELS.get(estimate.method, "")
    return f"{value} [{label}{low}, {high}]"


def years_text(years: list[int]) -> str:
    """'2016' for one year, '2017-2018' for a range."""
    ordered = sorted(int(year) for year in years)
    return str(ordered[0]) if ordered[0] == ordered[-1] else f"{ordered[0]}-{ordered[-1]}"


@dataclass(frozen=True)
class Omission:
    """A bullet or clause that was left out, and why."""

    bullet_id: str
    part: str
    reason: str


@dataclass(frozen=True)
class Bullet:
    """One finished resume bullet and the results files its numbers came from."""

    bullet_id: str
    section: str
    text: str
    sources: tuple[str, ...]


@dataclass
class BulletDraft:
    """A bullet being assembled from measured clauses, context clauses and omissions."""

    bullet_id: str
    section: str
    lead: str
    clauses: list[tuple[str, bool]] = field(default_factory=list)
    omissions: list[Omission] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    core_missing: bool = False

    def add(self, clause: str, source: str) -> None:
        """Add a clause whose numbers come from results/<source>."""
        self.clauses.append((clause, True))
        if source not in self.sources:
            self.sources.append(source)

    def add_context(self, clause: str) -> None:
        """Add a clause without a number; it is printed only if the bullet has a measured clause."""
        self.clauses.append((clause, False))

    def omit(self, part: str, reason: str, core: bool = False) -> None:
        """Record a clause that is left out; a core clause takes the whole bullet with it."""
        self.omissions.append(Omission(self.bullet_id, part, reason))
        if core:
            self.core_missing = True

    def finish(self) -> tuple[Bullet | None, list[Omission]]:
        """The bullet (or None when its core metric or every metric is missing) and the omissions."""
        measured = [text for text, is_measured in self.clauses if is_measured]
        if self.core_missing or not measured:
            why = "its core metric is not measured" if self.core_missing else "none of its metrics is measured"
            return None, [*self.omissions, Omission(self.bullet_id, "whole bullet", why)]
        text = f"{self.lead}: {'; '.join(clause for clause, _ in self.clauses)}."
        check_wording(text)
        return Bullet(self.bullet_id, self.section, text, tuple(self.sources)), list(self.omissions)
