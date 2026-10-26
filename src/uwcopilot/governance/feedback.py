"""Human review feedback (Phase 6 C): the fixed override-reason list, and the override statistics of a decision log.

What the approval step records for every decision (agent/hitl.py, the human_review node of agent/graph.py):
- review_action: "override" when the reviewer approves or declines against the reference policy recommendation
  (NBC-8.2, HM-10.2; agent.hitl.is_override), otherwise "accept". Referring a file or asking for more information is
  never a final decision, so it is never an override, even when the policy recommended something else;
- reason_codes: codes from config/override_reasons.yaml (load_override_reasons); an override needs at least one;
- reason_text: optional free text about the reasons (the rationale stays required for every decision);
- role: the reviewer role. Roles are CLAIMED in this demo (no identity provider).

Records written before Phase 6 carry only is_override and override_reason. feedback_from_record() maps them: the
review_action follows is_override and the single override_reason becomes a one-code reason list. Such records are
counted as "legacy" so a reader can see how many were mapped.

summarize_feedback() reads any agent decision log and returns the override rate with a percentile bootstrap 95%
interval (1,000 resamples, seed 42; the unit is one decision) and counts per reason code, per reviewer role, per
action and per line. It describes whatever log it is given: run on a SIMULATED reviewer log
(scripts/feedback_report.py) it describes the simulation, not real reviewers. It never decides whether a rate is too
high; monitoring thresholds are configured elsewhere.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from uwcopilot.common.paths import CONFIG_DIR

OVERRIDE_REASONS_FILE = CONFIG_DIR / "override_reasons.yaml"
FEEDBACK_VERSION = "review-feedback-v1"
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
NOT_MEASURED = "not measured"

ReviewAction = Literal["accept", "override"]


class OverrideReason(BaseModel):
    """One approved override reason: a stable code, a short label and when to use it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    label: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=10, max_length=600)


class OverrideReasonList(BaseModel):
    """The whole config/override_reasons.yaml file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    reasons: tuple[OverrideReason, ...] = Field(min_length=1)

    @field_validator("reasons")
    @classmethod
    def codes_are_unique(cls, reasons: tuple[OverrideReason, ...]) -> tuple[OverrideReason, ...]:
        """A code may appear only once, so a record's code always means one thing."""
        codes = [reason.code for reason in reasons]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        if duplicates:
            raise ValueError(f"duplicate override reason codes: {duplicates}")
        return reasons

    def codes(self) -> tuple[str, ...]:
        """The codes, in file order."""
        return tuple(reason.code for reason in self.reasons)


@lru_cache(maxsize=8)
def load_override_reasons(path: Path = OVERRIDE_REASONS_FILE) -> OverrideReasonList:
    """Read and validate the override reason list (cached per path: the file is configuration, read once)."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return OverrideReasonList.model_validate(raw)


def override_reason_codes(path: Path = OVERRIDE_REASONS_FILE) -> tuple[str, ...]:
    """The approved reason codes, in file order."""
    return load_override_reasons(path).codes()


def review_action_for(is_override: bool) -> ReviewAction:
    """'override' for an override (agent.hitl.is_override), else 'accept'."""
    return "override" if is_override else "accept"


@dataclass(frozen=True)
class ReviewFeedback:
    """The feedback fields of one decision record, the same for new and pre-Phase-6 (legacy) records."""

    record_id: str
    application_id: str
    line: str
    action: str
    reference_recommendation: str
    review_action: ReviewAction
    reason_codes: tuple[str, ...]
    reason_text: str | None
    reviewer_role: str
    reviewer_label: str
    legacy: bool = False
    unknown_codes: tuple[str, ...] = field(default_factory=tuple)


def feedback_from_record(record: Mapping[str, Any], known_codes: Iterable[str] | None = None) -> ReviewFeedback | None:
    """The feedback of one agent decision record, or None when the record has no reviewer block.

    Legacy records (no review_action) are mapped from is_override and override_reason. known_codes defaults to the
    approved list; a code outside it (possible only in a record written under an older list) is kept and reported
    in unknown_codes, never dropped.
    """
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, Mapping):
        return None
    allowed = set(override_reason_codes() if known_codes is None else known_codes)
    legacy = "review_action" not in reviewer
    if legacy:
        review_action = review_action_for(bool(reviewer.get("is_override")))
        legacy_reason = reviewer.get("override_reason")
        codes: tuple[str, ...] = (str(legacy_reason),) if legacy_reason else ()
    else:
        review_action = "override" if reviewer.get("review_action") == "override" else "accept"
        codes = tuple(str(code) for code in reviewer.get("reason_codes") or [])
    policy = record.get("policy_result") or {}
    return ReviewFeedback(
        record_id=str(record.get("record_id", "")),
        application_id=str(record.get("application_id", "")),
        line=str(policy.get("line", "unknown")),
        action=str(reviewer.get("action", "")),
        reference_recommendation=str(record.get("reference_recommendation", "")),
        review_action=review_action,
        reason_codes=codes,
        reason_text=reviewer.get("reason_text"),
        reviewer_role=str(reviewer.get("role", "unknown")),
        reviewer_label=str(record.get("reviewer_label", "not recorded")),
        legacy=legacy,
        unknown_codes=tuple(code for code in codes if code not in allowed),
    )


def override_rate_interval(
    indicators: list[int], n_resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED
) -> dict[str, Any]:
    """The override rate and its percentile bootstrap 95% interval; each indicator is one decision (1 = override)."""
    if not indicators:
        return {"status": NOT_MEASURED, "reason": "no decision records with a reviewer action"}
    # Imported here: numpy is needed only for the statistics, not by the checkpoint that loads the reason list.
    import numpy as np

    from uwcopilot.risk.bootstrap import bootstrap_ci

    interval = bootstrap_ci(np.mean, np.asarray(indicators, dtype=float), n_resamples=n_resamples, seed=seed)
    return {
        "value": round(interval["estimate"], 4),
        "ci_low": round(interval["ci_low"], 4),
        "ci_high": round(interval["ci_high"], 4),
        "k": int(sum(indicators)),
        "n": len(indicators),
        "confidence": 1 - interval["alpha"],
        "n_resamples": interval["n_resamples"],
        "seed": interval["seed"],
        "method": "percentile bootstrap",
        "unit": "decision",
    }


def counts_by_group(items: list[ReviewFeedback], key: str) -> dict[str, dict[str, Any]]:
    """{group: {decisions, overrides, override_rate}} for one attribute of ReviewFeedback (point values, no CI)."""
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        group = str(getattr(item, key))
        entry = groups.setdefault(group, {"decisions": 0, "overrides": 0})
        entry["decisions"] += 1
        entry["overrides"] += int(item.review_action == "override")
    for entry in groups.values():
        entry["override_rate"] = round(entry["overrides"] / entry["decisions"], 4)
    return dict(sorted(groups.items()))


def summarize_feedback(
    records: Iterable[Mapping[str, Any]],
    known_codes: Iterable[str] | None = None,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Override statistics of a decision log (module docstring). Records without a reviewer block are skipped."""
    codes = tuple(override_reason_codes() if known_codes is None else known_codes)
    all_records = list(records)
    items: list[ReviewFeedback] = []
    for record in all_records:
        feedback = feedback_from_record(record, codes)
        if feedback is not None:
            items.append(feedback)
    overrides = [item for item in items if item.review_action == "override"]
    reason_counts: Counter[str] = Counter()
    accept_reason_counts: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for item in items:
        target = reason_counts if item.review_action == "override" else accept_reason_counts
        target.update(item.reason_codes)
        unknown.update(item.unknown_codes)
    return {
        "feedback_version": FEEDBACK_VERSION,
        "records_read": len(all_records),
        "records_without_reviewer": len(all_records) - len(items),
        "decisions": len(items),
        "overrides": len(overrides),
        "accepts": len(items) - len(overrides),
        "override_rate": override_rate_interval(
            [int(item.review_action == "override") for item in items], n_resamples=n_resamples, seed=seed
        ),
        "reasons_on_overrides": {code: reason_counts.get(code, 0) for code in codes},
        "reason_codes_on_accepts": {code: accept_reason_counts[code] for code in sorted(accept_reason_counts)},
        "unknown_reason_codes": dict(sorted(unknown.items())),
        "overrides_without_reason": sum(1 for item in overrides if not item.reason_codes),
        "overrides_with_two_or_more_reasons": sum(1 for item in overrides if len(item.reason_codes) >= 2),
        "overrides_with_reason_text": sum(1 for item in overrides if item.reason_text),
        "by_reviewer_role": counts_by_group(items, "reviewer_role"),
        "by_action": dict(sorted(Counter(item.action for item in items).items())),
        "by_line": counts_by_group(items, "line"),
        "by_reference_recommendation": counts_by_group(items, "reference_recommendation"),
        "by_reviewer_label": dict(sorted(Counter(item.reviewer_label for item in items).items())),
        "legacy_records_mapped": sum(1 for item in items if item.legacy),
        "definition": "override = an approve or decline that differs from the reference policy recommendation "
        "(NBC-8.2, HM-10.2; agent.hitl.is_override); a referral or an information request is never an override",
    }
