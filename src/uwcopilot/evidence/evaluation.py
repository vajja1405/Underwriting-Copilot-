"""Claim-status evaluation of the evidence ledger against the SYNTHETIC ground truth (Phase 4 F).

The expected status of every ledger claim is defined HERE, from the truth's injected problems and its list of
missing required documents, before the engine is run on the test packets. The table was written from the problem
definitions (docintel/truth.py) and the ledger's claim id scheme (evidence/ledger.py); it is not computed from the
engine's output. It was checked and finalized on the dev split; the test split is evaluated once with it
(EXPECTED_TABLE_VERSION and expected_table_sha256() are written into the results so a later change is visible).

LABEL (CLAIM_STATUS_LABEL): this is agreement with the documented status rules, written by the same author as the
ledger. The problem -> claim id table restates the ledger's contradiction -> claim table, so agreement measures
whether problems are detected and propagated to the right claims, not an independent taxonomy, and a status rule
that is wrong in BOTH tables is scored as correct.

Version history: v1 (the first test run) expected a calculation over a value no document provided (the debt
schedule's payments and balance when the schedule is missing) to be VERIFIED, because it copied the ledger's rule;
a review found that the ledger then printed numbers computed on imputed $0 defaults as VERIFIED. v2 (this table)
adds DEPENDENCY-AWARE expectations, written from the guideline formulas: NBC-4.1 annual debt service uses the
scheduled payment, NBC-4.4 leverage the schedule's total balance, NBC-2.1 the principals' credit scores and
bankruptcy flags (credit report). The v2 table was checked on the dev split before the test split was re-run.

Expected status of a claim (expected_claims):
1. CONFLICTING for the claims of each injected contradiction problem (CONFLICT_CLAIMS_BY_PROBLEM): the document
   check's own claim "CHK-<check_id>" and the application facts the disagreement puts in doubt. A problem whose
   comparison needs a document the truth lists as missing (PROBLEM_NEEDS_DOCUMENTS) cannot be seen in the
   documents; its claims are not expected and the problem is counted as unobservable.
2. UNKNOWN for every checklist code in truth.missing_required_documents: "DOC-<code>" and "CHK-required_documents",
   plus the claims that cannot exist without that document (MISSING_DOCUMENT_CLAIMS; for a missing statement month
   the code is bank_statements_12_months, and NBC-3.2 then leaves verified revenue and everything computed from it
   unavailable). v2: also UNKNOWN (conditional) for the claims a planted problem leaves unverifiable
   (PROBLEM_UNKNOWN_CLAIMS): a loss run whose summary does not equal its claim lines (loss_run_total_mismatch) cannot
   show which side is right, so its policy years and every loss-run calculation are unverifiable (HM-3.3, HM-11.1).
3. UNKNOWN for a calculation the reference formulas cannot compute even on truth.application_as_documented
   (value None: for example a percentage of a zero denominator).
4. VERIFIED for every other claim (the spec: "everything else VERIFIED").
A claim named by several rules takes the first in the order UNKNOWN, CONFLICTING, VERIFIED (a value that is missing
cannot be shown to conflict).

"Required" claims must be in the ledger: one that is missing counts as predicted ABSENT. "Conditional" claims exist
only for some requests (a stress test applies above a loan amount; a loss ratio only for a quoted line), so they
are scored only when the ledger has them.

Excluded by design (counted, not scored): MODEL-* and FEATURE-* claims are UNKNOWN by the honesty rule (no Phase 1
model applies to these commercial packets), ASSUME-* claims are the clean screening assumption, and the location
fields no packet document prints (APP-locations.<i>.wind_tier and the like) are UNKNOWN for every insurance packet.
Their status is a property of the engine's or the generator's design, not of the packet.

The truth is read by attribute only (a docintel.truth.PacketTruth works); like docintel/evaluation.py, this module
does not import truth.py, so nothing at run time can reach the ground truth through it.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

EXPECTED_TABLE_VERSION = "evidence-claim-status-table-v2"
CLAIM_STATUS_LABEL = (
    "agreement with the documented status rules (same author): the expected table restates the ledger's own "
    "status rules, so a rule that is wrong in both is scored as correct"
)

VERIFIED = "VERIFIED"
CONFLICTING = "CONFLICTING"
UNKNOWN = "UNKNOWN"
ABSENT = "ABSENT"
EXPECTED_STATUSES: tuple[str, ...] = (VERIFIED, CONFLICTING, UNKNOWN)
PREDICTED_STATUSES: tuple[str, ...] = (
    VERIFIED,
    CONFLICTING,
    UNKNOWN,
    "MODEL_ESTIMATE",
    "USER_STATED",
    "ASSUMPTION",
    ABSENT,
)
# Precedence when several rules name one claim: the first status wins.
STATUS_PRECEDENCE: tuple[str, ...] = (UNKNOWN, CONFLICTING, VERIFIED)

BY_DESIGN_PREFIXES: dict[str, str] = {
    "MODEL-": "UNKNOWN by the honesty rule: no Phase 1 model is trained on these commercial packets",
    "FEATURE-": "model feature claims: only produced by a model estimator, none applies here",
    "ASSUME-": "ASSUMPTION by design: the clean screening assumption (KYC lists not used in this evaluation)",
}
# Claim-id patterns excluded by design, keyed by a readable name (counted under that name).
LOCATION_DEFAULTS_KEY = "APP-locations.<i>.<undocumented field>"
BY_DESIGN_PATTERNS: dict[str, tuple[str, str]] = {
    LOCATION_DEFAULTS_KEY: (
        r"^APP-locations\.\d+\.(wind_tier|flood_zone|wildfire_hazard|vacant_days|knob_and_tube_or_fuse_box|"
        r"systems_updated_year)$",
        "UNKNOWN by the packet design: no packet document prints these location fields (docintel "
        "to_application_insurance writes the agent model's defaults)",
    ),
}

# 1. Contradiction problems -> the claims a correct ledger shows as CONFLICTING (all required).
CONFLICT_CLAIMS_BY_PROBLEM: dict[str, tuple[str, ...]] = {
    "revenue_gap": ("CHK-revenue_vs_deposits", "APP-stated_annual_revenue"),
    "balance_discontinuity": ("CHK-balance_continuity",),
    "debt_payment_mismatch": (
        "CHK-debt_payment_consistency",
        "APP-debt.existing_monthly_payment_schedule",
        "APP-debt.existing_monthly_payment_observed",
    ),
    "name_mismatch": ("CHK-name_consistency",),
    "fein_mismatch": ("CHK-fein_consistency",),
    "owner_mismatch": ("CHK-owner_consistency",),
    "prompt_injection": ("CHK-embedded_instructions",),
    "loss_run_total_mismatch": ("CHK-loss_run_totals",),
    "paid_reserve_mismatch": ("CHK-loss_run_line_balance",),
    "tiv_mismatch": ("CHK-tiv_consistency", "APP-stated_tiv"),
    "claims_history_mismatch": (
        "CHK-claims_history_consistency",
        "APP-stated_claim_count",
        "APP-stated_total_incurred",
    ),
}
# A disagreement needs both sides in the file: the verified revenue needs twelve statement months (NBC-3.2), the
# scheduled debt payment needs the debt schedule.
PROBLEM_NEEDS_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "revenue_gap": ("bank_statements_12_months",),
    "debt_payment_mismatch": ("debt_schedule",),
}


@dataclass(frozen=True)
class MissingDocumentClaims:
    """The claims that are UNKNOWN because one checklist document is missing (besides DOC-<code>). patterns are
    regular expressions over claim ids built from application paths (one claim per owner); they are conditional."""

    required: tuple[str, ...] = ()
    conditional: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()


ALWAYS_FOR_MISSING_DOCUMENTS: tuple[str, ...] = ("CHK-required_documents",)
# 2. Missing checklist documents -> further UNKNOWN claims.
MISSING_DOCUMENT_CLAIMS: dict[str, MissingDocumentClaims] = {
    # NBC-3.2: verified revenue needs 12 consecutive months; NOI, DSCR, leverage and the stress tests use it.
    "bank_statements_12_months": MissingDocumentClaims(
        required=("CHK-statement_months_complete",),
        conditional=(
            "CALC-verified_annual_revenue",
            "CALC-revenue_gap_pct",
            "CALC-noi",
            "CALC-annual_debt_service",
            "CALC-dscr",
            "CALC-leverage",
            "CALC-stressed_dscr_revenue",
            "CALC-stressed_dscr_rate",
        ),
    ),
    # The scheduled payments and balances are printed only on the debt schedule. v2 (dependency-aware): NBC-4.1
    # annual debt service uses the scheduled payment (the higher of scheduled and observed), so it and every ratio
    # over it (DSCR, the stated-revenue DSCR, both stress DSCRs) cannot be verified; NBC-4.4 leverage divides the
    # schedule's total balance.
    "debt_schedule": MissingDocumentClaims(
        conditional=(
            "APP-debt.existing_monthly_payment_schedule",
            "APP-debt.existing_total_debt_balance",
            "APP-debt.refinanced_monthly_payment",
            "CALC-annual_debt_service",
            "CALC-dscr",
            "CALC-dscr_stated_revenue",
            "CALC-stressed_dscr_revenue",
            "CALC-stressed_dscr_rate",
            "CALC-leverage",
        ),
    ),
    # v2: NBC-2.1 reads each principal's credit score and bankruptcy flag from the credit report.
    "credit_report": MissingDocumentClaims(
        conditional=("CALC-min_principal_credit_score",),
        patterns=(r"^APP-owners\.\d+\.(credit_score|bankruptcy_within_7_years)$",),
    ),
    # HM-3.1 / HM-3.2: without every carrier's loss run the experience period is incomplete, so no loss ratio.
    "loss_runs": MissingDocumentClaims(
        required=("CHK-loss_run_coverage",),
        conditional=(
            "CALC-loss_ratio_5yr_property",
            "CALC-loss_ratio_5yr_general_liability",
            "CALC-max_annual_loss_ratio_property",
            "CALC-max_annual_loss_ratio_general_liability",
            "CALC-claim_frequency",
        ),
    ),
}
# v2: planted problems that leave claims unverifiable (UNKNOWN, conditional), with claim-id patterns.
LOSS_RUN_CALCS: tuple[str, ...] = (
    "CALC-loss_ratio_5yr_property",
    "CALC-loss_ratio_5yr_general_liability",
    "CALC-max_annual_loss_ratio_property",
    "CALC-max_annual_loss_ratio_general_liability",
    "CALC-claim_frequency",
    "CALC-incurred_difference_pct",
    "CALC-unbalanced_claim_lines",
)
PROBLEM_UNKNOWN_CLAIMS: dict[str, MissingDocumentClaims] = {
    "loss_run_total_mismatch": MissingDocumentClaims(
        conditional=LOSS_RUN_CALCS,
        patterns=(r"^APP-loss_runs\.years\.", r"^APP-loss_runs\.current_term_claims$"),
    ),
}
NOT_COMPUTABLE_SOURCE = "not computable on the truth's as-documented application"
STATEMENTS_CODE = "bank_statements_12_months"
LOSS_RUNS_CODE = "loss_runs"


@dataclass(frozen=True)
class ExpectedClaim:
    """The expected status of one claim, whether the ledger must have it, and the truth facts behind it."""

    claim_id: str
    status: str
    required: bool
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ClaimScore:
    """One scored claim: expected vs predicted status."""

    claim_id: str
    expected: str
    predicted: str
    required: bool
    sources: tuple[str, ...] = ()

    @property
    def correct(self) -> bool:
        """True when the ledger shows the expected status."""
        return self.expected == self.predicted


def expected_table() -> dict[str, Any]:
    """The whole expected-status table as plain JSON (written into the results next to its hash)."""
    return {
        "version": EXPECTED_TABLE_VERSION,
        "label": CLAIM_STATUS_LABEL,
        "conflict_claims_by_problem": {key: list(value) for key, value in CONFLICT_CLAIMS_BY_PROBLEM.items()},
        "problem_needs_documents": {key: list(value) for key, value in PROBLEM_NEEDS_DOCUMENTS.items()},
        "always_for_missing_documents": list(ALWAYS_FOR_MISSING_DOCUMENTS),
        "missing_document_claims": {
            code: {
                "required": list(claims.required),
                "conditional": list(claims.conditional),
                "patterns": list(claims.patterns),
            }
            for code, claims in MISSING_DOCUMENT_CLAIMS.items()
        },
        "problem_unknown_claims": {
            kind: {"conditional": list(claims.conditional), "patterns": list(claims.patterns)}
            for kind, claims in PROBLEM_UNKNOWN_CLAIMS.items()
        },
        "not_computable_rule": NOT_COMPUTABLE_SOURCE + " -> UNKNOWN (conditional)",
        "default": VERIFIED,
        "precedence": list(STATUS_PRECEDENCE),
        "excluded_by_design": {
            **BY_DESIGN_PREFIXES,
            **{key: f"{pattern}: {reason}" for key, (pattern, reason) in BY_DESIGN_PATTERNS.items()},
        },
    }


def expected_table_sha256() -> str:
    """SHA-256 of the canonical JSON of expected_table()."""
    text = json.dumps(expected_table(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def by_design_key(claim_id: str) -> str | None:
    """The by-design exclusion a claim falls under (a prefix or a BY_DESIGN_PATTERNS name), or None when scored."""
    for prefix in BY_DESIGN_PREFIXES:
        if claim_id.startswith(prefix):
            return prefix
    for key, (pattern, _) in BY_DESIGN_PATTERNS.items():
        if re.search(pattern, claim_id):
            return key
    return None


def by_design_reason(claim_id: str) -> str | None:
    """Why a claim is excluded from scoring, or None when it is scored."""
    key = by_design_key(claim_id)
    if key is None:
        return None
    if key in BY_DESIGN_PREFIXES:
        return BY_DESIGN_PREFIXES[key]
    return BY_DESIGN_PATTERNS[key][1]


def expected_patterns(truth: Any) -> list[tuple[str, str]]:
    """(claim-id pattern, source) of the conditional UNKNOWN expectations of the truth's missing documents and of
    its planted problems (PROBLEM_UNKNOWN_CLAIMS)."""
    found = []
    for code in truth.missing_required_documents:
        for pattern in MISSING_DOCUMENT_CLAIMS.get(code, MissingDocumentClaims()).patterns:
            found.append((pattern, f"missing document {code}"))
    for problem in truth.problems:
        for pattern in PROBLEM_UNKNOWN_CLAIMS.get(problem.problem_type, MissingDocumentClaims()).patterns:
            found.append((pattern, f"{problem.problem_id} {problem.problem_type}"))
    return found


def pattern_expectation(claim_id: str, patterns: list[tuple[str, str]]) -> ExpectedClaim | None:
    """The conditional UNKNOWN expectation of a claim that matches a missing document's pattern (None otherwise)."""
    sources = tuple(source for pattern, source in patterns if re.search(pattern, claim_id))
    return ExpectedClaim(claim_id, UNKNOWN, False, sources) if sources else None


def merge_expectation(table: dict[str, ExpectedClaim], claim_id: str, status: str, required: bool, source: str) -> None:
    """Add one expectation; the higher-precedence status wins, a claim is required when any rule requires it."""
    current = table.get(claim_id)
    if current is None:
        table[claim_id] = ExpectedClaim(claim_id, status, required, (source,))
        return
    best = min(current.status, status, key=STATUS_PRECEDENCE.index)
    sources = current.sources if source in current.sources else (*current.sources, source)
    table[claim_id] = ExpectedClaim(claim_id, best, current.required or required, sources)


def observable(problem: Any, missing_documents: set[str]) -> bool:
    """False when the problem's comparison needs a document the truth lists as missing."""
    needed = PROBLEM_NEEDS_DOCUMENTS.get(problem.problem_type, ())
    return not any(code in missing_documents for code in needed)


def unobservable_problems(truth: Any) -> list[str]:
    """Problem ids whose disagreement cannot be seen because a document it needs is missing."""
    missing = set(truth.missing_required_documents)
    return [
        problem.problem_id
        for problem in truth.problems
        if problem.problem_type in CONFLICT_CLAIMS_BY_PROBLEM and not observable(problem, missing)
    ]


def expected_claims(truth: Any, reference_calculations: Iterable[Any] = ()) -> dict[str, ExpectedClaim]:
    """The claims the truth says are CONFLICTING or UNKNOWN (rules 1-3 of the module docstring).

    reference_calculations: agent.policy Calculation objects of the reference policy on
    truth.application_as_documented (calc_id and value are read). Every claim not in the result is expected VERIFIED.
    """
    table: dict[str, ExpectedClaim] = {}
    missing = set(truth.missing_required_documents)
    for problem in truth.problems:
        claims = CONFLICT_CLAIMS_BY_PROBLEM.get(problem.problem_type, ())
        if not claims or not observable(problem, missing):
            continue
        for claim_id in claims:
            merge_expectation(table, claim_id, CONFLICTING, True, f"{problem.problem_id} {problem.problem_type}")
    for code in truth.missing_required_documents:
        source = f"missing document {code}"
        merge_expectation(table, f"DOC-{code}", UNKNOWN, True, source)
        for claim_id in ALWAYS_FOR_MISSING_DOCUMENTS:
            merge_expectation(table, claim_id, UNKNOWN, True, source)
        claims = MISSING_DOCUMENT_CLAIMS.get(code, MissingDocumentClaims())
        for claim_id in claims.required:
            merge_expectation(table, claim_id, UNKNOWN, True, source)
        for claim_id in claims.conditional:
            merge_expectation(table, claim_id, UNKNOWN, False, source)
    for problem in truth.problems:
        for claim_id in PROBLEM_UNKNOWN_CLAIMS.get(problem.problem_type, MissingDocumentClaims()).conditional:
            merge_expectation(table, claim_id, UNKNOWN, False, f"{problem.problem_id} {problem.problem_type}")
    for calculation in reference_calculations:
        if calculation.value is None:
            merge_expectation(table, calculation.calc_id, UNKNOWN, False, NOT_COMPUTABLE_SOURCE)
    return table


def score_claims(
    predicted: dict[str, str], expected: dict[str, ExpectedClaim], patterns: list[tuple[str, str]] | None = None
) -> tuple[list[ClaimScore], dict[str, int]]:
    """Score a ledger (claim id -> status; {} when the case has no ledger) against the expected table and the
    conditional patterns (expected_patterns).

    Returns the scored claims (ledger claims except the by-design ones, plus required expected claims the ledger
    lacks, as ABSENT) and the number of excluded claims per by-design key.
    """
    scores: list[ClaimScore] = []
    excluded: Counter = Counter()
    for claim_id, status in predicted.items():
        key = by_design_key(claim_id)
        if key is not None:
            excluded[key] += 1
            continue
        entry = expected.get(claim_id) or pattern_expectation(claim_id, patterns or [])
        if entry is None:
            scores.append(ClaimScore(claim_id, VERIFIED, status, False))
        else:
            scores.append(ClaimScore(claim_id, entry.status, status, entry.required, entry.sources))
    for claim_id, entry in expected.items():
        if entry.required and claim_id not in predicted:
            scores.append(ClaimScore(claim_id, entry.status, ABSENT, True, entry.sources))
    return scores, dict(excluded)


def claim_counts(scores: list[ClaimScore]) -> Counter:
    """Counters for the summary: claims.total / .correct, per expected and predicted status, and the confusion."""
    counts: Counter = Counter()
    for score in scores:
        counts["claims.total"] += 1
        counts["claims.correct"] += int(score.correct)
        counts[f"claims.expected.{score.expected}.total"] += 1
        counts[f"claims.expected.{score.expected}.correct"] += int(score.correct)
        counts[f"claims.predicted.{score.predicted}.total"] += 1
        counts[f"claims.predicted.{score.predicted}.correct"] += int(score.correct)
        counts[f"claims.confusion.{score.expected}.{score.predicted}"] += 1
        if score.expected != VERIFIED:
            counts["claims.open_expected.total"] += 1
            counts["claims.open_expected.correct"] += int(score.correct)
    return counts


def problem_document_code(problem: Any) -> str | None:
    """The checklist code a missing_* problem removes (None when the truth does not say)."""
    if problem.resolving_request == "missing_statement_month":
        return STATEMENTS_CODE
    if problem.resolving_request == "missing_loss_run":
        return LOSS_RUNS_CODE
    return dict(problem.resolving_request_params).get("document")


def problem_claim_ids(problem: Any, missing: set[str]) -> tuple[str, ...] | None:
    """The required expected claims of one problem (None when the problem is unobservable)."""
    if problem.problem_type in CONFLICT_CLAIMS_BY_PROBLEM:
        return CONFLICT_CLAIMS_BY_PROBLEM[problem.problem_type] if observable(problem, missing) else None
    code = problem_document_code(problem)
    return (f"DOC-{code}",) if code in missing else ()


def problem_claim_counts(truth: Any, scores: list[ClaimScore]) -> Counter:
    """Per problem type: how many of its required expected claims the ledger shows with the expected status
    (a contradiction problem's CONFLICTING claims; a missing document's or page's DOC-<code> claim)."""
    by_id = {score.claim_id: score for score in scores}
    counts: Counter = Counter()
    missing = set(truth.missing_required_documents)
    for problem in truth.problems:
        kind = problem.problem_type
        claim_ids = problem_claim_ids(problem, missing)
        if claim_ids is None:
            counts[f"claims.by_problem.{kind}.unobservable"] += 1
            continue
        for claim_id in claim_ids:
            score = by_id.get(claim_id)
            counts[f"claims.by_problem.{kind}.total"] += 1
            counts[f"claims.by_problem.{kind}.correct"] += int(score is not None and score.correct)
    return counts


def mismatches(scores: list[ClaimScore]) -> list[dict[str, Any]]:
    """The wrongly scored claims as JSON rows (for the per-packet file)."""
    return [
        {
            "claim_id": score.claim_id,
            "expected": score.expected,
            "predicted": score.predicted,
            "required": score.required,
            "sources": list(score.sources),
        }
        for score in scores
        if not score.correct
    ]
