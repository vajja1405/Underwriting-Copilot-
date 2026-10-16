"""The internal model of one SYNTHETIC commercial insurance submission (Harborline Mutual, fictional).

An InsuranceCase holds the insured, its locations, the prior carriers and their loss runs (policy years, premiums,
claims), the application's stated values, the owners and the broker's cover email, plus the document-level
differences its injected problems create.

How every field of application_as_documented is derived (to_application(oracle) must give the same result):

- application_id "APP-I-<nnn>"; application_date, proposed_effective_date, coverage, gl_limit_per_occurrence and
  gl_limit_aggregate (None when the form says "Not requested"), stated_tiv, annual_revenue, annual_payroll,
  subcontracted_work_pct, has_subcontractor_coi_tracking, foreign_products, stated_claim_count,
  stated_total_incurred: the insurance application form. Codes are printed as words (render_helpers.label).
- applicant.legal_name, fein, years_in_business, class_code: the application form; prior_experience_years 0;
  fryer_suppression_inspected_within_6_months False.
- locations: one per property schedule row: location_id, construction_class, year_built, roof_year, stories,
  square_feet, sprinklered, protection_class, building_value, contents_value, business_income_value. The fields the
  schedule does not print keep their defaults (systems_updated_year None, knob_and_tube_or_fuse_box False,
  wind_tier 0, flood_zone "X", wildfire_hazard "low", vacant_days 0); the generator never sets them otherwise.
- loss_runs.carrier_produced: True when every loss run in the packet is carrier-produced (it always is here).
  loss_runs.valued_as_of: the EARLIEST "valued as of" date among the loss runs in the packet. loss_runs.years: one
  LossRunYear per premium-by-year row of the loss runs in the packet, sorted by policy year start, with the claim
  rows whose date of loss falls in [start, start + 1 year), ordered by (date of loss, claim number); a line the
  carrier did not write has premium None. loss_runs.current_term_claims: claim rows dated on or after the current
  term start (the proposed effective date minus one year), in the same order. uncovered_years 0, explained True.
  Claim paid / reserve / incurred are the printed values.
- owners: the application's owners table (O1, O2, ...); credit_score None.
- documents_provided: signed_application, prior_carrier_history and revenue_and_payroll <- the application form;
  property_schedule <- property schedule; loss_runs <- a loss run for EVERY carrier on the application's prior
  carriers table; beneficial_ownership_statement <- ownership statement; inspection_report <- an inspection report
  for every location whose schedule TIV is above $5,000,000.
- notes: the application's Remarks text. ground_truth: None.
"""

from dataclasses import dataclass, field
from datetime import date

from uwcopilot.docintel.synthetic.credit_case import InjectionPlan, Party


@dataclass
class LocationData:
    """One property schedule row (replacement-cost values)."""

    location_id: str
    address: str
    construction_class: str
    occupancy: str
    year_built: int
    roof_year: int
    stories: int
    square_feet: int
    sprinklered: bool
    protection_class: int
    building_value: float
    contents_value: float
    business_income_value: float

    @property
    def tiv(self) -> float:
        """Building + contents + business income (HM-1.2)."""
        return round(self.building_value + self.contents_value + self.business_income_value, 2)


@dataclass
class ClaimData:
    """One claim. paid / reserve / incurred are the true values; printed_* override what the loss run shows."""

    claim_number: str
    date_of_loss: date
    line: str
    paid: float
    reserve: float
    incurred: float
    printed_reserve: float | None = None
    printed_incurred: float | None = None
    hidden: bool = False

    @property
    def status(self) -> str:
        """Open while a reserve remains."""
        return "Open" if self.shown_reserve > 0 else "Closed"

    @property
    def shown_reserve(self) -> float:
        """The reserve as printed."""
        return self.reserve if self.printed_reserve is None else self.printed_reserve

    @property
    def shown_incurred(self) -> float:
        """Incurred as printed."""
        return self.incurred if self.printed_incurred is None else self.printed_incurred


@dataclass
class PolicyYear:
    """One policy year a carrier wrote: start date and earned premium per line (None when not written)."""

    start: date
    property_premium: float | None
    gl_premium: float | None


@dataclass
class CarrierRun:
    """One prior carrier: its policy years, claims and the loss run it produced.

    years are the experience-period years it wrote (with earned premium); current_term is True for the carrier of
    the current term. summary_* override the printed totals block (integrity problems).
    """

    carrier: str
    layout: int
    policy_number: str
    years: list[PolicyYear]
    current_term: bool
    current_premiums: tuple[float | None, float | None]
    claims: list[ClaimData]
    valued_as_of: date
    summary_extra_paid: float = 0.0
    summary_extra_incurred: float = 0.0

    def printed_claims(self) -> list[ClaimData]:
        """Claims printed on the loss run, by date of loss and claim number."""
        return sorted(
            (claim for claim in self.claims if not claim.hidden),
            key=lambda claim: (claim.date_of_loss, claim.claim_number),
        )

    def summary(self) -> tuple[int, float, float, float]:
        """(claim count, total paid, total reserve, total incurred) as printed in the totals block."""
        printed = self.printed_claims()
        hidden = [claim for claim in self.claims if claim.hidden]
        count = len(printed) + len(hidden)
        paid = sum(claim.paid for claim in printed) + sum(claim.paid for claim in hidden) + self.summary_extra_paid
        reserve = sum(claim.shown_reserve for claim in printed) + sum(claim.reserve for claim in hidden)
        incurred = (
            sum(claim.shown_incurred for claim in printed)
            + sum(claim.incurred for claim in hidden)
            + self.summary_extra_incurred
        )
        return count, round(paid, 2), round(reserve, 2), round(incurred, 2)


@dataclass
class InsuranceCase:
    """One SYNTHETIC commercial property / general liability submission."""

    number: int
    packet_id: str
    application_date: date
    effective_date: date
    named_insured: str
    fein: str
    years_in_business: int
    class_code: str
    coverage: str
    gl_limits: tuple[float, float] | None
    stated_tiv: float
    annual_revenue: float
    annual_payroll: float
    subcontracted_pct: float
    coi_tracking: bool
    foreign_products: bool
    stated_claim_count: int
    stated_total_incurred: float
    owners: list[Party]
    locations: list[LocationData]
    runs: list[CarrierRun]
    remarks: str
    broker_name: str
    brokerage: str
    signer: str
    schedule_signed: date
    inspections: dict[str, date]
    inspector: str
    email_paragraphs: list[str] = field(default_factory=list)
    doc_names: dict[str, str] = field(default_factory=dict)
    statement_members: list[Party] = field(default_factory=list)
    missing_carrier: str | None = None
    missing_ownership_statement: bool = False
    injection: InjectionPlan | None = None

    @property
    def application_id(self) -> str:
        """APP-I-<nnn>, matching the packet number."""
        return f"APP-I-{self.number:03d}"

    @property
    def current_term_start(self) -> date:
        """The start of the current policy term (one year before the proposed effective date)."""
        return self.effective_date.replace(year=self.effective_date.year - 1)

    def schedule_tiv(self) -> float:
        """Total insured value on the property schedule."""
        return round(sum(location.tiv for location in self.locations), 2)

    def present_runs(self) -> list[CarrierRun]:
        """Loss runs in the packet (a missing carrier's run is left out), in policy-year order."""
        return [run for run in self.runs if run.carrier != self.missing_carrier]

    def name_on(self, doc_key: str) -> str:
        """The insured name printed on a document ("property_schedule", "ownership_statement", "loss_run:<carrier>")."""
        return self.doc_names.get(doc_key, self.named_insured)

    def all_claims(self) -> list[ClaimData]:
        """Every true claim of every carrier (printed, hidden or on a missing loss run)."""
        return [claim for run in self.runs for claim in run.claims]
