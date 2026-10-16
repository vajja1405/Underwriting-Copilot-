"""Map an InsuranceSubmission extraction to the agent's InsuranceApplication JSON, with provenance.

The derivation of every field follows the rules written down for the as-documented application:
- application_id "APP-I-<nnn>"; dates, coverage, GL limits (None when the form says "Not requested"), stated TIV,
  revenue, payroll, subcontracted share, COI tracking, foreign products, stated claim count and incurred losses:
  the insurance application form. Every one of them records its source (doc, page, line) in the provenance. A GL
  limit the form leaves empty is None without an unknown entry (builder.value(..., optional=True)) ONLY when the
  form's coverage was read and has no general liability (a property-only quote has no GL limits:
  gl_limits_optional); on a GL or package request, or when the coverage itself was not read, an empty GL limit is
  an unknown like any other unread value;
- applicant: named insured, FEIN, years in business and business class (class_code) from the form;
  prior_experience_years 0 and fryer_suppression_inspected_within_6_months False (no document carries them);
- locations: one per property schedule row with the fields the schedule prints. The fields no packet document
  prints (systems_updated_year, knob_and_tube_or_fuse_box, wind_tier, flood_zone, wildfire_hazard, vacant_days)
  take the agent Location model's defaults, written out explicitly; they have no provenance entry;
- loss_runs: carrier_produced True when every loss run says it is carrier-produced; valued_as_of = the EARLIEST
  "valued as of" date; years = one LossRunYear per premium-by-year row of every loss run, sorted by policy year
  start, holding that run's claim rows dated in [start, start + 1 year), ordered by (date of loss, claim number);
  current_term_claims = every claim row dated on or after the current term start (the proposed effective date
  one year earlier), same order; uncovered_years 0 and uncovered_years_explained True (loss-run coverage is the
  loss_run_coverage check's job). Claim amounts are the printed ones. When a loss run's printed summary does not
  equal its claim rows (checks_loss_runs.summary_differences: claim count, or totals more than $1.00 apart: a row
  OCR could not read, or a summary that is itself wrong), the rows are still used, but every policy year of that
  run (and current_term_claims when the run reaches the current term) is listed in unknowns: the reviewer and the
  agent see that the loss history may be incomplete;
- owners: the application's owners table (O1, O2, ...); no insurance document prints a credit score or a
  bankruptcy flag, so those take the Owner model's defaults (None, False). A control-person cell that was not
  read is False and its path is listed in unknowns;
- documents_provided (HM-3.1, in the agent's INSURANCE_DOCUMENTS order): checks_insurance.documents_provided, the
  same rules as the required_documents check: signed_application, prior_carrier_history and revenue_and_payroll
  from the form; property_schedule (HM-5.1 signature age); loss_runs only when EVERY carrier on the application's
  prior carriers table has a loss run in the packet (checks_loss_runs.every_prior_carrier_has_a_run);
  beneficial_ownership_statement; inspection_report when every location above $5,000,000 has a current one;
- notes: the Remarks text ("" when there is none); ground_truth None.
"""

from datetime import date

from uwcopilot.agent.applications import INSURANCE_DOCUMENTS, Location, Owner
from uwcopilot.docintel.checks_insurance import documents_provided as insurance_documents_provided
from uwcopilot.docintel.checks_loss_runs import summary_differences, years_before
from uwcopilot.docintel.schemas import InsuranceApplicationForm, InsuranceSubmission, LossRun, LossRunClaim
from uwcopilot.docintel.to_application_common import (
    ApplicationBuilder,
    Provenance,
    application_id,
    iso,
    money,
    provided_codes,
)

LOCATION_FIELDS: tuple[str, ...] = (
    "construction_class",
    "year_built",
    "roof_year",
    "stories",
    "square_feet",
    "sprinklered",
    "protection_class",
)
LOCATION_AMOUNTS: tuple[str, ...] = ("building_value", "contents_value", "business_income_value")
# Coverages that include general liability (the same set as agent.policy_insurance.has_gl).
GL_COVERAGES: frozenset[str] = frozenset({"general_liability", "package"})


def next_year(start: date) -> date:
    """The same calendar day one year later (29 February becomes 28 February)."""
    try:
        return start.replace(year=start.year + 1)
    except ValueError:
        return start.replace(year=start.year + 1, day=28)


def claim_dict(claim: LossRunClaim) -> dict:
    """An agent Claim dict from a printed claim row (None for a value that was not read)."""
    return {
        "date_of_loss": iso(claim.date_of_loss.value),
        "line": claim.line.value,
        "paid": money(claim.paid.value),
        "reserve": money(claim.reserve.value),
        "incurred": money(claim.incurred.value),
    }


def claim_order(claim: LossRunClaim) -> tuple[str, str]:
    """Sort key: (date of loss, claim number)."""
    return (iso(claim.date_of_loss.value) or "", str(claim.claim_number.value or ""))


def run_years(run: LossRun, builder: ApplicationBuilder) -> list[tuple[str, dict]]:
    """(policy year start, LossRunYear dict) for every premium row of one loss run, with its claims."""
    years = []
    for year in run.years:
        start = year.policy_year_start.value
        if start is None:
            continue
        inside = [claim for claim in run.claims if claim.date_of_loss.value is not None]
        inside = [claim for claim in inside if start <= claim.date_of_loss.value < next_year(start)]
        inside.sort(key=claim_order)
        entry = {
            "policy_year_start": start.isoformat(),
            "property_earned_premium": money(year.property_earned_premium.value),
            "gl_earned_premium": money(year.gl_earned_premium.value),
            "claims": [claim_dict(claim) for claim in inside],
        }
        years.append((start.isoformat(), entry))
        builder.computed(f"loss_runs.years[{start.isoformat()}]", entry, [year.policy_year_start])
    return years


def flag_incomplete_runs(runs: list[LossRun], effective: date | None, builder: ApplicationBuilder) -> None:
    """List the years of every loss run whose summary disagrees with its claim rows in unknowns (module docstring)."""
    term_start = None if effective is None else years_before(effective, 1)
    for run in runs:
        if not summary_differences(run):
            continue
        for year in run.years:
            start = year.policy_year_start.value
            if start is None:
                continue
            path = f"loss_runs.years[{start.isoformat()}]"
            if path not in builder.unknowns:
                builder.unknowns.append(path)
            reaches_term = term_start is not None and next_year(start) > term_start
            if reaches_term and "loss_runs.current_term_claims" not in builder.unknowns:
                builder.unknowns.append("loss_runs.current_term_claims")


def loss_runs(submission: InsuranceSubmission, effective: date | None, builder: ApplicationBuilder) -> dict:
    """The LossRuns dict (see the module docstring)."""
    runs = submission.loss_runs
    years = []
    for run in runs:
        years.extend(run_years(run, builder))
    years.sort(key=lambda item: item[0])
    flag_incomplete_runs(runs, effective, builder)
    valued = [run.valued_as_of for run in runs if run.valued_as_of.found]
    earliest = min(valued, key=lambda cited: cited.value) if valued else None
    current = []
    if effective is not None:
        term_start = years_before(effective, 1)
        dated = [claim for run in runs for claim in run.claims if claim.date_of_loss.value is not None]
        current = sorted((claim for claim in dated if claim.date_of_loss.value >= term_start), key=claim_order)
    produced = [run.carrier_produced for run in runs]
    return {
        "carrier_produced": builder.computed(
            "loss_runs.carrier_produced", bool(runs) and all(item.value is True for item in produced), produced
        ),
        "valued_as_of": None if earliest is None else builder.value("loss_runs.valued_as_of", earliest, iso),
        "years": [entry for _, entry in years],
        "uncovered_years": 0,
        "uncovered_years_explained": True,
        "current_term_claims": [claim_dict(claim) for claim in current],
    }


def locations(submission: InsuranceSubmission, builder: ApplicationBuilder) -> list[dict]:
    """One Location dict per property schedule row."""
    schedule = submission.property_schedule
    rows = []
    for index, location in enumerate([] if schedule is None else schedule.locations):
        path = f"locations[{index}]"
        row = {"location_id": builder.value(f"{path}.location_id", location.location_id)}
        for name in LOCATION_FIELDS:
            row[name] = builder.value(f"{path}.{name}", getattr(location, name))
        for name in LOCATION_AMOUNTS:
            row[name] = builder.value(f"{path}.{name}", getattr(location, name), money)
        for name, model_field in Location.model_fields.items():
            if name not in row:
                row[name] = model_field.default
        rows.append(row)
    if not rows:
        builder.unknowns.append("locations")
    return rows


def insurance_owners(form: InsuranceApplicationForm, builder: ApplicationBuilder) -> list[dict]:
    """The application's owners table."""
    owners = []
    for index, owner in enumerate(form.owners, start=1):
        path = f"owners[{index - 1}]"
        if not owner.is_control_person.found:
            builder.unknowns.append(f"{path}.is_control_person")
        owners.append(
            {
                "owner_id": f"O{index}",
                "name": builder.value(f"{path}.name", owner.name),
                "entity_type": builder.value(f"{path}.entity_type", owner.entity_type),
                "ownership_pct": builder.value(f"{path}.ownership_pct", owner.ownership_pct),
                "country": owner.country.value,
                "is_control_person": owner.is_control_person.value is True,
            }
        )
        for name, model_field in Owner.model_fields.items():
            if name not in owners[-1]:
                owners[-1][name] = model_field.default
    if not owners:
        builder.unknowns.append("owners")
    return owners


def insurance_documents(submission: InsuranceSubmission, builder: ApplicationBuilder) -> list[str]:
    """documents_provided for an insurance submission (see the module docstring)."""
    provided = set(insurance_documents_provided(submission))
    builder.documents(
        "documents_provided",
        [(item.doc_id, item.pages[0] if item.pages else None) for item in submission.documents],
    )
    return provided_codes(provided, INSURANCE_DOCUMENTS)


def gl_limits_optional(form: InsuranceApplicationForm) -> bool:
    """True when the form may leave its GL limits empty: its coverage was read and includes no general liability."""
    return form.coverage.found and form.coverage.value not in GL_COVERAGES


def insurance_application(submission: InsuranceSubmission) -> tuple[dict, Provenance, list[str]]:
    """The InsuranceApplication dict of an insurance submission extraction (see the module docstring)."""
    builder = ApplicationBuilder()
    form = submission.application or InsuranceApplicationForm(doc_id="")
    gl_optional = gl_limits_optional(form)
    if submission.application is None:
        builder.unknowns.append("application_form")
    application = {
        "application_id": application_id(submission.packet_id),
        "data_label": "SYNTHETIC",
        "line": "insurance",
        "institution": "harborline_mutual_commercial",
        "application_date": builder.value("application_date", form.application_date, iso),
        "proposed_effective_date": builder.value("proposed_effective_date", form.proposed_effective_date, iso),
        "applicant": {
            "legal_name": builder.value("applicant.legal_name", form.named_insured),
            "fein": builder.value("applicant.fein", form.fein),
            "years_in_business": builder.value("applicant.years_in_business", form.years_in_business),
            "prior_experience_years": 0,
            "class_code": builder.value("applicant.class_code", form.business_class),
            "fryer_suppression_inspected_within_6_months": False,
        },
        "coverage": builder.value("coverage", form.coverage),
        "gl_limit_per_occurrence": builder.value(
            "gl_limit_per_occurrence", form.gl_limit_per_occurrence, money, optional=gl_optional
        ),
        "gl_limit_aggregate": builder.value("gl_limit_aggregate", form.gl_limit_aggregate, money, optional=gl_optional),
        "stated_tiv": builder.value("stated_tiv", form.stated_tiv, money),
        "locations": locations(submission, builder),
        "loss_runs": loss_runs(submission, form.proposed_effective_date.value, builder),
        "stated_claim_count": builder.value("stated_claim_count", form.stated_claim_count),
        "stated_total_incurred": builder.value("stated_total_incurred", form.stated_total_incurred, money),
        "annual_revenue": builder.value("annual_revenue", form.annual_revenue, money),
        "annual_payroll": builder.value("annual_payroll", form.annual_payroll, money),
        "subcontracted_work_pct": builder.value("subcontracted_work_pct", form.subcontracted_work_pct),
        "has_subcontractor_coi_tracking": builder.value(
            "has_subcontractor_coi_tracking", form.has_subcontractor_coi_tracking
        ),
        "foreign_products": builder.value("foreign_products", form.foreign_products),
        "owners": insurance_owners(form, builder),
        "documents_provided": insurance_documents(submission, builder),
        "notes": form.remarks.value or "",
        "ground_truth": None,
    }
    return application, builder.provenance, builder.unknowns
