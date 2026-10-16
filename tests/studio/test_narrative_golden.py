"""Tests for the golden founder scenarios and the S4 evaluation script (scripts/eval_studio_evidence.py).

The scenarios are SYNTHETIC and seeded. A real metro runs in whatever mode this machine has (a CI runner without
real-metro snapshots is in limited mode); the tests accept either and check only what must hold in every mode.
"""

import json
from pathlib import Path

import pytest

import eval_studio_evidence
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.llm.ollama_client import OllamaError
from uwcopilot.studio.metros import list_metros
from uwcopilot.studio.narrative import check_draft, plan_sha256, render_narrative, template_draft
from uwcopilot.studio.narrative.evaluation import pooled
from uwcopilot.studio.narrative.golden import (
    QUOTED_RATE_NOTE,
    build_golden_scenario,
    golden_metros,
    golden_specs,
    scenario_seed,
)
from uwcopilot.studio.profiles import list_profiles

N_PATHS = 100


def test_there_are_thirty_six_golden_scenarios_with_the_synthetic_metro_first() -> None:
    specs = golden_specs()
    assert len(specs) == len(list_metros()) * len(list_profiles()) == 36
    assert golden_metros()[0] == "synthetic_metro" and set(golden_metros()) == set(list_metros())
    assert specs[:6] == [("synthetic_metro", profile) for profile in list_profiles()]
    assert len({scenario_seed(metro, profile) for metro, profile in specs}) == 36


def test_a_scenario_is_reproducible_from_its_seed() -> None:
    first = build_golden_scenario("synthetic_metro", "motel", n_paths=N_PATHS, with_path=False)
    second = build_golden_scenario("synthetic_metro", "motel", n_paths=N_PATHS, with_path=False)
    assert plan_sha256(first.case) == plan_sha256(second.case)
    assert first.feasibility == second.feasibility
    assert first.ledger() == second.ledger()
    assert first.case.facts.credit_score is not None  # SYNTHETIC founder facts are attached


def test_a_real_metro_scenario_renders_in_its_current_mode() -> None:
    scenario = build_golden_scenario("houston_tx", "laundromat", n_paths=N_PATHS, with_path=False)
    assert scenario.metro_mode in ("full", "limited")
    assert scenario.notes in ((), (QUOTED_RATE_NOTE,))
    ledger = scenario.ledger()
    assert check_draft(ledger, template_draft(ledger)) == []
    assert render_narrative(ledger).audit == []
    if scenario.site_score is None:
        assert ledger.claim("SITE.overall_score") is None  # no SiteIQ result here: nothing is invented


def test_pooled_rates_carry_wilson_and_bootstrap_intervals() -> None:
    block = pooled([3, 4], [4, 4])
    assert (block["k"], block["n"], block["rate"]) == (7, 8, 0.875)
    assert block["wilson_ci95"][0] < 0.875 < block["wilson_ci95"][1]
    assert block["bootstrap_ci95"]["n_resamples"] == 1000 and block["bootstrap_ci95"]["seed"] == 42
    assert pooled([], [])["bootstrap_ci95"] is None


def test_the_evaluation_script_writes_its_results_with_provenance(tmp_path: Path) -> None:
    output = tmp_path / "studio_evidence_metrics.json"
    assert eval_studio_evidence.main(["--limit", "1", "--n-paths", str(N_PATHS), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    headline = report["headline"]
    assert headline["narrative_numbers_traced_round_trip"]["rate"] == 1.0
    assert headline["narrative_numbers_traced_bound"]["rate"] == 1.0
    assert headline["template_draft_valid"]["k"] == 1
    assert headline["llm_fallback_rate"].startswith("not measured")
    assert headline["hf_inference"].startswith("not measured")
    assert report["llm"]["measured"] is False
    assert report["checker"]["bound"]["recall_by_type"]["wrong_row"]["n"] == 0
    provenance = report["provenance"]
    assert set(provenance["prompts"]) == {
        "founder_narrative_v1",
        "founder_narrative_retry_v1",
        "founder_narrative_template_v1",
    }
    assert provenance["generated_by"] == "scripts/eval_studio_evidence.py"
    assert report["data_label"] == "SYNTHETIC" and report["golden"]["scenarios_run"] == 1
    ledgers = report["ledgers"]
    row = report["per_scenario"][0]["ledger"]
    claims = row["claims"]
    assert ledgers["scenarios"] == 1
    assert ledgers["claims_per_ledger"] == {"min": claims, "median": claims, "max": claims}
    assert sum(ledgers["claims_by_status"].values()) == claims
    assert sum(ledgers["findings_by_check"].values()) == row["assertion_results"]["false"]


def test_an_llm_run_writes_its_own_file_and_measures_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(eval_studio_evidence, "default_client", lambda provider, model: FakeLLM(["Coverage is 9."] * 4))
    assert eval_studio_evidence.default_output("fake").name == "studio_evidence_metrics_fake.json"
    output = tmp_path / "fake.json"
    assert (
        eval_studio_evidence.main(
            ["--provider", "fake", "--limit", "1", "--n-paths", str(N_PATHS), "--output", str(output)]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    llm = report["llm"]
    assert llm["measured"] is True and llm["narratives_asked"] == 1
    assert llm["fallback_rate"]["rate"] == 1.0 and llm["llm_calls"] == 2
    assert llm["violation_code_counts"]["V1_DIGIT"] == 2


def test_a_provider_that_never_answers_is_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(provider: str, model: str | None) -> FakeLLM:
        raise OllamaError("Ollama server not reachable (test)")

    monkeypatch.setattr(eval_studio_evidence, "default_client", unreachable)
    output = tmp_path / "down.json"
    arguments = ["--provider", "fake", "--limit", "1", "--n-paths", str(N_PATHS), "--output", str(output)]
    assert eval_studio_evidence.main(arguments) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["llm"]["measured"] is False and report["llm"]["narratives_asked"] == 1
    assert report["headline"]["llm_fallback_rate"].startswith("not measured")
    assert report["per_scenario"][0]["llm"]["fallback_used"] is True
