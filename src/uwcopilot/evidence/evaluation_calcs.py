"""Financial calculation agreement (Phase 4 F): the reference policy's calculations on the engine's application
(read from the documents) against the same calculations on the truth's application_as_documented.

Both sides are agent.policy Calculation lists (calc_id, unit, value). Every calc id on either side is compared once:
- both values present: they agree within the tolerance of their unit (TOLERANCES): money ("usd", "usd_per_sqft")
  within $1.00 or 0.5% of the truth value, whichever is larger; ratios ("ratio", "probability") within 0.01;
  percentages ("pct") within 0.1 (percentage points); counts, months, days and scores exactly;
- both None (the formula cannot be computed on either side, e.g. a percentage of zero): agree;
- one None, or the calc id only on one side ("missing": the truth has it, the engine does not, for example because
  the engine's application did not validate; "extra": the engine has a calculation the truth's application does
  not): disagree.
EPSILON absorbs floating-point noise only (a ratio that differs by exactly 0.01 agrees).
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

MONEY_UNITS: frozenset[str] = frozenset({"usd", "usd_per_sqft"})
RATIO_UNITS: frozenset[str] = frozenset({"ratio", "probability"})
PERCENT_UNITS: frozenset[str] = frozenset({"pct"})
MONEY_ABSOLUTE = 1.00
MONEY_RELATIVE = 0.005
RATIO_TOLERANCE = 0.01
PERCENT_TOLERANCE = 0.1
EPSILON = 1e-9
TOLERANCES: dict[str, str] = {
    "usd, usd_per_sqft": "within $1.00 or 0.5% of the truth value, whichever is larger",
    "ratio, probability": "within 0.01",
    "pct": "within 0.1 percentage points",
    "months, count, days, score": "exact",
}


def within_tolerance(unit: str, truth_value: float, value: float) -> bool:
    """True when value agrees with truth_value within the tolerance of the unit (module docstring)."""
    difference = abs(value - truth_value)
    if unit in MONEY_UNITS:
        allowed = max(MONEY_ABSOLUTE, MONEY_RELATIVE * abs(truth_value))
    elif unit in RATIO_UNITS:
        allowed = RATIO_TOLERANCE
    elif unit in PERCENT_UNITS:
        allowed = PERCENT_TOLERANCE
    else:
        allowed = 0.0
    return difference <= allowed + EPSILON


def compare_one(truth: Any | None, found: Any | None) -> str:
    """'agree', 'disagree', 'missing' (only the truth has it) or 'extra' (only the engine has it)."""
    if found is None:
        return "missing"
    if truth is None:
        return "extra"
    if truth.value is None and found.value is None:
        return "agree"
    if truth.value is None or found.value is None:
        return "disagree"
    return "agree" if within_tolerance(truth.unit, truth.value, found.value) else "disagree"


def calc_rows(truth_calcs: Iterable[Any], found_calcs: Iterable[Any]) -> list[dict[str, Any]]:
    """One JSON-simple row per calc id on either side, sorted by calc id."""
    truth_by_id = {item.calc_id: item for item in truth_calcs}
    found_by_id = {item.calc_id: item for item in found_calcs}
    rows = []
    for calc_id in sorted(set(truth_by_id) | set(found_by_id)):
        truth, found = truth_by_id.get(calc_id), found_by_id.get(calc_id)
        unit = (truth or found).unit
        rows.append(
            {
                "calc_id": calc_id,
                "unit": unit,
                "truth": None if truth is None else truth.value,
                "found": None if found is None else found.value,
                "outcome": compare_one(truth, found),
            }
        )
    return rows


def calc_counts(rows: list[dict[str, Any]]) -> Counter:
    """calcs.* counters: totals and agreements overall, per unit and per calc id, and the outcome counts."""
    counts: Counter = Counter()
    for row in rows:
        agree = int(row["outcome"] == "agree")
        for group in ("calcs.all", f"calcs.unit.{row['unit']}", f"calcs.id.{row['calc_id']}"):
            counts[f"{group}.total"] += 1
            counts[f"{group}.agree"] += agree
        counts[f"calcs.outcome.{row['outcome']}"] += 1
    return counts
