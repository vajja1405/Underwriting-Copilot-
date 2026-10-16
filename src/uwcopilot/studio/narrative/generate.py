"""render_narrative: the founder narrative of a ledger, provenance-locked.

    result = render_narrative(ledger)                                   # template (default): no model, no network
    result = render_narrative(ledger, "ollama", model="qwen2.5-1.5b-instruct-f16")   # local model, then checks

Flow for a model ("fake", "ollama" or "hf_inference", or any LLMClient passed as `client`):
1. build the request (prompts.build_request): rules, the slots the model may use, the template draft as an example;
2. the model writes a slot-only draft; it is validated (provenance.memo_grammar.validate: digits, number words,
   symbols, unknown or malformed slots, comparatives typed by the model, omitted findings and open claims,
   decision wording, unknown citations, and an assert slot stated with another check's claim or section);
3. a rejected draft gets ONE retry, with the validator's findings sent back to the model;
4. a draft that passes is rendered (provenance.renderer.render): every slot becomes the ledger value in its display
   format with its status suffix and a footnote; the round-trip audit then re-reads every number of the body and
   must find it in the ledger (empty audit), and the bound check (checker.bound_check) must tie every number to a
   claim its own sentence points to, with the threshold's own comparator;
5. if the second draft also fails, the model cannot be reached or errors, or either check flags the rendered text, the
   deterministic TEMPLATE draft (template.py) is rendered instead and fallback_used is True, with the reason.
The template is the default and always works: it is built to pass the validator, and a test checks that it does on
every golden scenario. The rendered text ends with the simulation label and the Studio disclaimer (rule C, rule E).

"hf_inference" goes through the LLM factory, so it is refused unless the owners enabled it explicitly (see
llm/hf_inference_client.py); a refusal is a fallback with the reason, never an error for the founder.
"""

from collections.abc import Callable

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.ollama_client import OllamaOptions
from uwcopilot.provenance.memo_grammar import SLOT, AssertionScope, Violation, validate
from uwcopilot.provenance.renderer import render, round_trip_audit
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.narrative.checker import bound_check
from uwcopilot.studio.narrative.models import AttemptRecord, NarrativeResult
from uwcopilot.studio.narrative.prompts import (
    NARRATIVE_PROMPT_ID,
    build_request,
    clean_draft,
    prompt_sha256,
    retry_messages,
)
from uwcopilot.studio.narrative.template import TEMPLATE_ID, allowed_sections, required_refs, template_draft

TEMPLATE_PROVIDER = "template"
LLM_PROVIDERS: tuple[str, ...] = ("fake", "ollama", "hf_inference")
MAX_ATTEMPTS = 2
# The template draft (the example the model follows) is about 1,200 tokens; a full draft needs about as many.
DEFAULT_MAX_TOKENS = 1600
# The first request is about 12,000-13,000 characters (measured on the first six golden scenarios) and the retry adds
# the rejected draft and the findings; the Ollama client's context guard estimates 1 token per 2 characters, so
# 12,288 tokens leave room for both and the answer.
OLLAMA_NUM_CTX = 12288
OLLAMA_SEED = 42
ClientFactory = Callable[[str, str | None], LLMClient]


class UnknownNarrativeProvider(ValueError):
    """The provider is not template, fake, ollama or hf_inference."""


def assertion_scopes(ledger: Ledger) -> dict[str, AssertionScope]:
    """Each assertion's own claims and sections: what a sentence with its assert slot may be stated with."""
    return {
        item.id: AssertionScope(
            evidence_ids=frozenset(item.evidence_ids),
            section_ids=frozenset(item.section_ids) | ({item.section_id} if item.section_id else set()),
        )
        for item in ledger.assertions
    }


def check_draft(ledger: Ledger, draft: str) -> list[Violation]:
    """Every grammar violation of a slot-only draft against this ledger (empty = valid)."""
    if not draft.strip():
        return [Violation("V6_OMISSION", "", "the draft is empty")]
    claim_ids = {claim.id for claim in ledger.claims}
    assertion_ids = {item.id for item in ledger.assertions}
    return validate(
        draft,
        claim_ids,
        assertion_ids,
        required_refs(ledger),
        allowed_sections(ledger),
        assertion_scopes(ledger),
    )


def default_client(provider: str, model: str | None) -> LLMClient:
    """The LLM client for a provider name, through the factory (cost cap, cache, explicit settings)."""
    options = OllamaOptions(num_ctx=OLLAMA_NUM_CTX, seed=OLLAMA_SEED) if provider == "ollama" else None
    return get_llm_client(provider, model=model, ollama_options=options)


def violation_lines(violations: list[Violation]) -> list[str]:
    """'CODE: span' for each violation."""
    return [f"{item.code}: {item.span}" for item in violations]


def finish(
    ledger: Ledger,
    draft: str,
    provider: str,
    model: str | None,
    prompt_id: str,
    attempts: list[AttemptRecord],
    fallback_reason: str | None,
) -> NarrativeResult:
    """Render a validated draft, audit it and build the result."""
    memo = render(draft, ledger)
    audit = round_trip_audit(memo, ledger)
    llm_asked = provider != TEMPLATE_PROVIDER
    closing = f"Every probability above is {SIMULATION_LABEL}. {DISCLAIMER}"
    text = memo.text + "\n\n" + closing
    assertions_used = [match.group("id") for match in SLOT.finditer(draft) if match.group("kind") == "assert"]
    return NarrativeResult(
        text=text,
        body=memo.body,
        footnotes=memo.footnotes,
        draft=draft,
        audit=audit,
        claims_used=memo.claims_used,
        assertions_used=list(dict.fromkeys(assertions_used)),
        provider=provider,
        model=model,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        attempts=attempts,
        prompt_id=prompt_id,
        prompt_sha256=prompt_sha256(prompt_id),
        llm_prompt_id=NARRATIVE_PROMPT_ID if llm_asked else None,
        llm_prompt_sha256=prompt_sha256(NARRATIVE_PROMPT_ID) if llm_asked else None,
        data_label=ledger.data_label,
    )


def fallback(
    ledger: Ledger, provider: str, model: str | None, attempts: list[AttemptRecord], reason: str
) -> NarrativeResult:
    """The template narrative, recorded as a fallback from the provider that was asked."""
    return finish(ledger, template_draft(ledger), provider, model, TEMPLATE_ID, attempts, reason)


def ask_model(ledger: Ledger, llm: LLMClient, provider: str, model: str | None, max_tokens: int) -> NarrativeResult:
    """Steps 1-5 of the module docstring with one client."""
    system, messages = build_request(ledger, template_draft(ledger))
    attempts: list[AttemptRecord] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = llm.complete(system, messages, max_tokens=max_tokens, temperature=0)
        except (RuntimeError, ValueError, OSError) as error:
            attempts.append(AttemptRecord(attempt=attempt, error=f"{type(error).__name__}: {error}"))
            return fallback(ledger, provider, model, attempts, f"the model call failed ({type(error).__name__})")
        draft = clean_draft(response.text)
        violations = check_draft(ledger, draft)
        attempts.append(
            AttemptRecord(
                attempt=attempt,
                violations=violation_lines(violations),
                latency_s=round(response.latency_s, 3),
                cached=response.cached,
            )
        )
        if not violations:
            result = finish(ledger, draft, provider, response.model, NARRATIVE_PROMPT_ID, attempts, None)
            if result.audit:
                return fallback(ledger, provider, model, attempts, "the rendered draft failed the round-trip audit")
            if bound_check(result.body, ledger, result.claims_used).flagged:
                return fallback(ledger, provider, model, attempts, "the rendered draft failed the bound check")
            return result
        messages = retry_messages(messages, draft, violations)
    return fallback(ledger, provider, model, attempts, "the draft broke the narrative grammar twice")


def render_narrative(
    ledger: Ledger,
    provider: str = TEMPLATE_PROVIDER,
    *,
    model: str | None = None,
    client: LLMClient | None = None,
    client_factory: ClientFactory | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> NarrativeResult:
    """The founder narrative of a ledger (see the module docstring).

    provider: "template" (default; no model), "fake", "ollama" or "hf_inference". client: an LLMClient to use
    instead of building one (tests pass a fake). client_factory: how to build a client from (provider, model);
    defaults to the LLM factory. A provider that cannot be built (server down, disabled, no price) is a fallback.
    """
    if provider == TEMPLATE_PROVIDER and client is None:
        return finish(ledger, template_draft(ledger), TEMPLATE_PROVIDER, None, TEMPLATE_ID, [], None)
    if provider not in LLM_PROVIDERS and client is None:
        raise UnknownNarrativeProvider(f"unknown provider {provider!r}; use template or one of {LLM_PROVIDERS}")
    if client is None:
        try:
            client = (client_factory or default_client)(provider, model)
        except (RuntimeError, ValueError, OSError) as error:
            reason = f"the {provider} client could not be built ({type(error).__name__})"
            attempts = [AttemptRecord(attempt=1, error=f"{type(error).__name__}: {error}")]
            return fallback(ledger, provider, model, attempts, reason)
    asked = provider if provider != TEMPLATE_PROVIDER else str(getattr(client, "provider", "fake"))
    return ask_model(ledger, client, asked, model or getattr(client, "model", None), max_tokens)
