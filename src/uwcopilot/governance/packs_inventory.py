"""Model inventory and materiality tiering for the governance packs (Phase 6 E).

The inventory lists the statistical models of this repository: "models" in the sense of SR 26-2 (a complex
quantitative method that applies statistical, economic, or financial theories to process input data into
quantitative estimates). The descriptive fields (purpose, intended use, not for) are declared here and match the
model cards; every number about a model is read from results/risk_metrics.json when a pack is rendered.

Materiality tiering rule (a DESIGN CHOICE of this project, not a rule taken from any guidance)
--------------------------------------------------------------------------------------------
SR 26-2 (section III) says model purpose, together with model exposure, determines model materiality. The rule
therefore has two inputs per model:

  purpose, as designed (what the output would be used for if the copilot were deployed):
    consumer_decision     the output is an input to a decision about one applicant or policyholder (credit,
                          underwriting or pricing). Adverse-action and unfair-discrimination risk sit with it.
    financial_risk        the output measures or prices financial risk for a portfolio (provisioning, capital,
                          reserving) without deciding on a person.
    method_demonstration  the model only demonstrates a method; its output is never used for a decision.
  exposure: whether the output touches a real book. No model here has any (no portfolio, no production use).

  tier as designed:  Tier 1 (high) for consumer_decision, Tier 2 (medium) for financial_risk, Tier 3 (low) for
                     method_demonstration.
  tier now:          the tier as designed when the model has exposure; Tier 3 when it has none. SR 26-2 allows a
                     model to be deemed immaterial on exposure and purpose, with risk management that identifies it
                     and monitors the conditions under which it may become material. That is the status of every
                     model in this repository; the tier as designed says what a real deployment would need first.

TIER_EXPECTATIONS lists what each tier would require (also design choices).
"""

from dataclasses import dataclass
from typing import Literal

Purpose = Literal["consumer_decision", "financial_risk", "method_demonstration"]

TIER_BY_PURPOSE: dict[str, int] = {"consumer_decision": 1, "financial_risk": 2, "method_demonstration": 3}
TIER_LABELS: dict[int, str] = {1: "Tier 1 (high)", 2: "Tier 2 (medium)", 3: "Tier 3 (low)"}
PURPOSE_TEXT: dict[str, str] = {
    "consumer_decision": "an input to a decision about one applicant or policyholder (credit, underwriting, pricing)",
    "financial_risk": "measures or prices financial risk for a portfolio without deciding on a person",
    "method_demonstration": "demonstrates a method only; its output is never used for a decision",
}
TIER_EXPECTATIONS: dict[int, str] = {
    1: (
        "validation independent of the developer before first use; ongoing monitoring with alert thresholds "
        "(config/monitoring.yaml); outcomes analysis once outcomes mature; champion/challenger before any "
        "replacement, promoted only by a model_validator; required rows in the release gate; review at least yearly"
    ),
    2: (
        "validation before first use by a qualified reviewer who did not build the model; ongoing monitoring; "
        "review at least every two years"
    ),
    3: (
        "listed in the inventory; the conditions under which its use would become material are monitored; "
        "never used for a decision"
    ),
}
TIERING_RULE: tuple[str, ...] = (
    "Inputs: the model's purpose as designed and whether it has exposure to a real book (SR 26-2, section III: "
    "purpose together with exposure determines materiality).",
    f"Purpose consumer_decision ({PURPOSE_TEXT['consumer_decision']}): {TIER_LABELS[1]} as designed.",
    f"Purpose financial_risk ({PURPOSE_TEXT['financial_risk']}): {TIER_LABELS[2]} as designed.",
    f"Purpose method_demonstration ({PURPOSE_TEXT['method_demonstration']}): {TIER_LABELS[3]}.",
    "Tier now: the tier as designed if the model has exposure, otherwise Tier 3. No model in this repository has "
    "exposure (no portfolio, no production use), so every model is Tier 3 now and is handled as an immaterial model: "
    "identified in the inventory and monitored for the conditions under which it would become material.",
    "This rule is a design choice of the project, not a rule from SR 26-2 or any other guidance.",
)


@dataclass(frozen=True)
class TierAssessment:
    """The result of the tiering rule for one model."""

    tier_as_designed: int
    tier_now: int
    reason: str

    @property
    def as_designed_label(self) -> str:
        """e.g. 'Tier 1 (high)'."""
        return TIER_LABELS[self.tier_as_designed]

    @property
    def now_label(self) -> str:
        """e.g. 'Tier 3 (low)'."""
        return TIER_LABELS[self.tier_now]


def materiality_tier(purpose: str, has_exposure: bool) -> TierAssessment:
    """Apply the tiering rule (module docstring). ValueError for an unknown purpose."""
    if purpose not in TIER_BY_PURPOSE:
        raise ValueError(f"unknown purpose {purpose!r}; known purposes are {sorted(TIER_BY_PURPOSE)}")
    designed = TIER_BY_PURPOSE[purpose]
    now = designed if has_exposure else 3
    exposure = "exposure to a real book" if has_exposure else "no exposure: no portfolio, no production use"
    return TierAssessment(designed, now, f"purpose `{purpose}`; {exposure}")


@dataclass(frozen=True)
class InventoryEntry:
    """One model in the inventory: what it is for, where its results are and its documentation."""

    key: str
    name: str
    results_section: str
    purpose: Purpose
    estimates: str
    intended_use: str
    not_for: str
    documentation: str
    has_exposure: bool = False

    def tier(self) -> TierAssessment:
        """The tiering rule applied to this model."""
        return materiality_tier(self.purpose, self.has_exposure)


INVENTORY: tuple[InventoryEntry, ...] = (
    InventoryEntry(
        key="credit_pd",
        name="Credit default (probability of default), Lending Club",
        results_section="credit",
        purpose="consumer_decision",
        estimates=(
            "The lifetime probability that an individual unsecured personal loan of the Lending Club type is charged "
            "off or defaults (finished loans)."
        ),
        intended_use=(
            "Decision support: the /score API returns the PD, a risk band, reason codes and scenario results to an "
            "underwriter, who decides."
        ),
        not_for="Automated approval or decline, pricing, 12-month PD for IFRS 9 / CECL / Basel capital.",
        documentation="docs/model_cards/credit_model_card.md, docs/model_cards/validation_report.md",
    ),
    InventoryEntry(
        key="insurance_frequency",
        name="Motor claim frequency, freMTPL2",
        results_section="insurance_frequency",
        purpose="consumer_decision",
        estimates="Expected claims per policy-year for one motor third-party liability policy.",
        intended_use=(
            "Decision support and stress testing in the copilot (/score with scenario overrides); with severity it "
            "gives the expected loss an underwriter reviews."
        ),
        not_for="Rate filings, pricing a real book, any portfolio other than the public freMTPL2 data.",
        documentation="docs/model_cards/insurance_model_card.md, docs/model_cards/validation_report.md",
    ),
    InventoryEntry(
        key="insurance_severity",
        name="Motor claim severity, freMTPL2",
        results_section="insurance_severity",
        purpose="consumer_decision",
        estimates="Expected average claim amount per claim on a capped basis; frequency x severity = expected loss.",
        intended_use="Decision support: the severity factor of the expected loss shown to an underwriter.",
        not_for="Rate filings, reserving, large-loss estimation (amounts are capped).",
        documentation="docs/model_cards/insurance_model_card.md, docs/model_cards/validation_report.md",
    ),
    InventoryEntry(
        key="fairness_demo",
        name="Fairness testing demonstration, UCI German Credit",
        results_section="fairness_demo",
        purpose="method_demonstration",
        estimates=(
            "The probability of bad credit, only so that approval rates per group can be compared (adverse impact "
            "ratio, equal-opportunity difference, proxy check)."
        ),
        intended_use="Demonstrates the fairness tests; the approve-if-below-threshold rule exists only for the test.",
        not_for="Any credit decision; any conclusion about US fair-lending law (German data).",
        documentation="docs/model_cards/credit_model_card.md (section Fairness), docs/model_cards/validation_report.md",
    ),
)


def inventory_entry(key: str) -> InventoryEntry:
    """The inventory entry with this key (KeyError if there is none)."""
    for entry in INVENTORY:
        if entry.key == key:
            return entry
    raise KeyError(key)
