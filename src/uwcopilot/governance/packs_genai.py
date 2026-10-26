"""Controls on the LLM extraction and the agent, with their measured values, for the governance packs (Phase 6 E).

SR 26-2 footnote 3 places generative and agentic AI outside that guidance, so the packs list these controls on their
own. They also list the third-party pretrained, non-generative models (Tesseract OCR, the fastembed retrieval models)
with their recorded use. Values are read from results/agent_metrics.json (scripts/eval_agent.py),
results/docintel_metrics.json (scripts/eval_docintel.py), results/injection_metrics.json and
results/injection/injection_metrics_detector_only.json (scripts/eval_injection.py), and the names of any
results/llm_migration_*.json certificates (scripts/llm_migration_gate.py). Every LLM-dependent value that was not
measured says so.
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
    is_number,
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


# Guideline retrieval modes (rag/retriever.py) that run each pretrained retrieval model (ids in rag/settings.py).
DENSE_MODES: tuple[str, ...] = ("dense", "hybrid", "hybrid_rerank")
RERANK_MODES: tuple[str, ...] = ("hybrid_rerank",)
THIRD_PARTY_MODEL_HEADER = [
    "Third-party pretrained model (non-generative)",
    "What it does here",
    "Ran in the evaluations behind these results",
    "Results file",
]


def _retrieval_use(mode: Any, modes: tuple[str, ...]) -> str:
    """'yes' or 'no' with the retrieval mode the agent evaluation recorded, or 'not measured' without one."""
    if not isinstance(mode, str) or not mode:
        return f"{NOT_MEASURED} (no retrieval mode recorded)"
    used = "yes" if mode in modes else "no"
    return f"{used} (the agent evaluation recorded retrieval mode `{mode}`)"


def third_party_model_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """The third-party pretrained, non-generative models behind numbers in the packs, with their recorded use.

    Tesseract OCR: its version and the pages it read, from results/docintel_metrics.json. The fastembed retrieval
    models: used or not according to the retrieval mode results/agent_metrics.json records. This project uses all
    of them as published and validates none of them.
    """
    ocr_pages = value_in(sources, DOCINTEL, "rules.latency.ocr_pages_total")
    pages = value_in(sources, DOCINTEL, "rules.page_classification.all.denominator")
    if is_number(ocr_pages) and is_number(pages):
        ocr_use = (
            f"{integer(ocr_pages)} of {integer(pages)} pages read by OCR in the extraction evaluation; the rules "
            "extractor's F1 and citation support include those pages"
        )
    else:
        ocr_use = f"{NOT_MEASURED} (no OCR page count in `results/{DOCINTEL}`)"
    version = value_in(sources, DOCINTEL, "provenance.data_snapshots.tesseract_version")
    mode = value_in(sources, AGENT, "configuration.retrieval_mode")
    return [
        [
            f"Tesseract OCR ({text(version)})",
            "Reads pages without a text layer for the rules extractor (`docintel/ocr.py`); its default engine, used "
            "here, is a pretrained LSTM neural network",
            ocr_use,
            f"`results/{DOCINTEL}`",
        ],
        [
            "Dense text embedding model (fastembed; `rag/settings.py` DENSE_MODEL_ID)",
            "Embeds guideline chunks and queries for the agent's guideline search (modes dense, hybrid, hybrid_rerank)",
            _retrieval_use(mode, DENSE_MODES),
            f"`results/{AGENT}`",
        ],
        [
            "Cross-encoder reranker (fastembed; `rag/settings.py` RERANK_MODEL_ID)",
            "Reorders the retrieved guideline chunks (mode hybrid_rerank only)",
            _retrieval_use(mode, RERANK_MODES),
            f"`results/{AGENT}`",
        ],
    ]


def llm_use(sources: Mapping[str, Any]) -> str:
    """Which LLM the results shown used, as the results files record it: the agent's memo writer and the extraction
    provider. 'not measured' when a file is absent, so an absent extraction run never reads as 'no LLM'."""
    memo_writer = text(value_in(sources, AGENT, "configuration.llm"))
    run = value_in(sources, DOCINTEL, "run")
    if not isinstance(run, Mapping):
        provider = NOT_MEASURED
    elif run.get("provider"):
        provider = text(run.get("provider"))
    else:
        provider = f"none (extractors run: {text(_joined(run.get('extractors')))})"
    return f"agent memo writer: {memo_writer}; extraction LLM provider: {provider}"


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
            "Field extraction F1, rules extractor (deterministic rules, not an LLM; scanned pages read by "
            "Tesseract OCR)",
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
