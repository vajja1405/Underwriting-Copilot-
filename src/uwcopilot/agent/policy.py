"""Deterministic reference policy: the SYNTHETIC guideline thresholds as code, with a section-id citation per rule.

evaluate_policy(application, inputs) returns a PolicyResult with:
- recommendation: approve / refer / decline_for_review / request_more_information. It is a recommendation for a
  human; nothing here approves or declines anything (NBC-7.1, HM-9.1);
- findings: every rule that did not pass, with its guideline sections, referral trigger ids (R-NBC-xx, R-HM-xx),
  who must review it, and the evidence ids (calculations, application facts, tool results) behind its numbers;
- checks_passed: the rules that passed, with the same kind of citations, so a memo can explain an approval too;
- calculations: every number the rules used (DSCR, loss ratios, ...), with its inputs;
- not_evaluated: rules this policy cannot check from the structured data, and why. Nothing is silently skipped.

Recommendation precedence (documented in docs/agent_design.md):
1. any blocking referral (Compliance, Fraud Operations, Special Investigations Unit, KYC Operations) -> "refer":
   while it is open the file cannot be approved, closed or declined (NBC-6.3, NBC-7.3, HM-8.2, HM-9.3);
2. any decline ground -> "decline_for_review" (a senior underwriter must confirm; never an automatic decline);
3. missing documents or data -> "request_more_information";
4. any other referral trigger -> "refer";
5. otherwise -> "approve" (a person with authority still has to approve).

The line-specific rules live in policy_credit.py (Northwind), policy_insurance.py (Harborline) and
policy_startup.py (the SYNTHETIC Northwind startup program, corpus/programs/). Startup files always need a senior
underwriter (NSP-9.1), whatever the recommendation.

Phase 4 adds PolicyInputs.document_checks: confirmed Phase 2 document checks (balance continuity, tax return vs
P&L, name / FEIN / owner consistency, loss-run totals) raise the findings in DOCUMENT_CHECK_RULES
(check_document_findings). The list is empty by default, and then nothing changes.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.applications import (
    AnyApplication,
    CreditApplication,
    StartupCreditApplication,
    screened_parties,
)
from uwcopilot.agent.vocab import (
    BLOCKING_FUNCTIONS,
    COMPLIANCE,
    FRAUD_OPERATIONS,
    KYC_OPERATIONS,
    REFERRAL_PRIORITY,
    SENIOR_UNDERWRITER,
    SPECIAL_INVESTIGATIONS,
    UNDERWRITER,
    UNDERWRITING_MANAGER,
    Recommendation,
    ReviewerRole,
)

POLICY_VERSION = "reference-policy-v1 (northwind_business_credit 2026.1, harborline_mutual_commercial 2026.1)"
FindingOutcome = Literal["refer", "decline_for_review", "request_more_information"]
CalcUnit = Literal["usd", "ratio", "pct", "months", "count", "days", "score", "usd_per_sqft", "probability"]
# NBC-8.1: a model probability of default above 12.0% is band E and a referral (R-NBC-12).
PD_REFERRAL_THRESHOLD = 0.12

# Section ids and trigger ids for the rules every line shares, by line. The startup program keeps the Northwind
# sanctions and injection rules unchanged (NSP-7.3), so startup files cite the same sections and triggers.
SANCTIONS_SECTIONS = {
    "credit": ["NBC-6.3", "NBC-7.3"],
    "insurance": ["HM-8.2", "HM-9.3"],
    "startup_credit": ["NBC-6.3", "NBC-7.3"],
}
SANCTIONS_TRIGGER = {"credit": "R-NBC-10", "insurance": "R-HM-13", "startup_credit": "R-NBC-10"}
INJECTION_SECTIONS = {
    "credit": ["NBC-9.2", "NBC-7.3"],
    "insurance": ["HM-11.2", "HM-9.3"],
    "startup_credit": ["NBC-9.2", "NBC-7.3"],
}
INJECTION_TRIGGER = {"credit": "R-NBC-13", "insurance": "R-HM-16", "startup_credit": "R-NBC-13"}
INJECTION_REFER_TO = {
    "credit": FRAUD_OPERATIONS,
    "insurance": SPECIAL_INVESTIGATIONS,
    "startup_credit": FRAUD_OPERATIONS,
}
# NSP-9.1: lines whose files always need a senior underwriter, whatever the recommendation.
SENIOR_REVIEW_LINES = frozenset({"startup_credit"})
INJECTION_EVIDENCE_ID = "CHK-injection_screen"


class DocumentCheckRule(BaseModel):
    """What a confirmed document check (Phase 2, docintel/checks.py) raises: sections, trigger, reviewers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_ids: tuple[str, ...]
    trigger_id: str
    refer_to: tuple[str, ...]
    blocking: bool


# Document checks the policy turns into findings, by line (Phase 4). The startup program applies NBC-6.1, NBC-6.2
# and NBC-9.1 unchanged (NSP-7.3), so startup files use the Northwind table.
# - NBC-9.1: a balance that does not continue, or tax-return receipts more than 10.0% from the P&L, is a document
#   integrity indicator: Fraud Operations, blocking (R-NBC-13).
# - NBC-6.2: a business name or FEIN that differs between documents is an identity verification failure:
#   KYC Operations, blocking (R-NBC-09).
# - NBC-6.1: ownership that differs between documents: Senior Underwriter and KYC Operations, blocking (R-NBC-08).
# - HM-11.1: loss-run "totals that do not equal the sum of the lines": the SIU, blocking (R-HM-16).
# - HM-8.1: a named insured or ownership that cannot be verified: Underwriting Manager (R-HM-12, not blocking).
# Other check ids (revenue gap, statement months, required documents, TIV, claims history, paid/reserve per claim,
# account conduct, debt payments) are NOT listed: the policy already evaluates those problems from the
# application itself, so a flag for them is accepted and ignored. Embedded instructions enter through
# PolicyInputs.injection_flags (field "doc:<doc_id>:p<page>"), not through document checks.
NORTHWIND_DOCUMENT_CHECKS: dict[str, DocumentCheckRule] = {
    "balance_continuity": DocumentCheckRule(
        section_ids=("NBC-9.1", "NBC-7.3"), trigger_id="R-NBC-13", refer_to=(FRAUD_OPERATIONS,), blocking=True
    ),
    "tax_return_vs_pnl": DocumentCheckRule(
        section_ids=("NBC-9.1", "NBC-7.3"), trigger_id="R-NBC-13", refer_to=(FRAUD_OPERATIONS,), blocking=True
    ),
    "name_consistency": DocumentCheckRule(
        section_ids=("NBC-6.2", "NBC-7.3"), trigger_id="R-NBC-09", refer_to=(KYC_OPERATIONS,), blocking=True
    ),
    "fein_consistency": DocumentCheckRule(
        section_ids=("NBC-6.2", "NBC-7.3"), trigger_id="R-NBC-09", refer_to=(KYC_OPERATIONS,), blocking=True
    ),
    "owner_consistency": DocumentCheckRule(
        section_ids=("NBC-6.1", "NBC-7.3"),
        trigger_id="R-NBC-08",
        refer_to=(SENIOR_UNDERWRITER, KYC_OPERATIONS),
        blocking=True,
    ),
}
HARBORLINE_DOCUMENT_CHECKS: dict[str, DocumentCheckRule] = {
    "loss_run_totals": DocumentCheckRule(
        section_ids=("HM-11.1", "HM-9.3"), trigger_id="R-HM-16", refer_to=(SPECIAL_INVESTIGATIONS,), blocking=True
    ),
    "name_consistency": DocumentCheckRule(
        section_ids=("HM-8.1", "HM-9.3"), trigger_id="R-HM-12", refer_to=(UNDERWRITING_MANAGER,), blocking=False
    ),
    "fein_consistency": DocumentCheckRule(
        section_ids=("HM-8.1", "HM-9.3"), trigger_id="R-HM-12", refer_to=(UNDERWRITING_MANAGER,), blocking=False
    ),
    "owner_consistency": DocumentCheckRule(
        section_ids=("HM-8.1", "HM-9.3"), trigger_id="R-HM-12", refer_to=(UNDERWRITING_MANAGER,), blocking=False
    ),
}
DOCUMENT_CHECK_RULES: dict[str, dict[str, DocumentCheckRule]] = {
    "credit": NORTHWIND_DOCUMENT_CHECKS,
    "insurance": HARBORLINE_DOCUMENT_CHECKS,
    "startup_credit": NORTHWIND_DOCUMENT_CHECKS,
}
DOCUMENT_RULE_PREFIX = "document_"
# Named indicators a check can carry (DocumentCheckFlag.indicators) that a guideline routes on top of the check's
# own rule. NBC-9.1 lists "account numbers, names or addresses that differ between statements for the same account"
# as a document integrity indicator: Fraud Operations, blocking (R-NBC-13). A name that differs between two bank
# statements is therefore BOTH an identity verification failure (R-NBC-09, the name_consistency rule above) and an
# integrity indicator. Harborline has no statements, so its table is empty.
NAMES_DIFFER_BETWEEN_STATEMENTS = "names_differ_between_statements"
NORTHWIND_DOCUMENT_INDICATORS: dict[str, DocumentCheckRule] = {
    NAMES_DIFFER_BETWEEN_STATEMENTS: DocumentCheckRule(
        section_ids=("NBC-9.1", "NBC-7.3"), trigger_id="R-NBC-13", refer_to=(FRAUD_OPERATIONS,), blocking=True
    ),
}
DOCUMENT_INDICATOR_RULES: dict[str, dict[str, DocumentCheckRule]] = {
    "credit": NORTHWIND_DOCUMENT_INDICATORS,
    "insurance": {},
    "startup_credit": NORTHWIND_DOCUMENT_INDICATORS,
}


class PolicyModel(BaseModel):
    """Base for policy models: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class Calculation(PolicyModel):
    """One computed number, with the inputs it came from. calc_id is citable, e.g. "CALC-dscr"."""

    calc_id: str = Field(pattern=r"^CALC-[a-z0-9_]+$")
    label: str
    value: float | None
    unit: CalcUnit
    inputs: dict[str, float | int | str | None] = Field(default_factory=dict)
    section_ids: list[str] = Field(default_factory=list)


class Finding(PolicyModel):
    """A rule that did not pass. detail states the numbers; evidence_ids and section_ids support them."""

    rule: str
    outcome: FindingOutcome
    trigger_ids: list[str] = Field(default_factory=list)
    section_ids: list[str]
    refer_to: list[str]
    blocking: bool = False
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class CheckPassed(PolicyModel):
    """A rule that passed, stated with its numbers and citations."""

    rule: str
    section_ids: list[str]
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class NotEvaluated(PolicyModel):
    """A rule the policy could not check, and why (missing data, or not implemented before Phase 2)."""

    rule: str
    section_ids: list[str]
    reason: str


class ScreeningOutcome(PolicyModel):
    """What sanctions screening found for one party (from a screen_entity tool result)."""

    party_id: str
    status: Literal["clear", "potential_match", "not_completed"]
    result_id: str | None = None
    top_score: float | None = None
    candidate_count: int = 0


class RiskSignal(PolicyModel):
    """The risk model's output for this application (from a score_risk tool result)."""

    result_id: str
    product: str
    prediction: float
    risk_band: str
    model_version: str
    unknown_features: list[str] = Field(default_factory=list)


class InjectionFlag(PolicyModel):
    """Instruction-like text found in one free-text field."""

    field: str
    patterns: list[str]


class DocumentCheckFlag(PolicyModel):
    """The outcome of one Phase 2 document check (docintel/checks.py) that the policy should act on.

    status "confirmed": the problem stands (a failing check, or a resolution that confirmed it) and raises the
    finding in DOCUMENT_CHECK_RULES; "resolved": it was cleared (recorded as a passed check, no finding).
    detail and evidence_ids are copied into the finding. detail must be built from structured values only (counts,
    document ids, contradiction ids), never from document text: finding details reach the memo prompt.
    """

    check_id: str
    status: Literal["confirmed", "resolved"]
    detail: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    # Named indicators of this check that a guideline routes separately (DOCUMENT_INDICATOR_RULES), for example
    # NAMES_DIFFER_BETWEEN_STATEMENTS. Empty by default.
    indicators: list[str] = Field(default_factory=list)


class PolicyInputs(PolicyModel):
    """Everything the policy uses besides the application itself.

    document_checks (Phase 4) defaults to empty; with no flags the policy output is byte-identical to the output
    before the field existed (tests/evidence/test_policy_integration.py checks this on every committed application).
    """

    screening: list[ScreeningOutcome] = Field(default_factory=list)
    risk: RiskSignal | None = None
    risk_unavailable_reason: str | None = None
    injection_flags: list[InjectionFlag] = Field(default_factory=list)
    document_checks: list[DocumentCheckFlag] = Field(default_factory=list)


class PolicyResult(PolicyModel):
    """The reference policy's answer for one application."""

    application_id: str
    line: Literal["credit", "insurance", "startup_credit"]
    policy_version: str = POLICY_VERSION
    recommendation: Recommendation
    findings: list[Finding]
    checks_passed: list[CheckPassed]
    calculations: list[Calculation]
    not_evaluated: list[NotEvaluated]
    referral_targets: list[str]
    blocking_open: bool
    required_reviewer_role: ReviewerRole
    review_queue: str

    def rule_codes(self) -> list[str]:
        """Sorted unique rule codes of the findings (what the evaluation compares with ground truth)."""
        return sorted({finding.rule for finding in self.findings})


class RuleContext:
    """Collects findings, passed checks, calculations and not-evaluated rules while a line's rules run."""

    def __init__(self) -> None:
        """Start empty."""
        self.findings: list[Finding] = []
        self.checks: list[CheckPassed] = []
        self.calculations: dict[str, Calculation] = {}
        self.not_evaluated: list[NotEvaluated] = []

    def calc(
        self,
        name: str,
        label: str,
        value: float | None,
        unit: CalcUnit,
        section_ids: list[str],
        **inputs: float | int | str | None,
    ) -> str:
        """Record a calculation and return its citable id ("CALC-<name>")."""
        calc_id = f"CALC-{name}"
        self.calculations[calc_id] = Calculation(
            calc_id=calc_id, label=label, value=value, unit=unit, inputs=inputs, section_ids=section_ids
        )
        return calc_id

    def find(
        self,
        rule: str,
        outcome: FindingOutcome,
        section_ids: list[str],
        refer_to: list[str],
        detail: str,
        evidence_ids: list[str],
        trigger_ids: list[str] | None = None,
        blocking: bool = False,
    ) -> None:
        """Record a rule that did not pass."""
        self.findings.append(
            Finding(
                rule=rule,
                outcome=outcome,
                trigger_ids=trigger_ids or [],
                section_ids=section_ids,
                refer_to=refer_to,
                blocking=blocking,
                detail=detail,
                evidence_ids=evidence_ids,
            )
        )

    def passed(self, rule: str, section_ids: list[str], detail: str, evidence_ids: list[str]) -> None:
        """Record a rule that passed."""
        self.checks.append(CheckPassed(rule=rule, section_ids=section_ids, detail=detail, evidence_ids=evidence_ids))

    def skip(self, rule: str, section_ids: list[str], reason: str) -> None:
        """Record a rule that could not be evaluated."""
        self.not_evaluated.append(NotEvaluated(rule=rule, section_ids=section_ids, reason=reason))


# --------------------------------------------------------------------------------------------------------------
# Number formatting used in finding details (the claim checker parses these back).
# --------------------------------------------------------------------------------------------------------------


def money(value: float) -> str:
    """$1,234,567.89"""
    return f"${value:,.2f}"


def ratio(value: float) -> str:
    """1.18"""
    return f"{value:.2f}"


def pct(value: float) -> str:
    """12.5%"""
    return f"{value:.1f}%"


# --------------------------------------------------------------------------------------------------------------
# Rules shared by both lines
# --------------------------------------------------------------------------------------------------------------


def check_sanctions(application: AnyApplication, inputs: PolicyInputs, context: RuleContext) -> None:
    """NBC-6.3 / HM-8.2: every party must be screened; any potential match is a blocking Compliance referral."""
    line = application.line
    sections = SANCTIONS_SECTIONS[line]
    by_party = {outcome.party_id: outcome for outcome in inputs.screening}
    for party in screened_parties(application):
        outcome = by_party.get(party.party_id)
        if outcome is None or outcome.status == "not_completed":
            evidence = [outcome.result_id] if outcome is not None and outcome.result_id else []
            context.find(
                "screening_not_completed",
                "refer",
                [sections[0]],
                [COMPLIANCE],
                f"Sanctions screening of party {party.party_id} was not completed, so the file cannot proceed.",
                evidence,
                blocking=True,
            )
        elif outcome.status == "potential_match":
            score_text = f" (top score {outcome.top_score:.4f})" if outcome.top_score is not None else ""
            context.find(
                "sanctions_potential_match",
                "refer",
                sections,
                [COMPLIANCE],
                f"Sanctions screening of party {party.party_id} returned a potential match{score_text}; only "
                "Compliance can disposition it.",
                [outcome.result_id] if outcome.result_id else [],
                trigger_ids=[SANCTIONS_TRIGGER[line]],
                blocking=True,
            )
    screened_ok = [outcome for outcome in inputs.screening if outcome.status == "clear"]
    if screened_ok and len(screened_ok) == len(screened_parties(application)):
        context.passed(
            "sanctions_screening_clear",
            [sections[0]],
            f"Every party ({', '.join(outcome.party_id for outcome in screened_ok)}) was screened and none returned "
            "a potential match.",
            [outcome.result_id for outcome in screened_ok if outcome.result_id],
        )


def check_injection(application: AnyApplication, inputs: PolicyInputs, context: RuleContext) -> None:
    """NBC-9.2 / HM-11.2: instruction-like text in a document is an integrity indicator and a blocking referral.

    It changes nothing else: no calculation, screening step or other rule reads the flagged text.
    """
    line = application.line
    if not inputs.injection_flags:
        context.passed(
            "no_embedded_instructions",
            [INJECTION_SECTIONS[line][0]],
            "No instruction-like text was found in the free-text fields.",
            [INJECTION_EVIDENCE_ID],
        )
        return
    fields = sorted({flag.field for flag in inputs.injection_flags})
    patterns = sorted({pattern for flag in inputs.injection_flags for pattern in flag.patterns})
    context.find(
        "embedded_instruction",
        "refer",
        INJECTION_SECTIONS[line],
        [INJECTION_REFER_TO[line]],
        f"Instruction-like text was found in {', '.join(fields)} (patterns: {', '.join(patterns)}). It was not "
        "followed and is treated as a document integrity indicator.",
        [INJECTION_EVIDENCE_ID],
        trigger_ids=[INJECTION_TRIGGER[line]],
        blocking=True,
    )


def merged_document_checks(flags: list[DocumentCheckFlag]) -> dict[str, DocumentCheckFlag]:
    """One flag per check id, in first-seen order: confirmed wins over resolved; details and evidence are joined."""
    merged: dict[str, DocumentCheckFlag] = {}
    for flag in flags:
        current = merged.get(flag.check_id)
        if current is None:
            merged[flag.check_id] = flag
            continue
        status = "confirmed" if "confirmed" in (current.status, flag.status) else "resolved"
        details = [text for text in (current.detail, flag.detail) if text]
        evidence = list(current.evidence_ids)
        evidence.extend(item for item in flag.evidence_ids if item not in evidence)
        indicators = list(current.indicators)
        indicators.extend(item for item in flag.indicators if item not in indicators)
        merged[flag.check_id] = DocumentCheckFlag(
            check_id=flag.check_id,
            status=status,
            detail=" ".join(details),
            evidence_ids=evidence,
            indicators=indicators,
        )
    return merged


def check_document_findings(application: AnyApplication, inputs: PolicyInputs, context: RuleContext) -> None:
    """Phase 2 document checks as policy rules (see DOCUMENT_CHECK_RULES for the sections and referrals).

    A confirmed flag raises the finding "document_<check_id>", plus "document_<check_id>_<indicator>" for every
    indicator it carries that DOCUMENT_INDICATOR_RULES lists; a resolved flag is recorded as a passed check with
    the same rule name. Flags for check ids the table does not list are ignored on purpose (the policy evaluates
    those problems from the application). With no flags this adds nothing at all.
    """
    rules = DOCUMENT_CHECK_RULES[application.line]
    indicator_rules = DOCUMENT_INDICATOR_RULES[application.line]
    for check_id, flag in merged_document_checks(inputs.document_checks).items():
        rule = rules.get(check_id)
        if rule is None:
            continue
        name = f"{DOCUMENT_RULE_PREFIX}{check_id}"
        suffix = f": {flag.detail}" if flag.detail else "."
        if flag.status == "confirmed":
            context.find(
                name,
                "refer",
                list(rule.section_ids),
                list(rule.refer_to),
                f"Document check {check_id} is confirmed{suffix}",
                list(flag.evidence_ids),
                trigger_ids=[rule.trigger_id],
                blocking=rule.blocking,
            )
            for indicator in flag.indicators:
                extra = indicator_rules.get(indicator)
                if extra is None:
                    continue
                context.find(
                    f"{name}_{indicator}",
                    "refer",
                    list(extra.section_ids),
                    list(extra.refer_to),
                    f"Document check {check_id} shows {indicator.replace('_', ' ')}{suffix}",
                    list(flag.evidence_ids),
                    trigger_ids=[extra.trigger_id],
                    blocking=extra.blocking,
                )
        else:
            context.passed(
                name,
                [rule.section_ids[0]],
                f"Document check {check_id} was resolved{suffix}",
                list(flag.evidence_ids),
            )


def check_credit_risk_model(inputs: PolicyInputs, context: RuleContext) -> None:
    """NBC-8.1: a PD above 12.0% (band E) is a referral (R-NBC-12). Missing PD is reported, never filled in."""
    if inputs.risk is None:
        reason = inputs.risk_unavailable_reason or "the risk score tool returned no result"
        context.skip("risk_model_pd", ["NBC-8.1"], f"Model PD not available: {reason}.")
        return
    risk = inputs.risk
    pd_pct = round(risk.prediction * 100.0, 1)
    calc_id = context.calc(
        "model_pd_pct",
        "Model probability of default (percent)",
        pd_pct,
        "pct",
        ["NBC-8.1"],
        prediction=risk.prediction,
        model_version=risk.model_version,
    )
    evidence = [risk.result_id, calc_id]
    incomplete = ""
    if risk.unknown_features:
        incomplete = f" The PD is based on incomplete data (unknown: {', '.join(risk.unknown_features)})."
    if risk.prediction > PD_REFERRAL_THRESHOLD:
        context.find(
            "model_pd_above_threshold",
            "refer",
            ["NBC-8.1", "NBC-7.3"],
            [SENIOR_UNDERWRITER],
            f"Model PD {pct(pd_pct)} is above 12.0% (risk band E).{incomplete}",
            evidence,
            trigger_ids=["R-NBC-12"],
        )
    else:
        context.passed("model_pd", ["NBC-8.1"], f"Model PD {pct(pd_pct)} is not above 12.0%.{incomplete}", evidence)


# --------------------------------------------------------------------------------------------------------------
# Putting the result together
# --------------------------------------------------------------------------------------------------------------


def decide_recommendation(findings: list[Finding]) -> Recommendation:
    """Apply the precedence in the module docstring."""
    if any(finding.blocking for finding in findings):
        return "refer"
    outcomes = {finding.outcome for finding in findings}
    if "decline_for_review" in outcomes:
        return "decline_for_review"
    if "request_more_information" in outcomes:
        return "request_more_information"
    if "refer" in outcomes:
        return "refer"
    return "approve"


def referral_targets(findings: list[Finding]) -> list[str]:
    """Every function named by a finding, most urgent first."""
    named = {target for finding in findings for target in finding.refer_to}
    return [target for target in REFERRAL_PRIORITY if target in named]


def review_queue_for(recommendation: str, targets: list[str], line: str = "credit") -> str:
    """The first queue the file goes to: the most urgent referral target, else the underwriter.

    A startup file with no referral target goes to the Senior Underwriter queue (NSP-9.1).
    """
    if targets:
        return targets[0]
    if line in SENIOR_REVIEW_LINES:
        return SENIOR_UNDERWRITER
    return UNDERWRITER if recommendation in ("approve", "request_more_information") else SENIOR_UNDERWRITER


def required_role_for(recommendation: str, line: str = "credit") -> ReviewerRole:
    """Refer and decline-for-review files need a senior underwriter at the checkpoint (NBC-7.2, HM-9.2).

    Every startup file needs one, whatever the recommendation (NSP-9.1).
    """
    if recommendation in ("refer", "decline_for_review") or line in SENIOR_REVIEW_LINES:
        return "senior_underwriter"
    return "underwriter"


def evaluate_policy(application: AnyApplication, inputs: PolicyInputs) -> PolicyResult:
    """Run every rule for the application's line and return the reference recommendation with its reasons."""
    from uwcopilot.agent.policy_credit import evaluate_credit_rules  # local imports avoid a cycle
    from uwcopilot.agent.policy_insurance import evaluate_insurance_rules
    from uwcopilot.agent.policy_startup import STARTUP_POLICY_VERSION, evaluate_startup_rules

    context = RuleContext()
    if isinstance(application, CreditApplication):
        evaluate_credit_rules(application, inputs, context)
        check_credit_risk_model(inputs, context)
    elif isinstance(application, StartupCreditApplication):
        evaluate_startup_rules(application, inputs, context)
    else:
        evaluate_insurance_rules(application, inputs, context)
    check_document_findings(application, inputs, context)
    check_sanctions(application, inputs, context)
    check_injection(application, inputs, context)

    recommendation = decide_recommendation(context.findings)
    targets = referral_targets(context.findings)
    line = application.line
    return PolicyResult(
        application_id=application.application_id,
        line=line,
        policy_version=STARTUP_POLICY_VERSION if line == "startup_credit" else POLICY_VERSION,
        recommendation=recommendation,
        findings=context.findings,
        checks_passed=context.checks,
        calculations=list(context.calculations.values()),
        not_evaluated=context.not_evaluated,
        referral_targets=targets,
        blocking_open=any(finding.blocking for finding in context.findings),
        required_reviewer_role=required_role_for(recommendation, line),
        review_queue=review_queue_for(recommendation, targets, line),
    )


def blocking_functions_in(targets: list[str]) -> list[str]:
    """The referral targets whose referral blocks approval."""
    return [target for target in targets if target in BLOCKING_FUNCTIONS]
