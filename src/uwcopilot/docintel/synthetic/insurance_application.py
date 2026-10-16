"""application_as_documented for a SYNTHETIC insurance submission: what a perfect reader of its documents concludes.

The derivation of every field is written down in insurance_case.py; this module computes it from the generator's
model (printed values, the documents actually in the packet).
"""

from datetime import date

from uwcopilot.docintel.synthetic.insurance_case import CarrierRun, ClaimData, InsuranceCase

INSPECTION_ABOVE_TIV = 5_000_000


def next_year(start: date) -> date:
    """The same day one year later."""
    return start.replace(year=start.year + 1)


def claim_dict(claim: ClaimData, printed: bool = True) -> dict:
    """An agent Claim dict (printed values, or the true ones)."""
    return {
        "date_of_loss": claim.date_of_loss.isoformat(),
        "line": claim.line,
        "paid": claim.paid,
        "reserve": claim.shown_reserve if printed else claim.reserve,
        "incurred": claim.shown_incurred if printed else claim.incurred,
    }


def year_dicts(run: CarrierRun, claims: list[ClaimData], printed: bool = True) -> list[dict]:
    """LossRunYear dicts for a run's experience years with the given claims placed by date of loss."""
    years = []
    for year in run.years:
        inside = [claim for claim in claims if year.start <= claim.date_of_loss < next_year(year.start)]
        inside.sort(key=lambda claim: (claim.date_of_loss, claim.claim_number))
        years.append(
            {
                "policy_year_start": year.start.isoformat(),
                "property_earned_premium": year.property_premium,
                "gl_earned_premium": year.gl_premium,
                "claims": [claim_dict(claim, printed) for claim in inside],
            }
        )
    return years


def loss_runs(case: InsuranceCase) -> dict:
    """The LossRuns block from the loss runs in the packet."""
    runs = case.present_runs()
    years = [year for run in runs for year in year_dicts(run, run.printed_claims())]
    years.sort(key=lambda year: year["policy_year_start"])
    current = [claim for run in runs for claim in run.printed_claims() if claim.date_of_loss >= case.current_term_start]
    current.sort(key=lambda claim: (claim.date_of_loss, claim.claim_number))
    return {
        "carrier_produced": True,
        "valued_as_of": min(run.valued_as_of for run in runs).isoformat(),
        "years": years,
        "uncovered_years": 0,
        "uncovered_years_explained": True,
        "current_term_claims": [claim_dict(claim) for claim in current],
    }


def documents_provided(case: InsuranceCase) -> list[str]:
    """Checklist codes the submission's documents satisfy (see insurance_case.py)."""
    provided = ["signed_application", "property_schedule"]
    if case.missing_carrier is None:
        provided.append("loss_runs")
    provided.extend(["prior_carrier_history", "revenue_and_payroll"])
    if not case.missing_ownership_statement:
        provided.append("beneficial_ownership_statement")
    large = [location.location_id for location in case.locations if location.tiv > INSPECTION_ABOVE_TIV]
    if large and all(location_id in case.inspections for location_id in large):
        provided.append("inspection_report")
    return provided


def insurance_as_documented(case: InsuranceCase) -> dict:
    """The InsuranceApplication JSON a perfect reader of this submission would build (validated by the caller)."""
    return {
        "application_id": case.application_id,
        "data_label": "SYNTHETIC",
        "line": "insurance",
        "institution": "harborline_mutual_commercial",
        "application_date": case.application_date.isoformat(),
        "proposed_effective_date": case.effective_date.isoformat(),
        "applicant": {
            "legal_name": case.named_insured,
            "fein": case.fein,
            "years_in_business": case.years_in_business,
            "prior_experience_years": 0,
            "class_code": case.class_code,
            "fryer_suppression_inspected_within_6_months": False,
        },
        "coverage": case.coverage,
        "gl_limit_per_occurrence": None if case.gl_limits is None else case.gl_limits[0],
        "gl_limit_aggregate": None if case.gl_limits is None else case.gl_limits[1],
        "stated_tiv": case.stated_tiv,
        "locations": [
            {
                "location_id": location.location_id,
                "construction_class": location.construction_class,
                "year_built": location.year_built,
                "roof_year": location.roof_year,
                "stories": location.stories,
                "square_feet": location.square_feet,
                "sprinklered": location.sprinklered,
                "protection_class": location.protection_class,
                "building_value": location.building_value,
                "contents_value": location.contents_value,
                "business_income_value": location.business_income_value,
            }
            for location in case.locations
        ],
        "loss_runs": loss_runs(case),
        "stated_claim_count": case.stated_claim_count,
        "stated_total_incurred": case.stated_total_incurred,
        "annual_revenue": case.annual_revenue,
        "annual_payroll": case.annual_payroll,
        "subcontracted_work_pct": case.subcontracted_pct,
        "has_subcontractor_coi_tracking": case.coi_tracking,
        "foreign_products": case.foreign_products,
        "owners": [
            {
                "owner_id": f"O{index}",
                "name": owner.name,
                "entity_type": owner.entity_type,
                "ownership_pct": owner.ownership_pct,
                "country": owner.country,
                "is_control_person": owner.is_control_person,
            }
            for index, owner in enumerate(case.owners, start=1)
        ],
        "documents_provided": documents_provided(case),
        "notes": case.remarks,
        "ground_truth": None,
    }
