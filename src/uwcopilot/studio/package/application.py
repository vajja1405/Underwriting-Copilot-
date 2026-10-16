"""to_application: a founder package -> the lender copilot's StartupCreditApplication (schema startup-application-v1).

    application = to_application(package)         # validated, line "startup_credit", data label SYNTHETIC

Every field comes from the package's ledger or its applicant facts; nothing is typed here. The rules (S6 DELTA):
- the metro is the registry metro id (config/studio/metros) and the industry is the business type's OWN code
  (car_wash -> car_wash, ...), so the program reads the category the business type really has (NSP-2.3);
- the projection is the simulation's years 1-3 at P10 / P50 / P90 (revenue and NOI); NOI is at most revenue at every
  percentile, and a package where it is not is refused (never clamped);
- the top drivers are the material assumptions with tornado ranks 1, 2 and 3 exactly (ranks are unique);
- every other loan or seller note is listed with its amount and monthly payment;
- an evidence flag counts only with its document in the file: equity_verified_by_statements needs the equity claim
  VERIFIED by bank statements AND equity_bank_statements in documents_provided; experience_evidence_provided needs
  owner_resumes_and_references; a driver's supporting_document_provided needs a quote, lease or letter of intent, or
  comparable data that verifies its figure AND is in the file;
- code fields (business type, industry, metro, state, purpose, debt kind, document codes, assumption names and
  units) are codes; the founder's free text (business and owner names) goes only into name fields, and the notes and
  site description stay empty;
- a field the program needs but the package does not have (experience months, a note rate, a non-negative cash
  reserve, three simulated years) stops the conversion with ApplicationNotReady and the list of what is missing.
"""

from datetime import date
from typing import Any

from uwcopilot.agent.applications import StartupCreditApplication
from uwcopilot.agent.policy_startup import EQUITY_STATEMENTS_DOCUMENT, EXPERIENCE_EVIDENCE_DOCUMENT
from uwcopilot.evidence.ledger_models import Claim
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package.models import Package

APPLICATION_ID_DIGITS = 8
PERCENTILES: tuple[str, ...] = ("p10", "p50", "p90")
PROJECTION_YEARS = 3
MONTHS_PER_YEAR = 12


class ApplicationNotReady(ValueError):
    """The package lacks a value the startup application needs; .missing lists them."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__("the package cannot be handed to the lender yet: " + "; ".join(self.missing))


class LedgerReader:
    """Reads claim values for the application and collects what is missing instead of guessing."""

    def __init__(self, package: Package) -> None:
        self.package = package
        self.claims = {item.id: item for item in package.ledger.claims}
        self.missing: list[str] = []

    def claim(self, claim_id: str) -> Claim | None:
        """A claim (None when absent)."""
        return self.claims.get(claim_id)

    def number(self, claim_id: str, what: str) -> float:
        """A claim's numeric value; records `what` as missing (and returns 0.0) when there is none."""
        item = self.claims.get(claim_id)
        if (
            item is None
            or item.value is None
            or isinstance(item.value, bool)
            or not isinstance(item.value, int | float)
        ):
            self.missing.append(what)
            return 0.0
        return float(item.value)

    def text(self, claim_id: str, what: str) -> str:
        """A claim's text value (missing when absent)."""
        item = self.claims.get(claim_id)
        if item is None or not isinstance(item.value, str):
            self.missing.append(what)
            return ""
        return item.value


def application_id_for(package: Package) -> str:
    """APP-S-<8 digits> from the package's inputs hash (stable for the same package)."""
    number = int(package.inputs_sha256[:12], 16) % 10**APPLICATION_ID_DIGITS
    return f"APP-S-{number:0{APPLICATION_ID_DIGITS}d}"


def verified_by(claim: Claim | None, document: str) -> bool:
    """True when a document of this type in the ledger VERIFIES the claim."""
    if claim is None or claim.status != "VERIFIED" or claim.provenance.kind != "document":
        return False
    return document in str(claim.provenance.details.get("document_types", "")).split(", ")


def owners(package: Package, reader: LedgerReader) -> list[dict[str, Any]]:
    """The owners as the program reads them (experience in years; flags need their documents)."""
    provided = set(package.applicant.documents_provided)
    rows = []
    for owner in package.applicant.owners:
        if owner.experience_months is None:
            reader.missing.append(f"relevant experience of owner {owner.owner_id} (NSP-2.1)")
        months = owner.experience_months or 0
        rows.append(
            {
                "owner_id": owner.owner_id,
                "name": owner.name,
                "entity_type": "person",
                "ownership_pct": owner.ownership_pct,
                "country": owner.country,
                "is_control_person": owner.is_control_person,
                "credit_score": owner.credit_score,
                "bankruptcy_within_7_years": owner.bankruptcy_within_7_years,
                "years_relevant_experience": months / MONTHS_PER_YEAR,
                "experience_evidence_provided": owner.experience_months is not None
                and EXPERIENCE_EVIDENCE_DOCUMENT in provided,
                "tax_lien_unresolved": owner.tax_lien_unresolved,
                "personal_guarantee": owner.personal_guarantee,
            }
        )
    return rows


def sources_and_uses(package: Package, reader: LedgerReader, loan_amount: float) -> dict[str, Any]:
    """Uses (build-out and equipment, working-capital reserve) and sources (loan, equity, other debt)."""
    capex_id = ids.FOUNDER_CAPEX if reader.claim(ids.FOUNDER_CAPEX) is not None else ids.ASSUME_CAPEX
    capex = reader.number(capex_id, "build-out and equipment cost")
    reserve = reader.number(ids.CALC_CASH_RESERVE, "cash reserve at opening")
    if reserve < 0:
        reader.missing.append("a non-negative cash reserve at opening (the uses exceed the sources)")
    equity_claim = reader.claim(ids.FOUNDER_EQUITY)
    equity = reader.number(ids.FOUNDER_EQUITY, "owner cash equity")
    other_debt = []
    for index, kind in enumerate(package.applicant.other_debt_kinds, start=1):
        other_debt.append(
            {
                "kind": kind,
                "amount": reader.number(ids.other_debt_id(index, "amount"), f"other debt {index} amount"),
                "monthly_payment": reader.number(
                    ids.other_debt_id(index, "monthly_payment"), f"other debt {index} monthly payment"
                ),
            }
        )
    statements_listed = EQUITY_STATEMENTS_DOCUMENT in package.applicant.documents_provided
    return {
        "uses": {"build_out": capex, "working_capital_reserve": max(reserve, 0.0)},
        "sources": {"loan": loan_amount, "owner_cash_equity": equity, "other_debt": other_debt},
        "equity_verified_by_statements": verified_by(equity_claim, EQUITY_STATEMENTS_DOCUMENT) and statements_listed,
    }


def projection_years(reader: LedgerReader) -> list[dict[str, Any]]:
    """Years 1-3 at P10 / P50 / P90; NOI may not exceed revenue at any percentile (refused, never clamped)."""
    years = []
    for year in range(1, PROJECTION_YEARS + 1):
        row: dict[str, Any] = {"year": year}
        for field in ("revenue", "noi"):
            for percentile in PERCENTILES:
                claim_id = ids.year_id(year, f"{field}_{percentile}")
                row[f"{field}_{percentile}"] = reader.number(claim_id, f"year {year} {field} {percentile.upper()}")
        for percentile in PERCENTILES:
            if row[f"noi_{percentile}"] > row[f"revenue_{percentile}"]:
                reader.missing.append(f"year {year}: NOI {percentile.upper()} is above revenue {percentile.upper()}")
        years.append(row)
    return years


def projections(package: Package, reader: LedgerReader) -> dict[str, Any]:
    """The projection summary, monthly fixed costs and the simulation's two information-only outputs (NSP-4.4)."""
    run = package.run
    return {
        "years": projection_years(reader),
        "monthly_fixed_costs": reader.number(ids.CALC_FIXED_COSTS, "monthly fixed costs"),
        "simulation": {
            "simulator_version": run.simulator_version,
            "n_paths": run.n_paths,
            "seed": run.seed,
            "dscr_target": run.dscr_target,
            "p_dscr_ge_target_year2": reader.number(ids.SIM_P_DSCR_Y2, "simulated chance of the DSCR target") / 100.0,
            "p_cash_negative_24m": reader.number(ids.SIM_P_CASH, "simulated chance of negative cash") / 100.0,
        },
    }


def material_assumptions(package: Package) -> list[dict[str, Any]]:
    """The assumption list; a driver's document flag needs its document in the file."""
    provided = set(package.applicant.documents_provided)
    return [
        {
            "name": item.name,
            "value": item.value,
            "unit": item.unit,
            "status": item.status,
            "source": item.source,
            "supporting_document_provided": bool(set(item.supporting_document_types) & provided),
            "tornado_rank": item.tornado_rank,
        }
        for item in package.material_assumptions
    ]


def to_application(
    package: Package, *, application_id: str | None = None, submitted_date: date | None = None
) -> StartupCreditApplication:
    """The startup credit application of a founder package (module docstring)."""
    reader = LedgerReader(package)
    applicant = package.applicant
    amount = reader.number(ids.FOUNDER_LOAN, "loan amount")
    data: dict[str, Any] = {
        "application_id": application_id or application_id_for(package),
        "submitted_date": (submitted_date or package.prepared_on).isoformat(),
        "applicant": {
            "business_name": applicant.business_name,
            "business_type": applicant.business_type,
            "industry": applicant.business_type,
            "entity_type": applicant.entity_type,
            "state": applicant.state,
            "formation_date": applicant.formation_date.isoformat() if applicant.formation_date else None,
            "planned_opening_date": applicant.planned_opening_date.isoformat(),
            "metro_id": applicant.metro_id,
            "site_description": "",
        },
        "owners": owners(package, reader),
        "loan_request": {
            "amount": amount,
            "term_months": int(reader.number(ids.FOUNDER_TERM, "loan term")),
            "annual_rate_pct": reader.number(ids.RATE_NOTE, "note rate"),
            "rate_type": reader.text(ids.PLAN_RATE_TYPE, "rate type"),
            "purpose": applicant.loan_purpose,
        },
        "sources_and_uses": sources_and_uses(package, reader, amount),
        "projections": projections(package, reader),
        "material_assumptions": material_assumptions(package),
        "collateral": [item.model_dump() for item in applicant.collateral],
        "documents_provided": list(applicant.documents_provided),
        "notes": "",
    }
    if reader.missing:
        raise ApplicationNotReady(reader.missing)
    return StartupCreditApplication.model_validate(data)
