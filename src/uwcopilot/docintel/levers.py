"""Levers: apply resolution substitutions to an agent application (plain JSON dict), as in resolution_templates.yaml.

A substitution dict maps a lever to a value. The lever vocabulary (config/resolution_templates.yaml):

- "<dotted.path[i]>": set a field of the CreditApplication / InsuranceApplication JSON, for example
  "stated_annual_revenue", "debt.existing_monthly_payment_schedule" or "loss_runs.years[2].claims[0].reserve".
  A path that names a whole list ("owners") replaces the list.
- "+<list.path>": append to a list. A dict value is appended; a list value appends each item. Two lists are kept
  sorted after an append: bank_statements.months by "month" and loss_runs.years by "policy_year_start".
- "documents_provided+": add a document code (or a list of codes) to documents_provided, if not already there.
- "@verified_annual_revenue": rebalance the MonthlyDeposits rows so that NBC-3.2 verified annual revenue
  (agent.credit_calculations.verify_revenue) equals the value. See rebalance_verified_revenue for the exact rule.
- "@schedule_tiv": scale every location's building, contents and business income values by one factor so the
  property schedule TIV (agent.insurance_calculations.schedule_tiv) equals the value (cents fixed on the largest
  building value).
- "check:<check_id>": a document-check outcome ("resolved" or "confirmed"). It does not change the application;
  apply_substitutions skips it and check_overrides() returns these levers.

The result is a new dict; the input is never changed. This module is shared by the SYNTHETIC packet generator
(to build application_latent) and the Phase 4 evidence engine, so it must not import the generator or the truth
models. Validate the result with uwcopilot.agent.applications.parse_application.
"""

import copy
import re
from typing import Any

from uwcopilot.agent.applications import MonthlyDeposits
from uwcopilot.agent.credit_calculations import MONTHS_REQUIRED, month_eligibility, month_index

CHECK_PREFIX = "check:"
APPEND_PREFIX = "+"
ADD_DOCUMENT_LEVER = "documents_provided+"
VERIFIED_REVENUE_LEVER = "@verified_annual_revenue"
SCHEDULE_TIV_LEVER = "@schedule_tiv"
# Lists that stay sorted after an append, and the key they are sorted by.
SORTED_LISTS: dict[str, str] = {"bank_statements.months": "month", "loss_runs.years": "policy_year_start"}
PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
MAX_ITERATIONS = 40
CENT = 0.01


class LeverError(ValueError):
    """A substitution could not be applied (unknown path, wrong type, or an unreachable target)."""


def parse_path(path: str) -> list[str | int]:
    """'loss_runs.years[2].claims' -> ['loss_runs', 'years', 2, 'claims']."""
    parts: list[str | int] = []
    for name, index in PATH_PART.findall(path):
        parts.append(int(index) if index else name)
    if not parts:
        raise LeverError(f"empty path: {path!r}")
    return parts


def walk(data: Any, parts: list[str | int], path: str) -> Any:
    """The value at parts inside data (dict keys and list indexes)."""
    current = data
    for part in parts:
        try:
            current = current[part]
        except (KeyError, IndexError, TypeError) as error:
            raise LeverError(f"path {path!r} does not exist in the application") from error
    return current


def set_path(data: dict, path: str, value: Any) -> None:
    """Set the value at a dotted path (the parent must exist; the last key may be new for a dict)."""
    parts = parse_path(path)
    parent = walk(data, parts[:-1], path)
    last = parts[-1]
    if isinstance(parent, list):
        if not isinstance(last, int) or last >= len(parent):
            raise LeverError(f"path {path!r} does not name a list item")
        parent[last] = copy.deepcopy(value)
    elif isinstance(parent, dict):
        parent[last] = copy.deepcopy(value)
    else:
        raise LeverError(f"path {path!r} does not end in a dict or list")


def append_path(data: dict, path: str, value: Any) -> None:
    """Append a dict (or every item of a list) to the list at path; keep known lists sorted."""
    target = walk(data, parse_path(path), path)
    if not isinstance(target, list):
        raise LeverError(f"path {path!r} is not a list")
    items = value if isinstance(value, list) else [value]
    for item in items:
        target.append(copy.deepcopy(item))
    sort_key = SORTED_LISTS.get(path)
    if sort_key is not None:
        target.sort(key=lambda item: str(item.get(sort_key, "")))


def add_documents(data: dict, value: str | list[str]) -> None:
    """Add one document code (or several) to documents_provided, keeping the existing order."""
    codes = value if isinstance(value, list) else [value]
    provided = data.setdefault("documents_provided", [])
    for code in codes:
        if code not in provided:
            provided.append(code)


# --------------------------------------------------------------------------------------------------------------
# @verified_annual_revenue
# --------------------------------------------------------------------------------------------------------------


def window_months(data: dict) -> list[dict]:
    """The MonthlyDeposits dicts of the 12 most recent months (all months when fewer), oldest first."""
    months = data.get("bank_statements", {}).get("months")
    if not months:
        raise LeverError("the application has no bank statement months to rebalance")
    ordered = sorted(months, key=lambda month: month_index(month["month"]))
    return ordered[-MONTHS_REQUIRED:]


def eligible(month: dict, cash_intensive: bool) -> float:
    """NBC-3.3 eligible deposits of one month (agent.credit_calculations.month_eligibility)."""
    return month_eligibility(MonthlyDeposits.model_validate(month), cash_intensive).eligible


def total_eligible(months: list[dict], cash_intensive: bool) -> float:
    """Sum of eligible deposits over the months."""
    return sum(eligible(month, cash_intensive) for month in months)


def convert_unidentified(months: list[dict], needed: float, cash_intensive: bool) -> None:
    """Treat unidentified deposits as revenue, in proportion to each month's amount, until `needed` is added.

    Moving an amount out of unidentified_deposits makes it non-cash revenue (total_deposits does not change).
    """
    for _ in range(MAX_ITERATIONS):
        pool = sum(month.get("unidentified_deposits", 0.0) for month in months)
        if needed <= CENT / 2 or pool <= CENT / 2:
            return
        before = total_eligible(months, cash_intensive)
        share = min(1.0, needed / pool)
        for month in months:
            month["unidentified_deposits"] = month.get("unidentified_deposits", 0.0) * (1.0 - share)
        needed -= total_eligible(months, cash_intensive) - before


def revenue_room(month: dict) -> float:
    """How far non-cash revenue of a month can fall: total minus non-revenue minus cash, never below zero."""
    non_revenue = sum(
        month.get(name, 0.0)
        for name in (
            "transfers_between_accounts",
            "refunds_and_reversals",
            "loan_proceeds",
            "owner_contributions",
            "one_time_receipts",
            "unidentified_deposits",
        )
    )
    return max(0.0, month["total_deposits"] - non_revenue - month.get("cash_deposits", 0.0))


def scale_revenue(months: list[dict], target: float, cash_intensive: bool) -> None:
    """Change each month's non-cash revenue (total_deposits) in proportion to its eligible deposits."""
    for _ in range(MAX_ITERATIONS):
        residual = target - total_eligible(months, cash_intensive)
        if abs(residual) <= CENT / 4:
            return
        weights = [max(eligible(month, cash_intensive), 0.0) for month in months]
        weight_total = sum(weights) or float(len(months))
        for month, weight in zip(months, weights, strict=True):
            change = residual * (weight or 1.0) / weight_total
            change = max(change, -revenue_room(month))
            month["total_deposits"] = month["total_deposits"] + change


def round_months(months: list[dict]) -> None:
    """Round every amount of the months to cents."""
    for month in months:
        for name, value in month.items():
            if name != "month":
                month[name] = round(float(value), 2)


def fix_cents(months: list[dict], target: float, cash_intensive: bool) -> None:
    """Put the last cents of difference on the month with the most room, so the total matches to the cent."""
    residual = round(target - total_eligible(months, cash_intensive), 2)
    if residual == 0:
        return
    best = max(months, key=revenue_room)
    best["total_deposits"] = round(best["total_deposits"] + residual, 2)
    if eligible(best, cash_intensive) < 0:
        raise LeverError("cannot reach the verified revenue target")


def rebalance_verified_revenue(data: dict, target: float) -> None:
    """Make NBC-3.2 verified annual revenue equal `target` by editing the MonthlyDeposits rows.

    The rule, in order, over the 12 most recent statement months (all months when fewer are present):
    1. to raise verified revenue, first treat unidentified deposits as revenue (NBC-3.3: they were excluded only
       because their nature was unknown), in proportion to each month's unidentified amount;
    2. then change each month's non-cash revenue (total_deposits) in proportion to its eligible deposits (the cash
       cap of NBC-3.3 is recomputed each round, so a few rounds are needed);
    3. round to cents and put the last cents on the month with the most revenue.
    Non-revenue categories other than unidentified deposits are never changed. Raises LeverError when the target
    cannot be reached (for example a target below zero).
    """
    if target < 0:
        raise LeverError("verified revenue cannot be negative")
    statements = data["bank_statements"]
    cash_intensive = bool(statements.get("cash_intensive_approved", False))
    months = window_months(data)
    current = total_eligible(months, cash_intensive)
    if target > current:
        convert_unidentified(months, target - current, cash_intensive)
    scale_revenue(months, target, cash_intensive)
    round_months(months)
    fix_cents(months, target, cash_intensive)
    if abs(total_eligible(months, cash_intensive) - target) > CENT + 1e-9:
        raise LeverError(f"verified revenue target {target:,.2f} could not be reached")


# --------------------------------------------------------------------------------------------------------------
# @schedule_tiv
# --------------------------------------------------------------------------------------------------------------

VALUE_FIELDS: tuple[str, ...] = ("building_value", "contents_value", "business_income_value")


def location_total(location: dict) -> float:
    """Building + contents + business income of one location (HM-1.2 TIV)."""
    return sum(float(location[name]) for name in VALUE_FIELDS)


def scale_schedule_tiv(data: dict, target: float) -> None:
    """Scale all location values by target / current schedule TIV; fix the cents on the largest building value."""
    locations = data.get("locations") or []
    current = sum(location_total(location) for location in locations)
    if current <= 0 or target <= 0:
        raise LeverError("schedule TIV and the target must be positive")
    factor = target / current
    for location in locations:
        for name in VALUE_FIELDS:
            location[name] = round(float(location[name]) * factor, 2)
    residual = round(target - sum(location_total(location) for location in locations), 2)
    largest = max(locations, key=lambda location: float(location["building_value"]))
    largest["building_value"] = round(float(largest["building_value"]) + residual, 2)


# --------------------------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------------------------


def apply_lever(data: dict, lever: str, value: Any) -> None:
    """Apply one lever to data in place (check levers are skipped)."""
    if lever.startswith(CHECK_PREFIX):
        return
    if lever == ADD_DOCUMENT_LEVER:
        add_documents(data, value)
    elif lever == VERIFIED_REVENUE_LEVER:
        rebalance_verified_revenue(data, float(value))
    elif lever == SCHEDULE_TIV_LEVER:
        scale_schedule_tiv(data, float(value))
    elif lever.startswith(APPEND_PREFIX):
        append_path(data, lever[len(APPEND_PREFIX) :], value)
    else:
        set_path(data, lever, value)


def apply_substitutions(application: dict, substitutions: dict[str, Any]) -> dict:
    """A copy of the application JSON with every substitution applied, in the dict's order."""
    result = copy.deepcopy(application)
    for lever, value in substitutions.items():
        apply_lever(result, lever, value)
    return result


def check_overrides(substitutions: dict[str, Any]) -> dict[str, str]:
    """The "check:<check_id>" levers of a substitution dict, as {check_id: "resolved" | "confirmed"}."""
    return {
        lever[len(CHECK_PREFIX) :]: str(value)
        for lever, value in substitutions.items()
        if lever.startswith(CHECK_PREFIX)
    }
