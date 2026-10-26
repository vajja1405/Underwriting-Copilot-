"""Controls on the LLM extraction and the agent, with their measured values, for the governance packs (Phase 6 E).

SR 26-2 footnote 3 places generative and agentic AI outside that guidance, so the packs list these controls on their
own. Values are read from results/agent_metrics.json (scripts/eval_agent.py), results/docintel_metrics.json
(scripts/eval_docintel.py), results/injection_metrics.json and results/injection/injection_metrics_detector_only.json
(scripts/eval_injection.py), and the names of any results/llm_migration_*.json certificates
(scripts/llm_migration_gate.py). Every LLM-dependent value that was not measured says so.
"""

from collections.abc import Mapping
from typing import Any

from uwcopilot.governance.packs import (
    AGENT,
    DOCINTEL,
    INJECTION,
    INJECTION_DETECTOR,
    MIGRATION_CERTIFICATES,
    NOT_MEASURED,
    integer,
    lookup,
    share,
    text,
    value_in,
    wilson_share,
)


def _layer_texts(layers: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(layer, its attack success rate or why it is not measured) for each layer of scripts/eval_injection.py.

    A measured layer holds asr.all = {successes, n, rate, wilson_ci95}; an unmeasured one holds a text in asr, or
    (in the detector-only file) the layer entry is the text itself.
    """
    texts = []
    for name, entry in layers.items():
        rate = lookup(entry, "asr.all") if isinstance(entry, Mapping) else None
        if isinstance(rate, Mapping):
            texts.append((name, wilson_share(rate)))
        else:
            texts.append((name, text(lookup(entry, "asr") if isinstance(entry, Mapping) else entry)))
    return texts


def _grouped(texts: list[tuple[str, str]]) -> str:
    """'L0, L1, L2: <text>' when every layer says the same, else 'L0: <text>; L1: <text>'."""
    values = {value for _, value in texts}
    if len(values) == 1:
        return f"{', '.join(name for name, _ in texts)}: {values.pop()}"
    return "; ".join(f"{name}: {value}" for name, value in texts)


def _injection_layers(sources: Mapping[str, Any]) -> str:
    """Per-layer attack success rates when results/injection_metrics.json exists, else why they are not measured."""
    layers = value_in(sources, INJECTION, "layers")
    if isinstance(layers, Mapping) and layers:
        return _grouped(_layer_texts(layers))
    recorded = value_in(sources, INJECTION_DETECTOR, "layers")
    if isinstance(recorded, Mapping) and recorded:
        detail = f"; the detector-only run records {_grouped(_layer_texts(recorded))}"
    else:
        detail = ""
    return f"{NOT_MEASURED}: `results/{INJECTION}` is absent{detail}"


def _migration_certificates(sources: Mapping[str, Any]) -> str:
    """The LLM migration certificates present in results/, or 'not measured'."""
    names = sources.get(MIGRATION_CERTIFICATES) or []
    if not names:
        return f"{NOT_MEASURED} (no `results/llm_migration_*.json` certificate exists: no LLM migration was evaluated)"
    return ", ".join(f"`results/{name}`" for name in names)


def agent_control_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Every control on the agent: human oversight first, then tools, injection, policy and model changes."""
    return oversight_rows(sources) + genai_control_rows(sources)


def oversight_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Human oversight: how each control is enforced and the measured value (with its results file)."""
    path = "deterministic_path"
    agent = f"`results/{AGENT}`"
    return [
        [
            "The agent never approves or declines: every application stops at the human checkpoint",
            "agent/graph.py: human_review always interrupts and only a human's resume value, checked by "
            "agent/hitl.py validate_decision, continues the run; the agent's own role is never authorized for a file "
            "action (security/rbac.py)",
            share(value_in(sources, AGENT, f"{path}.reached_human_checkpoint")),
            agent,
        ],
        [
            "No decision record without a human reviewer",
            "the decision record is written after human_review; agent/decision_record.py chains records by SHA-256",
            f"{integer(value_in(sources, AGENT, f'{path}.decisions_recorded_without_a_human'))} records",
            agent,
        ],
        [
            "Every required escalation (sanctions potential match, detected injection, policy referral) reaches a "
            "human",
            "escalation comes from the deterministic screening and policy steps (agent/policy.py), before the memo is "
            "drafted",
            share(value_in(sources, AGENT, f"{path}.escalation.all_required_escalations")),
            agent,
        ],
    ]


def genai_control_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Controls on tools, injection, the policy and model changes, with the measured value and its results file."""
    agent = f"`results/{AGENT}`"
    return [
        [
            "Tools only from an allow-list; no tool can carry a decision",
            "agent/tool_runner.py: allow-list (tools/registry.py ALLOWED_TOOLS), schema validation, loop guard; "
            "tests/security/test_containment.py",
            text(value_in(sources, INJECTION_DETECTOR, "action_level.status")),
            f"`results/{INJECTION_DETECTOR}`",
        ],
        [
            "Prompt-injection attack success rate by defense layer (frozen payloads)",
            "security/injection_eval.py (needs a model run)",
            _injection_layers(sources),
            f"`results/{INJECTION}`",
        ],
        [
            "Injection tripwire recall on the frozen payloads (a tripwire, not an attack success rate)",
            "agent/injection.py detect",
            wilson_share(value_in(sources, INJECTION_DETECTOR, "detector.recall_raw.all")),
            f"`results/{INJECTION_DETECTOR}`",
        ],
        [
            "Injection detector on the SYNTHETIC applications: recall / false-positive rate",
            "agent/injection.py detect; a detected injection is escalated",
            f"{share(value_in(sources, AGENT, 'injection_detector.recall'))} / "
            f"{share(value_in(sources, AGENT, 'injection_detector.false_positive_rate'))}",
            agent,
        ],
        [
            "Recommendation agrees with the rule-based reference policy (deterministic path)",
            "agent/policy.py decide_recommendation; scripts/eval_agent.py",
            share(value_in(sources, AGENT, "reference_policy.recommendation_agreement")),
            agent,
        ],
        [
            "An LLM or prompt change needs a migration certificate signed by a model_validator",
            "scripts/llm_migration_gate.py (non-inferiority margins in config/release_gate.yaml); security/rbac.py "
            "sign_migration_certificate",
            _migration_certificates(sources),
            "`results/llm_migration_*.json`",
        ],
    ]


def llm_quality_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Quality of the extraction and of the memo: rules extractor measured, LLM paths not measured."""
    docintel = f"`results/{DOCINTEL}`"
    agent = f"`results/{AGENT}`"
    return [
        [
            "Field extraction F1, rules extractor (deterministic; not an LLM)",
            share(value_in(sources, DOCINTEL, "rules.fields.overall.f1")),
            docintel,
        ],
        [
            "Field extraction, LLM (hybrid) extractor vs rules",
            text(value_in(sources, DOCINTEL, "comparisons.rules_vs_hybrid")),
            docintel,
        ],
        ["Extractors run", text(_joined(value_in(sources, DOCINTEL, "run.extractors"))), docintel],
        ["Memo citation accuracy (LLM memo)", text(value_in(sources, AGENT, "llm_dependent.citation_accuracy")), agent],
        [
            "Memo unsupported-claim rate (LLM memo)",
            text(value_in(sources, AGENT, "llm_dependent.unsupported_claim_rate")),
            agent,
        ],
        ["Memo writer used instead", text(value_in(sources, AGENT, "llm_dependent.memo_writer_used_instead")), agent],
        ["Prompt version recorded with every run", text(value_in(sources, AGENT, "versions.prompt_version")), agent],
    ]


def _joined(values: Any) -> Any:
    """A list of strings joined with commas (other values unchanged)."""
    if isinstance(values, list) and values:
        return ", ".join(str(item) for item in values)
    return values
