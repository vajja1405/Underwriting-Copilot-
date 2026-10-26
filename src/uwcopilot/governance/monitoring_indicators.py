"""Extraction, agent and reviewer indicators, read from the latest results files (Phase 6 B).

These are not a stream: each is one value from the last evaluation run, compared with its rule in
config/monitoring.yaml. The files are read as JSON; nothing is recomputed and no other track's code is imported.

- results/docintel_metrics.json (rules extractor on the SYNTHETIC held-out packets):
  citation_support_rate = rules.citations.supported_all_values (share of extracted values whose cited page prints
  the value; its CI is copied); ocr_page_share = rules.latency.ocr_pages_total / rules.page_classification.all.
  denominator (pages with no text layer that were read by OCR, the OCR fallback, over all pages);
  low_confidence_field_share is "not available" because the file has no field-confidence metric.
- results/agent_metrics.json (deterministic path on SYNTHETIC applications):
  required_escalations_escalated = deterministic_path.escalation.all_required_escalations; escalation_share
  (reported, no rule: the mix of the evaluation set is designed, so there is no reference level) =
  all_required_escalations.n / deterministic_path.cases; unsupported_claim_rate = llm_dependent.
  unsupported_claim_rate, "not measured" while no LLM has been run.
- results/governance_metrics.json, section "feedback" (SIMULATED reviewer decisions, written by
  scripts/feedback_report.py): override_rate = summary.override_rate and the override reasons =
  summary.reasons_on_overrides, the keys governance/feedback.py summarize_feedback writes. For another shape, the
  first key with that name anywhere in the section's results is used ("override_reasons" is accepted for the
  reasons too). When the section or a key is absent, the indicator or its reasons are "not available"; nothing is
  guessed. The indicator also records which feedback section it read (its commit, time and SHA-256).

The monitor reads the feedback section when it runs, so it must run after scripts/feedback_report.py (`make
governance-results` runs the producers in order). override_indicator_problems() says when the monitoring section of a
results file did not read the feedback section that the same file holds now.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from uwcopilot.governance.monitoring import CheckResult
from uwcopilot.governance.monitoring_config import Level, LevelRule
from uwcopilot.risk.results import is_not_measured

IndicatorStatus = Literal["measured", "not measured", "not available"]
DOCINTEL_FILE = "docintel_metrics.json"
AGENT_FILE = "agent_metrics.json"
GOVERNANCE_FILE = "governance_metrics.json"
FEEDBACK_SECTION = "feedback"
MONITORING_SECTION = "monitoring"
EXTRACTION = "document_extraction"
AGENT = "agent"
REVIEW = "review_feedback"
NO_REASONS = "not available: the feedback section has no reasons_on_overrides or override_reasons key"


@dataclass(frozen=True)
class Indicator:
    """One indicator: where it came from, its value (when there is one) and its level under the config rule."""

    name: str
    component: str
    source: str
    status: IndicatorStatus
    value: float | None = None
    interval: tuple[float, float] | None = None
    level: Level | None = None
    rule: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_check(self) -> CheckResult:
        """The indicator as a check result, so a breach becomes an alert record like any other."""
        if self.value is None or self.level is None:
            raise ValueError(f"{self.name} has no value to check")
        return CheckResult("indicator", self.name, self.value, self.level, self.rule or "", self.interval)

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for the results file and the report."""
        return {
            "name": self.name,
            "component": self.component,
            "source": self.source,
            "status": self.status,
            "value": self.value,
            "interval": list(self.interval) if self.interval else None,
            "level": self.level,
            "rule": self.rule,
            "detail": self.detail,
            "note": self.note,
        }


def dig(document: Any, *keys: str) -> Any:
    """document[key1][key2]..., or None when any step is missing."""
    for key in keys:
        if not isinstance(document, dict) or key not in document:
            return None
        document = document[key]
    return document


def find_key(document: Any, key: str) -> Any:
    """The value of the first `key` found anywhere in nested dicts and lists (depth first), or None."""
    if isinstance(document, dict):
        if key in document:
            return document[key]
        children = list(document.values())
    elif isinstance(document, list):
        children = document
    else:
        return None
    for child in children:
        found = find_key(child, key)
        if found is not None:
            return found
    return None


def as_number(value: Any) -> float | None:
    """A finite number, or the 'value' / 'estimate' of a metric dict; None otherwise."""
    if isinstance(value, dict):
        value = value.get("value", value.get("estimate"))
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def as_interval(metric: Any) -> tuple[float, float] | None:
    """(ci_low, ci_high) of a metric dict, when both are numbers."""
    low, high = as_number(dig(metric, "ci_low")), as_number(dig(metric, "ci_high"))
    return (low, high) if low is not None and high is not None else None


def _measured(
    name: str,
    component: str,
    source: str,
    value: float,
    rule: LevelRule | None,
    interval: tuple[float, float] | None = None,
    detail: dict[str, Any] | None = None,
    note: str = "",
) -> Indicator:
    """A measured indicator and its level (no level when it has no rule)."""
    return Indicator(
        name=name,
        component=component,
        source=source,
        status="measured",
        value=value,
        interval=interval,
        level=rule.level(value) if rule else None,
        rule=rule.describe() if rule else None,
        detail=detail or {},
        note=note,
    )


def _without_value(
    name: str,
    component: str,
    source: str,
    status: IndicatorStatus,
    note: str,
    detail: dict[str, Any] | None = None,
) -> Indicator:
    """An indicator that has no value ('not measured' or 'not available'), with the reason."""
    return Indicator(name=name, component=component, source=source, status=status, note=note, detail=detail or {})


def extraction_indicators(docintel: dict[str, Any] | None, rules: dict[str, LevelRule]) -> list[Indicator]:
    """Citation support, OCR fallback share and low-confidence share from docintel_metrics.json."""
    file_missing = f"results/{DOCINTEL_FILE} is not present"
    citation_source = f"results/{DOCINTEL_FILE}: rules.citations.supported_all_values"
    ocr_source = f"results/{DOCINTEL_FILE}: rules.latency.ocr_pages_total / rules.page_classification.all.denominator"
    confidence_source = f"results/{DOCINTEL_FILE}"
    indicators = []

    citation = dig(docintel, "rules", "citations", "supported_all_values")
    citation_value = as_number(citation)
    if citation_value is None:
        note = file_missing if docintel is None else "the file has no rules.citations.supported_all_values value"
        indicators.append(_without_value("citation_support_rate", EXTRACTION, citation_source, "not available", note))
    else:
        numerator, denominator = dig(citation, "numerator"), dig(citation, "denominator")
        detail: dict[str, Any] = {"supported_values": numerator, "extracted_values": denominator}
        if isinstance(numerator, int) and isinstance(denominator, int):
            detail["citation_failures"] = denominator - numerator
        indicators.append(
            _measured(
                "citation_support_rate",
                EXTRACTION,
                citation_source,
                citation_value,
                rules["citation_support_rate"],
                interval=as_interval(citation),
                detail=detail,
                note="CI copied from the file (bootstrap over packets).",
            )
        )

    ocr_pages = as_number(dig(docintel, "rules", "latency", "ocr_pages_total"))
    all_pages = as_number(dig(docintel, "rules", "page_classification", "all", "denominator"))
    if ocr_pages is None or not all_pages:
        note = file_missing if docintel is None else "the file has no OCR page count or no page total"
        indicators.append(_without_value("ocr_page_share", EXTRACTION, ocr_source, "not available", note))
    else:
        indicators.append(
            _measured(
                "ocr_page_share",
                EXTRACTION,
                ocr_source,
                ocr_pages / all_pages,
                rules["ocr_page_share"],
                detail={"ocr_pages": int(ocr_pages), "pages": int(all_pages)},
                note="Share of pages with no text layer, read by OCR (the OCR fallback). No CI: a count ratio.",
            )
        )

    indicators.append(
        _without_value(
            "low_confidence_field_share",
            EXTRACTION,
            confidence_source,
            "not available",
            file_missing if docintel is None else "the file has no field-confidence metric",
        )
    )
    return indicators


def agent_indicators(agent: dict[str, Any] | None, rules: dict[str, LevelRule]) -> list[Indicator]:
    """Escalation correctness, escalation share and the unsupported-claim rate from agent_metrics.json."""
    file_missing = f"results/{AGENT_FILE} is not present"
    escalation_source = f"results/{AGENT_FILE}: deterministic_path.escalation.all_required_escalations"
    share_source = f"results/{AGENT_FILE}: all_required_escalations.n / deterministic_path.cases"
    claim_source = f"results/{AGENT_FILE}: llm_dependent.unsupported_claim_rate"
    indicators = []

    escalations = dig(agent, "deterministic_path", "escalation", "all_required_escalations")
    escalation_value = as_number(escalations)
    if escalation_value is None:
        note = file_missing if agent is None else "the file has no all_required_escalations value"
        indicators.append(
            _without_value("required_escalations_escalated", AGENT, escalation_source, "not available", note)
        )
    else:
        indicators.append(
            _measured(
                "required_escalations_escalated",
                AGENT,
                escalation_source,
                escalation_value,
                rules["required_escalations_escalated"],
                interval=as_interval(escalations),
                detail={"cases_requiring_escalation": dig(escalations, "n")},
                note="Deterministic path (no LLM); CI copied from the file.",
            )
        )

    required = dig(escalations, "n")
    cases = dig(agent, "deterministic_path", "cases")
    if isinstance(required, int) and isinstance(cases, int) and cases > 0:
        indicators.append(
            _measured(
                "escalation_share",
                AGENT,
                share_source,
                required / cases,
                None,
                detail={"cases_requiring_escalation": required, "cases": cases},
                note="Reported only, no rule: the SYNTHETIC evaluation set's mix is designed, so it has no reference.",
            )
        )
    else:
        note = file_missing if agent is None else "the file has no escalation count or case count"
        indicators.append(_without_value("escalation_share", AGENT, share_source, "not available", note))

    claim_rate = dig(agent, "llm_dependent", "unsupported_claim_rate")
    claim_value = as_number(claim_rate)
    if claim_value is not None:
        indicators.append(
            _measured(
                "unsupported_claim_rate",
                AGENT,
                claim_source,
                claim_value,
                rules["unsupported_claim_rate"],
                interval=as_interval(claim_rate),
            )
        )
    elif isinstance(claim_rate, str) and claim_rate.startswith("not measured"):
        indicators.append(_without_value("unsupported_claim_rate", AGENT, claim_source, "not measured", claim_rate))
    else:
        note = file_missing if agent is None else "the file has no unsupported_claim_rate entry"
        indicators.append(_without_value("unsupported_claim_rate", AGENT, claim_source, "not available", note))
    return indicators


def override_reasons(section: Any) -> dict[str, Any] | None:
    """Overrides per reason code from the feedback section's results, or None when the section has no reasons key.

    feedback.summarize_feedback writes them under summary.reasons_on_overrides; another shape may put them under a
    'reasons_on_overrides' or 'override_reasons' key anywhere in the section.
    """
    reasons = dig(section, "summary", "reasons_on_overrides")
    if reasons is None:
        reasons = find_key(section, "reasons_on_overrides")
    if reasons is None:
        reasons = find_key(section, "override_reasons")
    return reasons if isinstance(reasons, dict) else None


def override_indicators(governance: dict[str, Any] | None, rules: dict[str, LevelRule]) -> list[Indicator]:
    """Reviewer override rate and override reasons from the 'feedback' section of governance_metrics.json."""
    source = f"results/{GOVERNANCE_FILE}: section {FEEDBACK_SECTION!r}, summary.override_rate"
    section = dig(governance, FEEDBACK_SECTION, "results")
    if section is None:
        return [
            _without_value(
                "override_rate",
                REVIEW,
                source,
                "not available",
                f"results/{GOVERNANCE_FILE} has no {FEEDBACK_SECTION!r} section (the feedback track has not run)",
            )
        ]
    detail: dict[str, Any] = {
        "feedback_section_git_commit": dig(governance, FEEDBACK_SECTION, "metadata", "git_commit"),
        "feedback_section_generated_at_utc": dig(governance, FEEDBACK_SECTION, "metadata", "generated_at_utc"),
        "feedback_section_sha256": section_sha256(dig(governance, FEEDBACK_SECTION)),
    }
    reasons = override_reasons(section)
    detail["override_reasons"] = NO_REASONS if reasons is None else reasons

    rate = dig(section, "summary", "override_rate")
    if rate is None:
        rate = find_key(section, "override_rate")
        source = f"results/{GOVERNANCE_FILE}: section {FEEDBACK_SECTION!r}, first 'override_rate' key found"
    if is_not_measured(rate):
        note = str(rate.get("reason", ""))
        return [_without_value("override_rate", REVIEW, source, "not measured", note, detail)]
    value = as_number(rate)
    if value is None:
        note = "the feedback section has no numeric override_rate"
        return [_without_value("override_rate", REVIEW, source, "not available", note, detail)]
    if isinstance(dig(rate, "k"), int) and isinstance(dig(rate, "n"), int):
        detail["overrides"] = rate["k"]
        detail["decisions"] = rate["n"]
    reasons_note = f" Override reasons: {NO_REASONS}." if reasons is None else ""
    return [
        _measured(
            "override_rate",
            REVIEW,
            source,
            value,
            rules["override_rate"],
            interval=as_interval(rate),
            detail=detail,
            note="SIMULATED reviewer decisions (feedback track); CI copied from the file." + reasons_note,
        )
    ]


def section_sha256(section: Any) -> str | None:
    """SHA-256 of a results section as canonical JSON (sorted keys), or None when there is no section."""
    if section is None:
        return None
    return hashlib.sha256(json.dumps(section, sort_keys=True).encode("utf-8")).hexdigest()


def override_indicator_problems(governance: dict[str, Any] | None) -> list[str]:
    """Why a results file's monitoring section did not read the feedback section the same file holds (empty: it did).

    scripts/monitor.py reads the feedback section when it runs and records its SHA-256 (feedback_section_sha256).
    When the feedback section is written after the monitor ran, or did not exist then, the two sections of one file
    disagree: the monitoring section says the override rate is 'not available', or shows an older value. Nothing is
    compared when either section is absent. `make governance-results` runs the producers in the right order.
    """
    feedback = dig(governance, FEEDBACK_SECTION)
    indicators = dig(governance, MONITORING_SECTION, "results", "indicators")
    if dig(feedback, "results") is None or not isinstance(indicators, dict):
        return []
    feedback_written = dig(feedback, "metadata", "generated_at_utc")
    problems = []
    if dig(indicators, "sources", f"results/{GOVERNANCE_FILE}", "sha256") is None:
        problems.append(f"the monitor ran when results/{GOVERNANCE_FILE} did not exist yet (its SHA-256 is null)")
    override = None
    for item in indicators.get("values") or []:
        if isinstance(item, dict) and item.get("name") == "override_rate":
            override = item
    if override is None:
        problems.append("the monitoring section has no override_rate indicator")
        return problems
    read_hash = dig(override, "detail", "feedback_section_sha256")
    if read_hash is None:
        problems.append(
            f"the monitor read no feedback section (override_rate is {override.get('status')!r}: "
            f"{override.get('note')}), but the file has one, written at {feedback_written}"
        )
    elif read_hash != section_sha256(feedback):
        read_written = dig(override, "detail", "feedback_section_generated_at_utc")
        problems.append(
            f"the monitor read the feedback section written at {read_written}, but the file's feedback section "
            f"differs from it (written at {feedback_written})"
        )
    return problems


def read_json(path: Path) -> dict[str, Any] | None:
    """A results file as a dict, or None when it does not exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str | None:
    """SHA-256 of a file, or None when it does not exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_indicators(
    results_dir: Path, rules: dict[str, LevelRule]
) -> tuple[list[Indicator], dict[str, dict[str, Any]]]:
    """Every indicator, plus the SHA-256 of each source file as read (None when absent)."""
    paths = {name: results_dir / name for name in (DOCINTEL_FILE, AGENT_FILE, GOVERNANCE_FILE)}
    sources = {f"results/{name}": {"sha256": file_sha256(path)} for name, path in paths.items()}
    indicators = [
        *extraction_indicators(read_json(paths[DOCINTEL_FILE]), rules),
        *agent_indicators(read_json(paths[AGENT_FILE]), rules),
        *override_indicators(read_json(paths[GOVERNANCE_FILE]), rules),
    ]
    return indicators, sources
