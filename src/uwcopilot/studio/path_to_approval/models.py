"""Inputs and outputs of the path-to-approval search (Studio S5), as frozen pydantic models.

Inputs:
- PlanCase: one founder plan the search starts from: business type, metro, the chosen site's demand range, the
  S3 Plan and Loan, and the founder's FACTS (credit score, experience, other debt, verified figures). The search may
  change only plan choices (levers); it never changes a fact (guardrails.py).
- Candidate: another site the founder listed in the same metro (the "site" lever).
- Target: the three simulated targets (targets.py builds the default with its sources).
- Change: one lever set to one value; a change-set is a list of them.

Outputs:
- Outcome: the three probabilities for one plan (simulated, based on stated assumptions) and the lender's
  base-case ratios, plus which targets are missed and by how much.
- AppliedChange / ChangeSet: what changed, by how much, how hard it is (effort), what would back each changed
  figure for a lender (evidence_needed), the program rules the changed plan still fails, the before/after outcomes
  and a plain-language explanation.
- PlanAmounts / ProgramChecks: the plan's money before and after, with the startup program's equity and product-limit
  checks (program.py).
- PathResult: the status ("already_meets_target", "paths_found" or "no_path_within_bounds"), the change-sets, the
  closest result when nothing reaches the target, every lever considered or not available, and search statistics.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.feasibility.inputs import Loan, Plan, SiteDemand

PATH_ENGINE_VERSION = "studio-path-to-approval-1.2"
FactStatus = Literal["USER_STATED", "VERIFIED"]
# The plan figures a founder can mark as observed or verified (backed by a document). The search never edits them.
VERIFIABLE_FIELDS: tuple[str, ...] = (
    "plan.owner_equity",
    "plan.capex",
    "plan.price",
    "plan.monthly_rent",
    "plan.days_open_per_week",
    "plan.paid_hours_per_month",
    "loan.amount",
    "loan.term_months",
    "site",
)
Status = Literal["already_meets_target", "paths_found", "no_path_within_bounds"]
Mode = Literal["full", "quick"]


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class OtherDebt(Frozen):
    """A loan or seller note other than the requested loan (NSP-1.2 "other debt"). A FACT: never hidden or changed.

    Its proceeds fund the project (they count in the cash at opening) and its monthly payment counts in debt
    service, in monthly net income and in cash for every simulated month (a constant payment, a design choice).
    """

    name: str = Field(min_length=1)
    amount: float = Field(ge=0, description="Proceeds that fund the project, USD")
    monthly_payment: float = Field(gt=0, description="Payment every month, USD")
    status: FactStatus = "USER_STATED"


class FounderFacts(Frozen):
    """Facts about the owners and the plan that the search may never change (Studio S5 guardrails).

    credit_score and experience_months are not used by the simulator; they are carried so that every change-set can
    be checked to leave them untouched, and so the package (S6) reads them from one place. verified_fields lists
    plan figures backed by a document or by observed data (VERIFIABLE_FIELDS); a lever that would edit one of them is
    not available.
    """

    credit_score: int | None = Field(default=None, ge=300, le=850)
    experience_months: int | None = Field(default=None, ge=0)
    other_debt: list[OtherDebt] = Field(default_factory=list)
    verified_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_fields(self) -> "FounderFacts":
        """Every verified field is one the plan has."""
        unknown = sorted(set(self.verified_fields) - set(VERIFIABLE_FIELDS))
        if unknown:
            raise ValueError(f"unknown verified field(s) {unknown}; known: {list(VERIFIABLE_FIELDS)}")
        return self

    @property
    def other_debt_amount(self) -> float:
        """Total proceeds of the other debt."""
        return float(sum(item.amount for item in self.other_debt))

    @property
    def other_debt_monthly_payment(self) -> float:
        """Total monthly payment of the other debt."""
        return float(sum(item.monthly_payment for item in self.other_debt))


class PlanCase(Frozen):
    """One founder plan: what the simulator needs (S3) plus the facts the search must leave alone."""

    profile_id: str = Field(min_length=1)
    metro_id: str = Field(min_length=1)
    site_label: str = Field(default="current site", min_length=1)
    site: SiteDemand
    plan: Plan
    loan: Loan
    facts: FounderFacts = Field(default_factory=FounderFacts)


class Candidate(Frozen):
    """Another candidate site in the same metro, with its own demand estimate (never edited by the search).

    monthly_rent is a rent quote for this site (USER_STATED). Without one, a switch to this site drops the current
    site's quote and the metro's rent range (ASSUMPTION) is used, because a quote belongs to its own site.
    """

    label: str = Field(min_length=1)
    metro_id: str = Field(min_length=1)
    site: SiteDemand
    monthly_rent: float | None = Field(default=None, ge=0)


class Target(Frozen):
    """The simulated targets a plan should reach (all editable). targets.default_target() fills the sources."""

    dscr_min: float = Field(gt=0, description="Year-2 DSCR counted as covered (NSP-4.1 / NBC-4.2 default 1.25)")
    p_dscr_min: float = Field(ge=0, le=1, description="Least share of paths with year-2 DSCR >= dscr_min")
    stress_revenue_share: float = Field(ge=0, lt=1, description="Revenue cut of the lender stress (NSP-4.2)")
    stressed_dscr_min: float = Field(gt=0, description="Least stressed year-2 DSCR (NSP-4.2 default 1.00)")
    p_stressed_min: float = Field(ge=0, le=1, description="Least share of paths passing the stress")
    p_cash_negative_max: float = Field(ge=0, le=1, description="Largest share of paths with cash < 0")
    cash_months: int = Field(default=24, ge=1, le=36, description="The cash check covers months 1..cash_months")
    year: Literal[2] = Field(default=2, description="Year 1 includes the opening ramp-up (NSP-1.2)")
    sources: dict[str, str] = Field(default_factory=dict, description="{target field: where its default comes from}")


class Change(Frozen):
    """One lever set to one value (dollars, price, days, months, or a candidate site's label)."""

    lever: str = Field(min_length=1)
    value: float | str


class Outcome(Frozen):
    """The three simulated probabilities of one plan, against a Target."""

    label: Literal["simulated, based on stated assumptions"] = SIMULATION_LABEL
    n_paths: int
    seed: int
    p_dscr_at_least_target_year2: float = Field(ge=0, le=1)
    p_stressed_dscr_at_least_min_year2: float = Field(ge=0, le=1)
    p_cash_negative_within_months: float = Field(ge=0, le=1)
    projected_dscr_base_case: float | None = Field(
        description="NSP-4.1 the lender's way: year-2 P50 NOI / projected annual debt service (incl. other debt)"
    )
    stressed_dscr_base_case: float | None = Field(
        description="NSP-4.2 revenue stress on the year-2 P50, always with the program's own 20% (not an edited target)"
    )
    annual_debt_service: float = Field(description="Year-2 scheduled debt service: the loan's 12 payments + other debt")
    meets_target: bool
    targets_missed: list[str]
    shortfall: float = Field(ge=0, description="Sum over the three targets of how far the probability misses")


class AppliedChange(Frozen):
    """One lever's change inside a change-set.

    to_value is the lever's amount in the changed plan. For a dollar lever it includes the moves of the other dollar
    levers of the same change-set (more owner equity also lowers the loan), and own_change is the lever's own move.
    """

    lever: str
    label: str
    unit: str
    from_value: float | str
    to_value: float | str
    own_change: float | None = Field(
        default=None, description="Dollar levers only: this lever's own move, USD (to_value - from_value when alone)"
    )
    effort: float = Field(ge=0, description="Effort of this lever's own move")
    fields_changed: list[str]
    evidence_needed: str | None = Field(
        default=None,
        description=(
            "What would back the changed figure for a lender (a quote, bank statements, a new loan request). Until "
            "it exists, the new figure is a plan change suggested by a simulation, not a quote and not verified."
        ),
    )


class ProgramChecks(Frozen):
    """Plain rules of the SYNTHETIC startup program that a change-set may not newly break (program.py)."""

    equity_injection_pct: float = Field(description="Owner cash equity / total project cost, percent (NSP-3.1)")
    equity_minimum_pct: float
    meets_equity_minimum: bool
    loan_within_limits: bool = Field(description="NSP-8.1 startup term loan amount limits")
    term_within_limits: bool = Field(description="NSP-8.1 startup term loan term range")


class PlanAmounts(Frozen):
    """The plan's money and terms (sources = uses: equity + loan + other debt = build-out + cash reserve)."""

    owner_equity: float
    loan_amount: float
    other_debt_amount: float
    build_out_cost: float
    cash_reserve_at_opening: float
    loan_term_months: int
    price: float | None
    days_open_per_week: float | None
    site_label: str
    checks: ProgramChecks


class ChangeSet(Frozen):
    """A set of lever changes with its before/after outcomes and a plain-language explanation."""

    rank: int = Field(ge=0, description="1, 2, 3 for the returned change-sets; 0 for the closest result")
    changes: list[AppliedChange]
    n_levers: int = Field(ge=1)
    effort: float = Field(ge=0)
    before: Outcome
    after: Outcome
    plan_before: PlanAmounts
    plan_after: PlanAmounts
    applied_plan: PlanCase = Field(description="The whole plan with the change-set applied ('apply this change')")
    meets_target: bool
    program_rules_not_met: list[str] = Field(
        default_factory=list,
        description="Rules of the SYNTHETIC startup program the changed plan still fails (program.py), in words",
    )
    evidence_needed: list[str] = Field(
        default_factory=list, description="AppliedChange.evidence_needed of every change, in order"
    )
    explanation: str
    unchanged: str = Field(description="What the change-set leaves alone (facts, assumptions, verified figures)")


class LeverSummary(Frozen):
    """A lever the search moved: its current value, bounds and the grid it tried."""

    name: str
    label: str
    unit: str
    current: float | str
    low: float | None
    high: float | None
    grid_size: int
    effort_weight: float
    note: str


class SearchSettings(Frozen):
    """How hard the search looks. full = 1,000 paths (the S3 default), 8 grid values per side; quick = the same
    paths with 4 grid values per side and a smaller budget of evaluated variants (search.QUICK_SETTINGS)."""

    mode: Mode = "full"
    n_paths: int = Field(default=1000, ge=50)
    confirm_paths: int | None = Field(
        default=None, description="Re-check what the search returns with this many paths (None = no re-check)"
    )
    months: int = Field(default=36, ge=24)
    seed: int = 42
    steps_per_side: int = Field(default=8, ge=1, le=50)
    max_results: int = Field(default=3, ge=1, le=10)
    search_pairs: bool = True
    max_evaluations: int = Field(
        default=2000,
        ge=1,
        description=(
            "Budget: the most plan variants one search evaluates, counting the plan itself and every variant whether "
            "it is simulated or read from the cache (so the answer never depends on what the cache holds)"
        ),
    )


class SearchStats(Frozen):
    """What the search did and how long it took (wall time on the machine that ran it)."""

    evaluations: int = Field(description="Plans simulated (cache misses)")
    cache_hits: int
    variants_evaluated: int = Field(
        default=0, description="Plan variants the search evaluated, simulated or read from the cache (the budget)"
    )
    singles_tried: int
    pairs_screened: int
    pairs_searched: int
    blocked_by_program: int = Field(description="Change-sets refused because they would break a program check")
    budget_exhausted: bool
    search_complete: bool
    runtime_ms: float


class PathResult(Frozen):
    """The answer to "what is the smallest change to my plan that reaches the targets?"."""

    label: Literal["simulated, based on stated assumptions"] = SIMULATION_LABEL
    disclaimer: str = DISCLAIMER
    engine_version: str = PATH_ENGINE_VERSION
    simulator_version: str
    profile_id: str
    metro_id: str
    metro_mode: str
    data_label: str
    status: Status
    message: str
    target: Target
    before: Outcome
    plan_before: PlanAmounts
    change_sets: list[ChangeSet]
    closest: ChangeSet | None = Field(description="The best result found when no change-set reaches the target")
    levers_considered: list[LeverSummary]
    levers_not_available: dict[str, str] = Field(description="{lever: why the search did not move it}")
    guardrails: list[str]
    settings: SearchSettings
    search: SearchStats
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """A small dict for logs and the results script: status, levers per change-set and the runtime."""
        return {
            "status": self.status,
            "n_change_sets": len(self.change_sets),
            "levers": [[change.lever for change in item.changes] for item in self.change_sets],
            "runtime_ms": self.search.runtime_ms,
            "evaluations": self.search.evaluations,
        }
