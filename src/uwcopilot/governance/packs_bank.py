"""docs/governance/bank_model_risk.md: the model risk management pack aligned to the structure of SR 26-2 (Phase 6 E).

Sections: regulatory basis and scope, sources of the numbers, model inventory, materiality tiering, validation,
ongoing monitoring, outcomes analysis, change management, a SEPARATE section for the LLM extraction and the agent
(SR 26-2 footnote 3 places generative and agentic AI outside its scope), and a map from the guidance's sections to
this project. Every number is read from results/*.json (governance/packs.py); the regulatory facts are those checked
against the local copy of the Federal Reserve PDF (data/raw/regulatory/frb_sr_26_2.pdf).
"""

from collections.abc import Mapping
from typing import Any

from uwcopilot.governance.packs import (
    AGENT,
    APPLICABILITY,
    DATA_STATEMENT,
    DOCINTEL,
    GOVERNANCE,
    INJECTION,
    INJECTION_DETECTOR,
    NOT_MEASURED,
    OWNER,
    RISK,
    SR_26_2,
    SR_26_2_FOOTNOTE_3,
    SR_26_2_LINKS,
    SR_26_2_LOCAL_COPY,
    SR_26_2_NOT_A_MODEL,
    bullets,
    generated_note,
    lookup,
    missing_section_text,
    run_status,
    section_results,
    source_note,
    sources_table,
    table,
    text,
)
from uwcopilot.governance.packs_genai import agent_control_rows, llm_quality_rows
from uwcopilot.governance.packs_governance import (
    champion_challenger_block,
    feedback_block,
    lineage_block,
    monitoring_block,
    outcomes_block,
    registry_rules,
    release_gate_block,
)
from uwcopilot.governance.packs_inventory import INVENTORY, TIER_EXPECTATIONS, TIER_LABELS, TIERING_RULE
from uwcopilot.governance.packs_models import credit_validation, fairness_validation, insurance_validation, model_facts

TITLE = "# Model risk management pack (bank): Underwriting Copilot"

# (results file, section, where the pack uses it) for the "Sources of the numbers" table.
SOURCES_USED: tuple[tuple[str, str | None, str], ...] = (
    (RISK, "credit", "1, 3"),
    (RISK, "insurance_frequency", "1, 3"),
    (RISK, "insurance_severity", "1, 3"),
    (RISK, "fairness_demo", "1, 3"),
    (GOVERNANCE, "monitoring", "1, 4"),
    (GOVERNANCE, "outcomes", "5"),
    (GOVERNANCE, "champion_challenger", "1, 6"),
    (GOVERNANCE, "release_gate", "6"),
    (GOVERNANCE, "lineage", "7"),
    (GOVERNANCE, "feedback", "7"),
    (AGENT, None, "7"),
    (DOCINTEL, None, "7"),
    (INJECTION, None, "7"),
    (INJECTION_DETECTOR, None, "7"),
)


def sr_26_2_rows(sources: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """The guidance's sections (as numbered in the local copy of the attachment) and where this project addresses
    them. The monitoring, outcomes and change-management rows say "not measured yet" until their sections exist."""
    outcomes = run_status(sources, "outcomes", "SYNTHETIC outcomes joined to SIMULATED decisions (section 5)")
    monitoring = run_status(
        sources, "monitoring", "SIMULATED stream on freMTPL2; the credit model is not monitored (data absent)"
    )
    challenger = run_status(sources, "champion_challenger", "SYNTHETIC champion/challenger compared (section 6)")
    gate = run_status(sources, "release_gate", "release gate run against the reviewed baseline (section 6)")
    return [
        (
            "II. Purpose and scope: model definition; most relevant above $30 billion in total assets",
            "Scope (above)",
            "Stated. Whether the guidance applies depends on the institution.",
        ),
        (
            "III. Inherent risk, exposure and purpose; materiality = purpose + exposure",
            "2. Materiality tiering",
            "Rule stated (a design choice). Exposure: none (no portfolio).",
        ),
        ("III. Aggregate model risk (common data, assumptions, methods)", "-", "Not assessed."),
        (
            "III. Effective challenge by objective, independent experts",
            "3. Validation",
            "Not performed: one developer built and tested every model.",
        ),
        (
            "IV. Model development: purpose, data, out-of-sample and out-of-time testing",
            "Model cards; 3. Validation",
            "Credit: out-of-time test. Insurance and German Credit: random split only (no date field).",
        ),
        (
            "IV. Model use: limitations, use beyond the intended purpose",
            "1. Inventory (not for); model card limitations",
            "Documented.",
        ),
        (
            "V. Validation: conceptual soundness",
            "Model cards (methodology); docs/model_cards/validation_report.md",
            "Developer testing only.",
        ),
        (
            "V. Outcomes analysis",
            "5. Outcomes analysis",
            f"Held-out backtests in the model cards; outcome join: {outcomes}.",
        ),
        ("V. Ongoing model monitoring", "4. Ongoing monitoring", f"{monitoring}."),
        (
            "VI. Governance and controls: policies, roles and responsibilities",
            "6. Change management; security/rbac.py",
            "Roles enforced in code; roles are claimed (no identity provider) and one person holds them all. "
            f"Champion/challenger: {challenger}. Release gate: {gate}.",
        ),
        ("VI. Model inventory", "1. Model inventory; model status registry", "Listed."),
        (
            "VI. Documentation",
            "Model cards, validation report, this pack",
            "Rendered from results files; a test compares the committed copies with a fresh render.",
        ),
        (
            "VII. Vendor and other third-party products",
            "Open-source libraries (LightGBM, scikit-learn, statsmodels) and public data",
            "No vendor model is used. Library versions are recorded in each results section's metadata.",
        ),
        (
            "Footnote 3: generative and agentic AI are not within the scope",
            "7. The LLM extraction and the agent",
            "Governed by this project's own controls, not by SR 26-2.",
        ),
    ]


def regulatory_basis() -> list[str]:
    """What the pack is aligned to, how the facts were checked, and what is in and out of scope."""
    return [
        f"**Regulatory basis.** Aligned to the structure of {SR_26_2} ({SR_26_2_LINKS}). The date, the supersession, "
        "the applicability statement, the model definition and footnote 3 were checked against the local copy of the "
        f"Federal Reserve's PDF, `{SR_26_2_LOCAL_COPY}` (cover letter and interagency attachment, both dated April 17, "
        "2026; downloaded by `make data-regulatory`, SHA-256 in `data/raw/checksums.jsonl`). The OCC Bulletin "
        "2026-13 and FDIC FIL-15-2026 identifiers are taken from the agencies' pages listed in "
        'docs/build_prompts.md ("Regulatory references", checked against primary sources on 2026-10-10); they are '
        "not in the local copy and were not checked against it.",
        "",
        "**Scope.**",
        "",
        *bullets(
            [
                "The agencies expect the guidance to be most relevant to banking organizations with over $30 billion "
                'in total assets, and it is risk-based: rigor scales with materiality. It "does not set forth '
                'enforceable standards or prescriptive requirements" (section I).',
                "Models here: the four statistical models of the inventory (section 1).",
                f'Not models under its definition, which excludes "{SR_26_2_NOT_A_MODEL}" (section II): the Phase 4 '
                "financial calculations and reconciliation rules, the agent's rule-based reference policy, the rules "
                "document extractor and the release gate. They are deterministic and are tested, not validated as "
                "models.",
                "Outside its scope: generative and agentic AI (footnote 3). The LLM extraction and the agent are "
                "governed in section 7, not by stretching SR 26-2 to cover them.",
                "Still within its principles: traditional statistical and quantitative models and non-generative, "
                "non-agentic AI models (footnote 3, last sentence).",
            ]
        ),
    ]


def inventory_section(sources: Mapping[str, Any]) -> list[str]:
    """One table per model: purpose, use, data, version, tier, ownership; then the Phase 6 registry entries."""
    lines = ["## 1. Model inventory", ""]
    for entry in INVENTORY:
        tier = entry.tier()
        rows = [
            ["What it estimates", entry.estimates],
            ["Intended use", entry.intended_use],
            ["Not for", entry.not_for],
            *model_facts(sources, entry),
            ["Materiality (section 2)", f"{tier.as_designed_label} as designed; {tier.now_label} now ({tier.reason})"],
            ["Owner", OWNER],
            [
                "Independent validation",
                "Not performed. Developer testing only (docs/model_cards/validation_report.md).",
            ],
            ["Approval for use", "None. Demonstration only; a person makes every decision."],
            ["Documentation", entry.documentation],
        ]
        lines += [
            f"### {entry.name}",
            "",
            *table(["Item", "Value"], rows),
            "",
            source_note(sources, RISK, entry.results_section),
            "",
        ]
    lines += [
        "### Models registered by the Phase 6 demonstrations",
        "",
        "The model status registry (`data/governance/model_status.json`, gitignored) also holds the models the Phase 6 "
        "runs train for their demonstrations. They are not used by the copilot.",
        "",
        *table(["Model id", "Registered by", "Status after the run"], phase6_registry_rows(sources)),
        "",
        source_note(sources, GOVERNANCE, "monitoring"),
        "",
        source_note(sources, GOVERNANCE, "champion_challenger"),
        "",
    ]
    return lines


def phase6_registry_rows(sources: Mapping[str, Any]) -> list[list[str]]:
    """Registry entries recorded by the monitoring and champion/challenger sections."""
    rows: list[list[str]] = []
    monitoring = section_results(sources, GOVERNANCE, "monitoring")
    if isinstance(monitoring, Mapping):
        status = monitoring.get("model_status") or {}
        rows.append([f"`{text(status.get('model_id'))}`", "scripts/monitor.py", text(status.get("status_after_run"))])
    else:
        rows.append([missing_section_text(GOVERNANCE, "monitoring"), "scripts/monitor.py", NOT_MEASURED])
    challenge = section_results(sources, GOVERNANCE, "champion_challenger")
    models = lookup(challenge, "registry.models") if isinstance(challenge, Mapping) else None
    if isinstance(models, Mapping) and models:
        for role, entry in models.items():
            rows.append(
                [
                    f"`{text(entry.get('model_id'))}`",
                    f"scripts/champion_challenger.py ({role})",
                    text(entry.get("status_in_registry")),
                ]
            )
    else:
        rows.append(
            [missing_section_text(GOVERNANCE, "champion_challenger"), "scripts/champion_challenger.py", NOT_MEASURED]
        )
    return rows


def tiering_section() -> list[str]:
    """The tiering rule and its result for every model."""
    rows = []
    for entry in INVENTORY:
        tier = entry.tier()
        rows.append(
            [
                entry.name,
                f"`{entry.purpose}`",
                tier.as_designed_label,
                tier.now_label,
                TIER_EXPECTATIONS[tier.tier_as_designed],
            ]
        )
    expectations = [f"{TIER_LABELS[level]}: {TIER_EXPECTATIONS[level]}." for level in sorted(TIER_EXPECTATIONS)]
    return [
        "## 2. Materiality tiering",
        "",
        "The rule (`src/uwcopilot/governance/packs_inventory.py`):",
        "",
        *bullets(TIERING_RULE),
        "",
        "What each tier would require before and during real use (design choices):",
        "",
        *bullets(expectations),
        "",
        *table(
            ["Model", "Purpose as designed", "Tier as designed", "Tier now", "What the tier as designed requires"], rows
        ),
    ]


def validation_section(sources: Mapping[str, Any]) -> list[str]:
    """Measured validation metrics with bootstrap CIs, per model."""
    return [
        "## 3. Validation",
        "",
        "Developer testing on held-out data; not an independent validation. Effective challenge by an independent "
        "party was not performed. The findings of that testing are in docs/model_cards/validation_report.md.",
        "",
        "### Credit default (Lending Club)",
        "",
        *credit_validation(sources),
        "",
        source_note(sources, RISK, "credit"),
        "",
        "### Motor claim frequency (freMTPL2)",
        "",
        *insurance_validation(sources, "frequency"),
        "",
        source_note(sources, RISK, "insurance_frequency"),
        "",
        "### Motor claim severity (freMTPL2)",
        "",
        *insurance_validation(sources, "severity"),
        "",
        source_note(sources, RISK, "insurance_severity"),
        "",
        "### Fairness testing demonstration (German Credit)",
        "",
        *fairness_validation(sources),
        "",
        "Adverse impact ratios and equal-opportunity differences are in docs/governance/insurance_ai_program.md "
        "(section 5) and in the credit model card.",
        "",
        source_note(sources, RISK, "fairness_demo"),
    ]


def monitoring_section(sources: Mapping[str, Any]) -> list[str]:
    """The monitoring plan and the Phase 6 monitoring run."""
    return [
        "## 4. Ongoing monitoring",
        "",
        "The plan per model (population stability, calibration, discrimination, overrides) is in each model card. "
        "Phase 6 runs it on a SIMULATED stream: every threshold is in `config/monitoring.yaml`; a breach writes an "
        "alert record and marks the monitored model `under_review` in the model status registry.",
        "",
        *monitoring_block(sources),
    ]


def outcomes_section(sources: Mapping[str, Any]) -> list[str]:
    """Held-out backtests (model cards) and the Phase 6 outcome join."""
    return [
        "## 5. Outcomes analysis",
        "",
        "At validation: observed against predicted by predicted decile on the held-out test split, in each model "
        "card (section 6). After decisions: outcomes joined to the decisions that were made (Phase 6), below.",
        "",
        *outcomes_block(sources),
    ]


def change_management_section(sources: Mapping[str, Any]) -> list[str]:
    """Registry rules, champion/challenger, release gate and versioning."""
    return [
        "## 6. Change management",
        "",
        "### Model status registry and who may promote",
        "",
        *registry_rules(),
        "",
        "### Champion/challenger",
        "",
        *champion_challenger_block(sources),
        "",
        "### Release gate",
        "",
        "`scripts/release_gate.py` compares the latest results with a reviewed baseline (`results/baseline/`) and "
        "fails the CI job when a critical metric regresses beyond the tolerance stated in `config/release_gate.yaml` "
        "(Phase 6 D).",
        "",
        *release_gate_block(sources),
        "",
        "### Versions recorded with every decision",
        "",
        "Each decision record carries the model version, the dataset checksums, the prompt file hashes and the "
        "eval-set version (`governance/lineage.py`); `make lineage` prints them and flags any that no longer match "
        "the working tree (section 7).",
    ]


def genai_section(sources: Mapping[str, Any]) -> list[str]:
    """The LLM extraction and the agent: outside SR 26-2, governed by the project's own controls."""
    return [
        "## 7. The LLM extraction and the agent: governed outside SR 26-2",
        "",
        f'SR 26-2, footnote 3 in full (local copy, section II): "{SR_26_2_FOOTNOTE_3}"',
        "",
        "The LLM field extraction (`docintel/extract_llm*.py`) and the underwriting agent (`agent/graph.py`: tool "
        "calls and the memo) are generative and agentic AI, so SR 26-2 does not cover them. Following the footnote's "
        "third sentence, the project governs them with its own controls: a human decides every file, the agent may "
        "only call allow-listed tools, document text is data and never instructions, and a change of LLM or prompt "
        "is to be certified by `scripts/llm_migration_gate.py` and signed by a model_validator before release (no "
        "such change has been evaluated here). No LLM was available for the "
        "results below: every LLM-dependent measure says not measured.",
        "",
        "### Controls on the agent",
        "",
        *table(["Control", "How it is enforced", "Measured value", "Results file"], agent_control_rows(sources)),
        "",
        "### Quality of the extraction and of the memo",
        "",
        *table(["Measure", "Value", "Results file"], llm_quality_rows(sources)),
        "",
        source_note(sources, AGENT),
        "",
        source_note(sources, DOCINTEL),
        "",
        source_note(sources, INJECTION),
        "",
        source_note(sources, INJECTION_DETECTOR),
        "",
        "### Decision lineage",
        "",
        *lineage_block(sources),
        "",
        "### Reviewer feedback (SIMULATED reviewer)",
        "",
        *feedback_block(sources),
    ]


def mapping_section(sources: Mapping[str, Any]) -> list[str]:
    """Map from the guidance's sections to this project (structure only; no compliance claim)."""
    return [
        "## 8. Map to the structure of SR 26-2",
        "",
        'Section numbers are those of the local copy of the attachment. "Status here" describes this demonstration '
        "only; it is not an assessment against the guidance.",
        "",
        *table(["SR 26-2 (section)", "Where in this project", "Status here"], sr_26_2_rows(sources)),
    ]


def render_bank_model_risk(sources: Mapping[str, Any]) -> str:
    """docs/governance/bank_model_risk.md as text."""
    lines = [
        TITLE,
        "",
        *generated_note(),
        "",
        "**What this is.** A demonstration of how a model risk management pack could be laid out for the models of "
        "this repository. "
        f"{APPLICABILITY} {DATA_STATEMENT}",
        "",
        *regulatory_basis(),
        "",
        "## Sources of the numbers",
        "",
        "Every number below is read from these files when the pack is rendered. The git commit is the one the "
        "producing script recorded in the file. The committed copy of this pack is checked by "
        "`tests/test_render_reports.py` to equal a fresh render of the results files committed with it.",
        "",
        *sources_table(sources, SOURCES_USED),
        "",
        *inventory_section(sources),
        *tiering_section(),
        "",
        *validation_section(sources),
        "",
        *monitoring_section(sources),
        "",
        *outcomes_section(sources),
        "",
        *change_management_section(sources),
        "",
        *genai_section(sources),
        "",
        *mapping_section(sources),
        "",
        "## Applicability",
        "",
        APPLICABILITY,
    ]
    return "\n".join(lines) + "\n"
