"""Ledger units and renderer display formats for Studio values (one rule, used by every claim builder).

The renderer (provenance/renderer.py) prints a claim in its display format: usd0 "$84,123", usd2 "$14.00", ratio2
"1.42", pct1 "25.0%", int "156", months "36 months", text as it is. Display rounding is presentation only; the claim
keeps the exact value and its inputs hash pins it.

Studio units are often descriptions ("share of revenue", "probability per year that a competitor opens"). The rule:
- a share or a probability (the unit text starts with "share", "probability" or "fraction", or says "as a share
  of") is shown as a percent: the value is multiplied by 100 and printed with pct1 (a probability is never printed
  as pct1 directly: 0.62 would read 0.6%);
- percentage points and "percent" are PERCENT with pct1;
- dollars use usd2 below $1,000 (prices and wages, where cents matter) and usd0 otherwise;
- months use "months"; counts (persons, households, workers, places), whole numbers, days per week and anything
  else of at least 10 use "int"; smaller values use ratio2, so a small value never prints as 0.
"""

import re
from dataclasses import dataclass

UNIT_SAFE = re.compile(r"[^A-Z0-9]+")
SMALL_VALUE = 10.0
DOLLAR_DETAIL = 1000.0
COUNT_UNITS: frozenset[str] = frozenset({"count", "persons", "households", "workers", "places"})


@dataclass(frozen=True)
class ValueFormat:
    """How one Studio value goes into the ledger: its unit code, its display and the factor applied to it."""

    unit: str
    display: str
    factor: float = 1.0

    def scale(self, value: float | None) -> float | None:
        """The value as the ledger stores it (shares become percents)."""
        return None if value is None else float(value) * self.factor


def unit_code(unit: str) -> str:
    """A ledger unit (UPPER_SNAKE) from a Studio unit: 'cars/day' -> 'CARS_PER_DAY', 'USD/month' -> 'USD_PER_MONTH'."""
    text = unit.strip().upper().replace("/", "_PER_").replace("%", "PERCENT")
    code = UNIT_SAFE.sub("_", text).strip("_")
    if not code or not code[0].isalpha():
        code = f"U_{code}" if code else "NONE"
    return code[:40].rstrip("_")


def is_share(unit: str) -> bool:
    """True when the unit text describes a 0-1 share or probability."""
    text = unit.strip().lower()
    return text.startswith(("share", "probability", "fraction")) or " as a share of " in f" {text} "


def format_for(unit: str, value: float | None) -> ValueFormat:
    """The ledger unit and display for a Studio value in `unit` (see the module docstring)."""
    text = unit.strip().lower()
    code = unit_code(unit)
    if is_share(unit):
        return ValueFormat("PERCENT", "pct1", 100.0)
    if text.startswith(("percentage point", "percent")):
        return ValueFormat("PERCENT", "pct1")
    if code.startswith("USD"):
        small = value is not None and abs(value) < DOLLAR_DETAIL
        return ValueFormat(code, "usd2" if small else "usd0")
    if text in ("month", "months"):
        return ValueFormat("MONTHS", "months")
    if "day" in text and "week" in text:
        return ValueFormat("DAYS_PER_WEEK", "int")
    if text in COUNT_UNITS or (value is not None and float(value).is_integer()):
        return ValueFormat(code, "int")
    if value is not None and abs(value) >= SMALL_VALUE:
        return ValueFormat(code, "int")
    return ValueFormat(code, "ratio2")
