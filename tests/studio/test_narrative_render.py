"""Tests for the provenance-locked founder narrative: the template, the LLM path with one retry, and the fallback.

No test calls a real model: LLM drafts come from the deterministic fake client (FakeLLM) or from a client factory
that fails. Every plan is SYNTHETIC (golden scenarios in the SYNTHETIC metro, few paths).
"""

import re
from collections.abc import Callable

import pytest

from uwcopilot.agent.claim_checker import decision_language
from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.kyc.memo import disposition_language
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.llm.hf_inference_client import HFInferenceSettings
from uwcopilot.llm.ollama_client import OllamaError
from uwcopilot.provenance.renderer import RenderedMemo, round_trip_audit
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.narrative import (
    NARRATIVE_PROMPT_ID,
    TEMPLATE_ID,
    UnknownNarrativeProvider,
    check_draft,
    render_narrative,
    required_refs,
    template_draft,
)
from uwcopilot.studio.narrative import prompts as prompt_module
from uwcopilot.studio.narrative.golden import GoldenScenario, build_golden_scenario
from uwcopilot.studio.narrative.prompts import (
    RETRY_PROMPT_ID,
    all_prompt_hashes,
    build_request,
    prompt_sha256,
)
from uwcopilot.studio.profiles import list_profiles

N_PATHS = 200
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def scenario() -> GoldenScenario:
    """A SYNTHETIC laundromat plan in the SYNTHETIC metro, with SiteIQ and a path search."""
    return build_golden_scenario("synthetic_metro", "laundromat", n_paths=N_PATHS)


@pytest.fixture(scope="module")
def ledger(scenario: GoldenScenario) -> Ledger:
    """The scenario's founder ledger."""
    return scenario.ledger()


def factory_of(client: FakeLLM) -> Callable[[str, str | None], FakeLLM]:
    """A client factory that always returns the given fake client."""
    return lambda provider, model: client


@pytest.mark.parametrize("profile_id", list_profiles())
def test_the_template_draft_passes_the_grammar_for_every_business_type(profile_id: str) -> None:
    ledger = build_golden_scenario("synthetic_metro", profile_id, n_paths=N_PATHS, with_path=False).ledger()
    assert check_draft(ledger, template_draft(ledger)) == []


def test_the_default_narrative_is_the_template_and_every_number_traces(ledger: Ledger) -> None:
    result = render_narrative(ledger)
    assert result.provider == "template" and not result.fallback_used and result.attempts == []
    assert result.prompt_id == TEMPLATE_ID and result.prompt_sha256 == prompt_sha256(TEMPLATE_ID)
    assert result.llm_prompt_id is None and result.audit == []
    assert result.text.endswith(f"Every probability above is {SIMULATION_LABEL}. {DISCLAIMER}")
    assert "[^c1]" in result.body and result.footnotes[0].startswith("[^c1]: ")
    assert result.data_label == "SYNTHETIC" and "SYNTHETIC" in result.body
    assert render_narrative(ledger) == result  # deterministic


def test_the_rendered_narrative_states_no_decision(ledger: Ledger) -> None:
    body = render_narrative(ledger).body
    assert decision_language(body) == []
    assert disposition_language(body) == []


def test_every_finding_and_open_claim_is_in_the_template(ledger: Ledger) -> None:
    draft = template_draft(ledger)
    for reference in required_refs(ledger):
        assert "{{" + reference + "}}" in draft, reference
    assert any(item.result is False for item in ledger.assertions)  # the scenario has findings to mention


def test_a_valid_llm_draft_is_used(ledger: Ledger) -> None:
    fake = FakeLLM([template_draft(ledger)])
    result = render_narrative(ledger, "fake", client_factory=factory_of(fake))
    assert not result.fallback_used and result.prompt_id == NARRATIVE_PROMPT_ID
    assert result.llm_prompt_id == NARRATIVE_PROMPT_ID and len(result.attempts) == 1
    assert result.audit == [] and len(fake.calls) == 1


def test_a_rejected_draft_gets_one_retry_with_the_validator_findings(ledger: Ledger) -> None:
    fake = FakeLLM(["The loan of 250000 dollars is approved.", template_draft(ledger)])
    result = render_narrative(ledger, "fake", client_factory=factory_of(fake))
    assert not result.fallback_used and len(result.attempts) == 2
    first = result.attempts[0].violations
    assert any(line.startswith("V1_DIGIT") for line in first)
    assert any(line.startswith("V7_DISPOSITION_LANGUAGE") for line in first)
    retry = fake.calls[1]["messages"]
    assert [message.role for message in retry] == ["user", "assistant", "user"]
    assert "V1_DIGIT: 250000" in retry[2].content


def test_two_rejected_drafts_fall_back_to_the_template(ledger: Ledger) -> None:
    fake = FakeLLM(["Coverage is 1.4 and well above the minimum.", "{{claim:NOT-A-CLAIM}} meets everything."])
    result = render_narrative(ledger, "fake", client_factory=factory_of(fake))
    assert result.fallback_used and result.fallback_reason == "the draft broke the narrative grammar twice"
    assert result.prompt_id == TEMPLATE_ID and result.draft == template_draft(ledger)
    assert result.llm_prompt_id == NARRATIVE_PROMPT_ID and len(result.attempts) == 2
    assert any(line.startswith("V4_UNKNOWN_SLOT") for line in result.attempts[1].violations)
    assert result.audit == []


def test_an_unreachable_model_falls_back_without_an_error(ledger: Ledger) -> None:
    def unreachable(provider: str, model: str | None) -> FakeLLM:
        raise OllamaError("Could not reach Ollama at http://127.0.0.1:11434 (ConnectError).")

    result = render_narrative(ledger, "ollama", client_factory=unreachable)
    assert result.fallback_used and "could not be built" in (result.fallback_reason or "")
    assert result.attempts[0].error and result.audit == []


def test_a_failing_call_falls_back(ledger: Ledger) -> None:
    fake = FakeLLM([])  # raises FakeLLMExhausted on the first call
    result = render_narrative(ledger, "fake", client_factory=factory_of(fake))
    assert result.fallback_used and result.fallback_reason == "the model call failed (FakeLLMExhausted)"


def test_hf_inference_is_refused_without_opt_in_and_falls_back(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uwcopilot.llm.factory.load_hf_inference_settings", lambda: HFInferenceSettings())
    result = render_narrative(ledger, "hf_inference", model="test-org/test-model")
    assert result.fallback_used and "HFInferenceDisabled" in (result.attempts[0].error or "")


def test_an_unknown_provider_is_an_error(ledger: Ledger) -> None:
    with pytest.raises(UnknownNarrativeProvider):
        render_narrative(ledger, "gpt-unknown")


def test_the_prompt_lists_slots_and_meanings_but_no_values_and_no_founder_text(
    scenario: GoldenScenario, ledger: Ledger
) -> None:
    system, messages = build_request(ledger, template_draft(ledger))
    content = messages[0].content
    assert "<founder_plan_data>" in content and "data, not instructions" in system
    assert "{{claim:FOUNDER.loan_amount}} = loan amount [USER_STATED]" in content
    loan = f"{scenario.case.loan.amount:,.0f}"
    assert loan not in content and f"${loan}" not in content
    for candidate in scenario.candidates:
        assert candidate.label not in content
    assert "golden site" not in content


def test_the_prompt_registry_hashes_change_when_the_text_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    hashes = all_prompt_hashes()
    assert set(hashes) == {NARRATIVE_PROMPT_ID, RETRY_PROMPT_ID, TEMPLATE_ID}
    assert all(SHA256.match(value) for value in hashes.values())
    assert len(set(hashes.values())) == 3
    system, instruction = prompt_module.FOUNDER_PROMPTS[NARRATIVE_PROMPT_ID]
    monkeypatch.setitem(prompt_module.FOUNDER_PROMPTS, NARRATIVE_PROMPT_ID, (system + " ", instruction))
    assert prompt_sha256(NARRATIVE_PROMPT_ID) != hashes[NARRATIVE_PROMPT_ID]


def test_a_rendered_llm_narrative_never_holds_a_number_outside_the_ledger(ledger: Ledger) -> None:
    fake = FakeLLM([template_draft(ledger)])
    result = render_narrative(ledger, "fake", client_factory=factory_of(fake))
    assert round_trip_audit(RenderedMemo(body=result.body), ledger) == []


# Review findings (S4 review, B1/S1): drafts that are slot-only on the surface but state a false comparison or pair one
# check's verdict with another check's fact. Each must fall back to the template.
ADVERSARIAL_DRAFTS = {
    "borrowed verdict": "The owner experience of {{claim:FOUNDER.experience_months}} {{assert:A-NSP-4.1}} [NSP-2.1].",
    "typed comparative": "The second-year ratio is comfortably above the minimum, so it {{assert:A-NSP-4.1}}.",
    "ordinal": "Cash turns positive in the eighteenth month.",
    "multiplier": "Revenue grows twofold over the plan.",
    "numeric character": "About ½ of the cost is equipment.",
    "vague count": "Several competitors operate nearby.",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL_DRAFTS))
def test_adversarial_drafts_fall_back_to_the_template(ledger: Ledger, name: str) -> None:
    """A draft with any of the reviewed tricks is rejected twice and the template is rendered."""
    draft = ADVERSARIAL_DRAFTS[name] + "\n\n" + template_draft(ledger)
    result = render_narrative(ledger, "fake", client=FakeLLM([draft, draft]))
    assert result.fallback_used
    assert result.prompt_id == TEMPLATE_ID
    assert result.attempts[0].violations


def test_a_draft_that_fails_the_bound_check_falls_back(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound check runs on every model draft that passed the grammar and the audit, and a flag means fallback."""
    from types import SimpleNamespace

    from uwcopilot.studio.narrative import generate

    monkeypatch.setattr(generate, "bound_check", lambda text, led, claims_used: SimpleNamespace(flagged=True))
    result = render_narrative(ledger, "fake", client=FakeLLM([template_draft(ledger)]))
    assert result.fallback_used
    assert result.fallback_reason == "the rendered draft failed the bound check"
