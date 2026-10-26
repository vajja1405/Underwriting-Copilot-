"""Tests for the README headline table (src/uwcopilot/governance/readme_headlines.py, Phase 8).

Ground rule 1 for this table: every number in a row appears in the results file the row names (not just somewhere
in some results file), a row never reports a value its file marks "not measured", a "not measured" row gives the
reason recorded in the file, and rows reporting SYNTHETIC or SIMULATED results say so. The checks run on small
hand-made results with distinctive values, and on the committed results files (skipped when none exist). They reuse
the traceability helpers of tests/test_render_reports.py.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from test_render_reports import displayed_forms, json_leaves, numbers_in, without_json_strings
from uwcopilot.governance import readme_headlines as headlines
from uwcopilot.governance.packs import lookup

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results"
HAVE_COMMITTED_RESULTS = any((RESULTS / name).exists() for name in headlines.HEADLINE_FILES)

# The only numbers the table's own text may hold (with no results file at all): "Recall@5", "MRR@10", "year-2",
# "alerts per 1,000" and the "95%" of the column header and the introduction.
ALLOWED_LABEL_NUMBERS = {"5", "10", "2", "1,000", "95%"}
MISSING_PATH = re.compile(r"^no `[^`]+` in the file$")
DATA_LABEL_KEYS = ("data_label", "synthetic_data")
LABEL_WORDS = ("SYNTHETIC", "SIMULATED")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def all_rows(sources: dict[str, Any]) -> list[headlines.Headline]:
    """Every row of every section."""
    return [row for _, rows in headlines.headline_rows(sources) for row in rows]


def list_lengths(value: Any) -> Iterator[int]:
    """The length of every list in a JSON value: a count such as '17 checks' is read off the file this way."""
    if isinstance(value, list):
        yield len(value)
        for item in value:
            yield from list_lengths(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from list_lengths(item)


def marked_not_measured(value: Any) -> bool:
    """True when a results file marks this value "not measured" (a text, or a dict whose status says so)."""
    if isinstance(value, dict):
        value = value.get("status")
    return isinstance(value, str) and value.startswith("not measured")


def file_reason(reason: str, strings: list[str]) -> bool:
    """True when a "not measured" reason is one the module writes itself or comes from a text in the file."""
    if reason in (headlines.NO_FILE, headlines.NO_REASON) or MISSING_PATH.match(reason):
        return True
    return any(reason in string for string in strings)


def untraced_numbers(row: headlines.Headline, data: Any) -> set[str]:
    """Numbers in the row that are not a displayed form of a number in its source file.

    Numbers may come from the file's numeric values, its numeric keys and the lengths of its lists; the labels
    (component and metric) may also hold ALLOWED_LABEL_NUMBERS. A "not measured" reason and long texts copied from
    the file (descriptions, model names) are removed first, after checking the reason is the file's.
    """
    numbers, strings = json_leaves(data)
    allowed = displayed_forms(numbers + [float(length) for length in list_lengths(data)])
    value = row.value
    if value.startswith("not measured ("):
        reason = value[len("not measured (") : -1]
        assert file_reason(reason, strings), f"reason not from {row.source}: {reason!r}"
        value = value.replace(reason, " ")
    labels = without_json_strings(f"{row.component} | {row.metric}", strings)
    untraced = {token for token in numbers_in(without_json_strings(value, strings)) if token not in allowed}
    return untraced | {token for token in numbers_in(labels) if token not in allowed | ALLOWED_LABEL_NUMBERS}


def nearest_label_words(data: Any, path: str) -> set[str]:
    """SYNTHETIC / SIMULATED in the data label nearest to path (the deepest level of the path that has one)."""
    keys = path.split(".")
    for depth in range(len(keys), -1, -1):
        level = lookup(data, ".".join(keys[:depth])) if depth else data
        if isinstance(level, dict):
            labels = [str(level[key]) for key in DATA_LABEL_KEYS if isinstance(level.get(key), str)]
            if labels:
                return {word for word in LABEL_WORDS if any(word in label for label in labels)}
    return set()


def check_rows(sources: dict[str, Any]) -> None:
    """Every rule of the module docstring, for every row rendered from sources."""
    for row in all_rows(sources):
        data = sources.get(row.source)
        if data is None:
            assert row.value == f"not measured ({headlines.NO_FILE})", row
            continue
        assert untraced_numbers(row, data) == set(), row
        values = [lookup(data, path) for path in row.paths]
        not_measured_in_file = any(value is None or marked_not_measured(value) for value in values)
        assert row.value.startswith("not measured") == not_measured_in_file, row
        if not not_measured_in_file:
            text = f"{row.component} {row.metric} {row.value}"
            for path in row.paths:
                for word in nearest_label_words(data, path):
                    assert word in text, (word, row)


# ---------------------------------------------------------------------------
# Hand-made results with distinctive values
# ---------------------------------------------------------------------------


def bootstrap(estimate: float, low: float, high: float, **extra: Any) -> dict[str, Any]:
    """A bootstrap result as risk/bootstrap.py writes it."""
    return {"estimate": estimate, "ci_low": low, "ci_high": high, "n_resamples": 1000, "seed": 42, **extra}


def wilson(k: int, n: int, low: float, high: float) -> dict[str, Any]:
    """A Wilson-interval rate as the Studio evaluations write it."""
    return {"k": k, "n": n, "rate": k / n, "wilson_ci95": [low, high]}


def hand_made_sources() -> dict[str, Any]:
    """A few results files, in the shapes their scripts write, with values that appear nowhere else."""
    no_llm = "not measured: no LLM provider was used (no key configured); no LLM call was made"
    return {
        headlines.AGENT: {
            "data_label": "SYNTHETIC",
            "deterministic_path": {
                "escalation": {"all_required_escalations": {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "n": 57}},
                "end_to_end_routing_agreement_with_designed_labels": {
                    "value": 0.71113,
                    "ci_low": 0.61117,
                    "ci_high": 0.81119,
                    "n": 63,
                },
                "decisions_recorded_without_a_human": 0,
            },
            "llm_dependent": {"citation_accuracy": no_llm},
        },
        headlines.GOVERNANCE: {
            "monitoring": {
                "results": {
                    "data_label": "SYNTHETIC",
                    "validation": {
                        "no_drift": {
                            "description": "SIMULATED stream with no injected drift.",
                            "data_label": "SIMULATED",
                            "false_alert_rate": bootstrap(
                                0.00337,
                                0.0,
                                0.01013,
                                numerator=1,
                                denominator=297,
                                exact_ci_low=0.00009,
                                exact_ci_high=0.01861,
                                bootstrap_interval_note="1 of 297 streams: read the exact interval",
                            ),
                        }
                    },
                    "credit_model": {"status": "not measured", "reason": "SYNTHETIC reason: no credit data. More."},
                }
            },
            "feedback": {
                "results": {
                    "data_label": "SYNTHETIC applications; SIMULATED reviewer actions",
                    "summary": {"override_rate": {"value": 0.14141, "ci_low": 0.06161, "ci_high": 0.23131, "k": 9}},
                }
            },
        },
        headlines.STUDIO_EVIDENCE_OLLAMA: {
            "data_label": "SYNTHETIC",
            "llm": {
                "provider": "ollama",
                "model": "tiny-model-7",
                "fallback_rate": wilson(5, 7, 0.35553, 0.91117),
                "first_try_valid_rate": wilson(1, 7, 0.02573, 0.51319),
                "valid_after_retry_rate": wilson(2, 7, 0.08221, 0.64113),
                "llm_calls": 13,
            },
        },
        headlines.WEB: {
            "runs": {
                "local-container": {
                    "e2e": {"flows": [{"name": "signed_out", "passed": True}, {"name": "lender", "passed": False}]},
                    "axe": {"totals": {"scans": 31, "scans_with_serious_or_critical": 3}},
                }
            }
        },
    }


def row_with(sources: dict[str, Any], source: str, metric: str) -> headlines.Headline:
    """The row of one results file with this metric label."""
    return next(row for row in all_rows(sources) if row.source == source and row.metric == metric)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_without_results_every_row_is_not_measured_and_the_text_holds_no_metric() -> None:
    rows = all_rows({})
    assert rows and all(row.value == "not measured (no results file)" for row in rows)
    assert {row.source for row in rows} == set(headlines.HEADLINE_FILES)
    text = "\n".join(headlines.headline_section({}))
    assert numbers_in(text) <= ALLOWED_LABEL_NUMBERS


def test_hand_made_results_render_with_their_own_interval() -> None:
    sources = hand_made_sources()
    agent = headlines.AGENT
    assert row_with(sources, agent, "End-to-end routing agreement with the designed labels").value == (
        "0.7111 [0.6112, 0.8112] (n=63)"
    )
    # A zero-width bootstrap interval is not shown as an interval.
    escalations = row_with(sources, agent, "Required escalations that reached the right human queue").value
    assert escalations == "1.0000 (n=57, zero-width bootstrap interval, no other interval recorded)"
    assert row_with(sources, agent, "Decisions recorded without a human").value == "0"
    # The file says the bootstrap interval is too narrow, so the exact interval is shown.
    false_alerts = row_with(sources, headlines.GOVERNANCE, "Streams with a false alert")
    assert false_alerts.value == "0.0034 exact [0.0001, 0.0186] (1/297)"
    assert "SIMULATED stream with no injected drift." in false_alerts.component
    override = row_with(sources, headlines.GOVERNANCE, "Override rate (describes the simulation)")
    assert override.value == "0.1414 [0.0616, 0.2313]"  # no total recorded, so no counts
    web = row_with(sources, headlines.WEB, "Playwright end-to-end flows")
    assert web.value == "2 flows; failed: lender"
    # A record that does not say passed=true counts as failed.
    assert headlines.checks_text([{"name": "health", "passed": True}, "odd record"]) == "2 checks; failed: unnamed"
    assert headlines.checks_text([{"name": "health", "passed": True}]) == "1 check, all passed"
    assert row_with(sources, headlines.WEB, "axe-core scans with a serious or critical issue").value == "3 of 31 scans"


def test_not_measured_rows_give_the_files_reason() -> None:
    sources = hand_made_sources()
    llm = row_with(sources, headlines.AGENT, "Citation accuracy")
    assert llm.value == "not measured (no LLM provider was used (no key configured))"
    credit = row_with(sources, headlines.GOVERNANCE, "Drift and calibration checks")
    assert credit.value == "not measured (SYNTHETIC reason: no credit data)"
    space = [row for row in all_rows(sources) if row.component == "Web app on the Hugging Face Space"]
    assert [row.value for row in space] == ["not measured (no `runs.hf-space.e2e.flows` in the file)"]
    # A section the file does not have is "not measured", not left out.
    gate = row_with(sources, headlines.GOVERNANCE, "Verdict and gate rows by status")
    assert gate.value == "not measured (no `release_gate.results` in the file)"


def test_a_run_on_a_handful_of_cases_shows_counts_only() -> None:
    row = row_with(hand_made_sources(), headlines.STUDIO_EVIDENCE_OLLAMA, "Drafts that passed the narrative grammar")
    assert row.value == (
        "counts only: 5/7 scenarios fell back to the template; drafts valid on the first try 1/7, after a retry 2/7 "
        "(ollama tiny-model-7, 13 calls)"
    )
    assert "[" not in row.value and "0.7143" not in row.value


def test_hand_made_rows_pass_every_check() -> None:
    check_rows(hand_made_sources())


def test_the_checks_catch_a_typed_number_a_foreign_reason_and_a_missing_label() -> None:
    sources = hand_made_sources()
    data = sources[headlines.AGENT]
    row = row_with(sources, headlines.AGENT, "End-to-end routing agreement with the designed labels")
    typed = headlines.Headline(row.component, row.metric, row.value + " (0.9876 in a typed note)", row.source)
    assert untraced_numbers(typed, data) == {"0.9876"}
    # A number from another results file does not count: 0.1414 is in governance_metrics.json, not here.
    borrowed = headlines.Headline(row.component, row.metric, "0.1414", row.source)
    assert untraced_numbers(borrowed, data) == {"0.1414"}
    invented = headlines.Headline(row.component, row.metric, "not measured (an invented reason)", row.source)
    with pytest.raises(AssertionError, match="reason not from"):
        untraced_numbers(invented, data)
    assert nearest_label_words(sources[headlines.GOVERNANCE], "feedback.results.summary.override_rate") == {
        "SYNTHETIC",
        "SIMULATED",
    }


def test_reason_layouts_and_short_reasons() -> None:
    assert headlines.not_measured_reason({"status": "not measured", "reason": "no data. Details."}) == "no data"
    assert headlines.not_measured_reason("not measured (no LLM key)") == "no LLM key"
    assert headlines.not_measured_reason({"status": "not measured (no LLM key)", "note": "x"}) == "no LLM key"
    assert headlines.not_measured_reason("not measured: needs a run (pending); later") == "needs a run (pending)"
    assert headlines.not_measured_reason({"status": "measured"}) is None
    assert headlines.not_measured_reason(0.0) is None
    # A cut that would leave a bracket open is not made.
    assert headlines.short_reason("a (b; c) d") == "a (b; c) d"


def test_interval_layouts() -> None:
    assert headlines.interval_text({"mean": 0.90781, "ci95": [0.85441, 0.95631]}) == "0.9078 [0.8544, 0.9563]"
    kyc = {"value": 103.77, "ci95_low": 89.93, "ci95_high": 118.86}
    assert headlines.one_decimal_interval(kyc) == "103.8 [89.9, 118.9]"
    provenance = {"n": 55, "detected": 6, "rate": 0.1091, "wilson_ci95": [0.051, 0.2183]}
    assert headlines.interval_text(provenance) == "0.1091 [0.0510, 0.2183] (6/55, Wilson)"
    degenerate_with_wilson = {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "k": 36, "n": 36, "wilson_ci95": [0.9, 1.0]}
    assert headlines.interval_text(degenerate_with_wilson) == "1.0000 [0.9000, 1.0000] (36/36, Wilson)"
    assert headlines.interval_text({"value": 0.5}) == "0.5000 (no interval recorded)"
    assert headlines.interval_text({"note": "no value"}) == "not measured (no value recorded)"


def test_readme_section_lists_each_row_with_its_source_link() -> None:
    lines = headlines.headline_section(hand_made_sources())
    assert lines[0].startswith("**Headline metric per results file.**")
    assert "| Component | Metric | Value [95% CI] | Source |" in lines
    assert [line for line in lines if line.startswith("**") and line.endswith("**")] == [
        "**Lender Desk**",
        "**Governance**",
        "**Founder Studio**",
        "**Web app and deployment**",
    ]
    web_row = next(line for line in lines if "Playwright end-to-end flows" in line and "LOCAL" in line)
    assert web_row.endswith("| [web_metrics.json](results/web_metrics.json) |")


@pytest.mark.skipif(not HAVE_COMMITTED_RESULTS, reason="no committed results files")
def test_committed_results_pass_every_check() -> None:
    """The table as `make reports` writes it: every number traces to the row's own results file."""
    sources = headlines.load_sources(RESULTS)
    check_rows(sources)
    measured = [row for row in all_rows(sources) if not row.value.startswith("not measured")]
    assert measured  # the committed files hold measured headline metrics
