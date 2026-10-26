"""docs/governance/insurance_ai_program.md: an AIS Program outline mapped to the NAIC Model Bulletin (Phase 6 E).

Sections: regulatory basis, sources of the numbers, the AI systems in scope, governance, risk management and internal
controls, third-party AI systems and data, fairness testing, and a map to the program elements of the NAIC Model
Bulletin on the Use of Artificial Intelligence Systems by Insurers.

The bulletin itself could not be downloaded (HTTP 403, docs/rag_design.md), so its text was not checked against a
local copy: the outline uses only the summary recorded in docs/build_prompts.md ("Regulatory references") and quotes
no section of the bulletin. New York DFS Circular Letter No. 7 was checked against its local copy. Every number is
read from results/*.json (governance/packs.py).
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
    NAIC_ADOPTION_MAP,
    NAIC_BULLETIN,
    NOT_MEASURED,
    NY_DFS_CL7,
    NY_DFS_LOCAL_COPY,
    OWNER,
    RISK,
    bullets,
    generated_note,
    interval,
    run_status,
    source_note,
    sources_table,
    table,
    text,
    value_in,
)
from uwcopilot.governance.packs_genai import genai_control_rows, llm_quality_rows, oversight_rows
from uwcopilot.governance.packs_governance import (
    feedback_block,
    lineage_block,
    monitoring_block,
    registry_rules,
    release_gate_block,
)
from uwcopilot.governance.packs_inventory import TIERING_RULE, inventory_entry, materiality_tier
from uwcopilot.governance.packs_models import FAIRNESS_HEADER, fairness_rows, insurance_validation, model_facts

TITLE = "# AI Systems (AIS) Program outline (insurance): Underwriting Copilot"

SOURCES_USED: tuple[tuple[str, str | None, str], ...] = (
    (RISK, "insurance_frequency", "1, 3"),
    (RISK, "insurance_severity", "1, 3"),
    (RISK, "fairness_demo", "1, 5"),
    (GOVERNANCE, "monitoring", "3"),
    (GOVERNANCE, "release_gate", "3"),
    (GOVERNANCE, "lineage", "3"),
    (GOVERNANCE, "feedback", "2"),
    (AGENT, None, "1, 2, 3, 4"),
    (DOCINTEL, None, "1, 3, 4"),
    (INJECTION, None, "3"),
    (INJECTION_DETECTOR, None, "3"),
)


def naic_rows(sources: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """The program elements as summarized in docs/build_prompts.md, and where this outline covers them. The risk
    management row says which Phase 6 runs exist ("not measured yet" until their sections are generated)."""
    monitoring = run_status(sources, "monitoring", "measured (section 3)")
    gate = run_status(sources, "release_gate", "run against the reviewed baseline (section 3)")
    return [
        (
            "A written AIS Program",
            "This outline",
            "An outline rendered from results files; not adopted by any insurer or board.",
        ),
        (
            "Governance",
            "2. Governance",
            "Roles enforced in code (security/rbac.py); roles are claimed and one person holds them all.",
        ),
        (
            "Risk management and internal controls",
            "3. Risk management and internal controls",
            "Validation measured on public data (developer testing). Monitoring on a SIMULATED stream: "
            f"{monitoring}. Release gate: {gate}.",
        ),
        (
            "Third-party AI systems",
            "4. Third-party AI systems and data",
            "No third-party AI system produced these results; the expectations for one are listed.",
        ),
        (
            "In proportion to the potential harm to consumers",
            "1. AI systems in scope (tiering)",
            "Rule stated (a design choice); no system has exposure.",
        ),
        (
            "Fairness testing (from NY DFS Circular Letter No. 7, not from the NAIC summary)",
            "5. Fairness testing",
            "Demonstrated on German Credit; not measurable on freMTPL2 (no protected attributes).",
        ),
    ]


def regulatory_basis() -> list[str]:
    """The bulletin as summarized in the repository, and what was and was not checked."""
    return [
        "**Regulatory basis.**",
        "",
        *bullets(
            [
                f"{NAIC_BULLETIN}. Its text was **not checked against a local copy**: the scripted download returned "
                "HTTP 403 (docs/rag_design.md). This outline therefore uses only the summary in docs/build_prompts.md "
                '("Regulatory references", checked against primary sources on 2026-10-10): insurers are expected to '
                "keep a written AIS Program covering governance, risk management and internal controls, and "
                "third-party AI systems, in proportion to the potential harm to consumers. The NAIC "
                f"{NAIC_ADOPTION_MAP} dated October 8, 2026 lists 29 jurisdictions, and the adopted wording varies by "
                "state. No section of the bulletin is quoted here.",
                f"{NY_DFS_CL7}, checked against the local copy `{NY_DFS_LOCAL_COPY}`: it names the adverse impact "
                "ratio among the statistical metrics an insurer may use, says an insurer may not rely solely on a "
                "third party's claim of non-discrimination, limits quantitative assessments to protected classes for "
                "which data are available or may be reasonably imputed, and adds a clause drawn from the NAIC bulletin "
                "on terms in contracts with third-party vendors.",
            ]
        ),
    ]


def scope_section(sources: Mapping[str, Any]) -> list[str]:
    """The AI systems in scope with their tier (same rule as the bank pack)."""
    rows = []
    for key in ("insurance_frequency", "insurance_severity", "fairness_demo"):
        entry = inventory_entry(key)
        tier = entry.tier()
        facts = dict(model_facts(sources, entry))
        rows.append([entry.name, entry.estimates, tier.as_designed_label, tier.now_label, facts["Results"]])
    genai_tier = materiality_tier("consumer_decision", has_exposure=False)
    extractors = value_in(sources, DOCINTEL, "run.extractors")
    extractor_text = ", ".join(str(item) for item in extractors) if isinstance(extractors, list) else NOT_MEASURED
    rows += [
        [
            "Document extraction (rules extractor; LLM extractor optional)",
            "Reads SYNTHETIC document packets and fills application fields with page citations.",
            genai_tier.as_designed_label,
            genai_tier.now_label,
            f"extractors run: {extractor_text}",
        ],
        [
            "Underwriting agent (recommendation and memo; a human decides)",
            "Runs allow-listed tools, applies the reference policy, drafts a memo and stops at the human checkpoint.",
            genai_tier.as_designed_label,
            genai_tier.now_label,
            text(value_in(sources, AGENT, "status")),
        ],
    ]
    return [
        "## 1. AI systems in scope",
        "",
        "The tiering rule is the bank pack's (`src/uwcopilot/governance/packs_inventory.py`), applied to every AI "
        "system so that controls scale with the potential harm to consumers:",
        "",
        *bullets(TIERING_RULE),
        "",
        *table(["AI system", "What it does", "Tier as designed", "Tier now", "Status of its results"], rows),
        "",
        source_note(sources, RISK, "insurance_frequency"),
        "",
        source_note(sources, AGENT),
    ]


def governance_section(sources: Mapping[str, Any]) -> list[str]:
    """Accountability, roles, human oversight, documentation and reviewer feedback."""
    return [
        "## 2. Governance",
        "",
        "### Accountability and roles",
        "",
        *bullets(
            [
                f"Owner of every AI system here: {OWNER}. There is no board, committee or second line: one person "
                "builds, tests and reviews, which is the main governance gap of this demonstration.",
                "Roles enforced in code (`security/rbac.py`): an `underwriter` approves up to a threshold and refers; "
                "only a `senior_underwriter` declines; only a `model_validator` promotes a model or signs an LLM "
                "migration certificate; an `admin` manages permissions and reads audit logs but can never approve, "
                "decline or promote. The agent's own role is never authorized for any of these actions.",
                "Roles are claimed in this demonstration (no identity provider): the code shows how authority is "
                "enforced once a role is known, not who someone is.",
            ]
        ),
        "",
        "### Human oversight of every decision",
        "",
        *table(["Control", "How it is enforced", "Measured value", "Results file"], oversight_rows(sources)),
        "",
        "### Documentation",
        "",
        *bullets(
            [
                "Model cards: docs/model_cards/insurance_model_card.md; validation findings: "
                "docs/model_cards/validation_report.md. Both are rendered from results files.",
                "Model inventory and materiality: docs/governance/bank_model_risk.md, sections 1 and 2.",
                "Agent and extraction design: docs/agent_design.md, docs/security.md.",
            ]
        ),
        "",
        "### Reviewer feedback",
        "",
        "Every review records accept or override, reason codes from a fixed list (`config/override_reasons.yaml`) "
        "plus free text, and the reviewer's role. Measured on a SIMULATED reviewer only:",
        "",
        *feedback_block(sources),
    ]


def risk_management_section(sources: Mapping[str, Any]) -> list[str]:
    """Data, validation, monitoring, change management, lineage and the controls on generative AI."""
    return [
        "## 3. Risk management and internal controls",
        "",
        "### Data",
        "",
        *bullets(
            [
                f"Frequency data: {text(value_in(sources, RISK, 'insurance_frequency.results.dataset'))}. Severity "
                f"data: {text(value_in(sources, RISK, 'insurance_severity.results.dataset'))}.",
                "The freMTPL2 files are downloaded by script (`make data`), never committed; their SHA-256 checksums "
                "are logged in `data/raw/checksums.jsonl` and copied into the metadata of the insurance results "
                "sections.",
                f"Time: {text(value_in(sources, RISK, 'insurance_frequency.results.out_of_time_test'))}",
                "Data-quality findings (for example claims without a recorded amount) are in "
                "docs/model_cards/validation_report.md and docs/insurance_data_notes.md.",
            ]
        ),
        "",
        "### Validation (developer testing, not independent)",
        "",
        "Claim frequency:",
        "",
        *insurance_validation(sources, "frequency"),
        "",
        source_note(sources, RISK, "insurance_frequency"),
        "",
        "Claim severity:",
        "",
        *insurance_validation(sources, "severity"),
        "",
        source_note(sources, RISK, "insurance_severity"),
        "",
        "### Ongoing monitoring",
        "",
        "The Phase 6 monitor watches a freMTPL2 claim-frequency GLM on a SIMULATED stream (thresholds in "
        "`config/monitoring.yaml`; a breach writes an alert record and marks the model `under_review`):",
        "",
        *monitoring_block(sources),
        "",
        "### Change management",
        "",
        *registry_rules(),
        "",
        *release_gate_block(sources),
        "",
        "### Decision lineage",
        "",
        *lineage_block(sources),
        "",
        "### Controls on the generative and agentic AI",
        "",
        *table(["Control", "How it is enforced", "Measured value", "Results file"], genai_control_rows(sources)),
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
    ]


def third_party_section(sources: Mapping[str, Any]) -> list[str]:
    """Third-party data and models used, and what a vendor AI system would need."""
    provider = value_in(sources, DOCINTEL, "run.provider")
    data_rows = [
        ["freMTPL2freq / freMTPL2sev", "Claim frequency and severity models", "OpenML 41214 / 41215; OpenML lists CC0"],
        [
            text(value_in(sources, RISK, "fairness_demo.results.dataset.name")),
            "Fairness testing demonstration",
            f"UCI; license {text(value_in(sources, RISK, 'fairness_demo.results.dataset.license'))}",
        ],
        ["OFAC SDN list", "Sanctions screening in the agent", "US Treasury; US government public data"],
        ["OpenSanctions (default)", "Sanctions screening in the agent", "CC BY-NC 4.0; data from OpenSanctions.org"],
    ]
    return [
        "## 4. Third-party AI systems and data",
        "",
        *table(["Third-party data", "Used for", "Source and terms"], data_rows),
        "",
        *bullets(
            [
                "Third-party AI models: none produced any number in this outline. Agent memo writer: "
                f"{text(value_in(sources, AGENT, 'configuration.llm'))}. Extraction LLM provider: "
                f"{text(provider) if provider else 'none (rules extractor only)'}.",
                "The repository holds client code for a local Ollama model and for hosted LLM APIs; none was called "
                "to produce these results, and none is described here as having been used.",
                "If a vendor AI system were used, the program would require: due diligence on its development data, "
                "method and measured performance; the same evaluation, release gate and migration certificate as an "
                "in-house model; contract terms on audit, change notice and cooperation (NY DFS Circular Letter No. "
                "7 adds a clause from the NAIC bulletin on vendor contract terms); and the insurer's own fairness "
                "testing, because an insurer may not rely solely on a vendor's claim of non-discrimination.",
            ]
        ),
        "",
        source_note(sources, AGENT),
    ]


def fairness_section(sources: Mapping[str, Any]) -> list[str]:
    """Why fairness is not measurable on freMTPL2, and the method demonstrated on German Credit."""
    proxy_rows = [
        [
            name,
            interval(value_in(sources, RISK, f"fairness_demo.results.proxy_check.{name}.test_auc"), 3),
        ]
        for name in ("sex", "age_lt25")
    ]
    caveats = value_in(sources, RISK, "fairness_demo.results.caveats")
    warning_path = "fairness_demo.results.data_quality_warning"
    return [
        "## 5. Fairness testing",
        "",
        *bullets(
            [
                f"Claim frequency and severity: freMTPL2 has no protected attributes, so an adverse impact ratio "
                f"cannot be computed: {NOT_MEASURED}. NY DFS Circular Letter No. 7 limits quantitative assessments to "
                "protected classes for which data are available or may be reasonably imputed. Region, Area and Density "
                f"are geographic rating features that can act as proxies; a proxy assessment on freMTPL2: "
                f"{NOT_MEASURED}.",
                f"Fairness monitoring over time: {NOT_MEASURED} (no protected attributes in the monitored data).",
                "The method (adverse impact ratio, equal-opportunity difference, proxy check, each with a bootstrap "
                "95% CI) is demonstrated on UCI German Credit, a different dataset (German credit data, not "
                "insurance).",
            ]
        ),
        "",
        *table(FAIRNESS_HEADER, fairness_rows(sources)),
        "",
        "Proxy check: how well the model's own features (protected attributes excluded) predict the protected "
        "attribute, as test AUC [95% CI]:",
        "",
        *table(["Protected attribute", "Proxy test AUC [95% CI]"], proxy_rows),
        "",
        f"- Data-quality warning recorded with the demo: {text(value_in(sources, RISK, warning_path))}",
        *bullets(text(item) for item in (caveats if isinstance(caveats, list) else [])),
        "",
        source_note(sources, RISK, "fairness_demo"),
    ]


def mapping_section(sources: Mapping[str, Any]) -> list[str]:
    """Map from the program elements (as summarized) to this outline."""
    return [
        "## 6. Map to the NAIC Model Bulletin's program elements",
        "",
        "Elements as summarized in docs/build_prompts.md (the bulletin text was not checked against a local copy). "
        '"Status here" describes this demonstration only; it is not an assessment against the bulletin.',
        "",
        *table(["Program element (as summarized)", "Where in this outline", "Status here"], naic_rows(sources)),
    ]


def render_insurance_ai_program(sources: Mapping[str, Any]) -> str:
    """docs/governance/insurance_ai_program.md as text."""
    lines = [
        TITLE,
        "",
        *generated_note(),
        "",
        "**What this is.** An outline of the written AIS Program the NAIC Model Bulletin expects an insurer to keep, "
        "filled in for the AI systems of this repository. No insurer has adopted it. "
        f"{APPLICABILITY} {DATA_STATEMENT}",
        "",
        *regulatory_basis(),
        "",
        "## Sources of the numbers",
        "",
        "Every number below is read from these files when the outline is rendered. The git commit is the one the "
        "producing script recorded in the file. The committed copy of this outline is checked by "
        "`tests/test_render_reports.py` to equal a fresh render of the results files committed with it.",
        "",
        *sources_table(sources, SOURCES_USED),
        "",
        *scope_section(sources),
        "",
        *governance_section(sources),
        "",
        *risk_management_section(sources),
        "",
        *third_party_section(sources),
        "",
        *fairness_section(sources),
        "",
        *mapping_section(sources),
        "",
        "## Applicability",
        "",
        APPLICABILITY,
    ]
    return "\n".join(lines) + "\n"
