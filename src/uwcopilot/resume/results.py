"""Read results/*.json for the resume lines: load a file, walk its keys, read a value with its 95% interval.

Nothing here computes a metric. A value that is missing, None, or a "not measured ..." text comes back as None, and
the bullet that needed it drops the clause and says why.

Which interval is quoted follows the README headline table (governance/readme_headlines.py interval_text): the
percentile bootstrap interval, unless it has zero width or the file flags it (bootstrap_degenerate: true or a
bootstrap_interval_note); then the exact Clopper-Pearson interval (exact_ci_low/exact_ci_high), else the Wilson
interval (wilson_ci95). With neither, the Estimate is marked NARROW_BOOTSTRAP and text.interval prints the value
with a note instead of a zero-width interval that would look like certainty.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The keys a results file uses for a point value, in the order they are tried.
VALUE_KEYS: tuple[str, ...] = ("value", "estimate", "rate", "mean_difference")
# Pairs of keys that hold a percentile-bootstrap interval.
BOOTSTRAP_KEY_PAIRS: tuple[tuple[str, str], ...] = (("ci_low", "ci_high"), ("ci95_low", "ci95_high"))
# The key that holds a percentile-bootstrap interval as a [low, high] list.
BOOTSTRAP_LIST_KEY = "ci95"
# The keys of the exact (Clopper-Pearson) interval, and of the Wilson interval as a [low, high] list.
EXACT_KEY_PAIR: tuple[str, str] = ("exact_ci_low", "exact_ci_high")
WILSON_LIST_KEY = "wilson_ci95"
# Keys that hold the number of units behind a rate.
COUNT_KEYS: tuple[str, ...] = ("n", "n_packets")

# The interval methods an Estimate can carry.
BOOTSTRAP = "bootstrap"
EXACT = "exact"
WILSON = "wilson"
# A bootstrap interval with zero width, or one the file flags as too narrow, and no exact or Wilson interval to use
# instead. low and high keep the bootstrap bounds as written, but text.interval does not print them as an interval.
NARROW_BOOTSTRAP = "narrow-bootstrap"


@dataclass(frozen=True)
class Estimate:
    """A measured value and the 95% interval written next to it in a results file."""

    value: float
    low: float
    high: float
    method: str  # BOOTSTRAP, EXACT, WILSON or NARROW_BOOTSTRAP
    n: int | None = None


class ResultsFolder:
    """The JSON files of one results folder, each loaded on first use."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._loaded: dict[str, dict[str, Any] | None] = {}

    def load(self, name: str) -> dict[str, Any] | None:
        """The parsed file <folder>/<name>, or None when it does not exist."""
        if name not in self._loaded:
            path = self.folder / name
            self._loaded[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return self._loaded[name]


def is_number(value: Any) -> bool:
    """True for an int or float (a bool is not a number here)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def dig(data: Any, *keys: str) -> Any:
    """data[key1][key2]...; None when a key is missing or a step is not a mapping."""
    node = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def number_at(data: Any, *keys: str) -> float | None:
    """The number at data[key1][key2]..., or None when it is missing or not a number."""
    value = dig(data, *keys)
    return value if is_number(value) else None


def number_pair(low: Any, high: Any) -> tuple[float, float] | None:
    """(low, high) as floats when both are numbers, else None."""
    if is_number(low) and is_number(high):
        return float(low), float(high)
    return None


def list_pair(bounds: Any) -> tuple[float, float] | None:
    """(low, high) from a two-item [low, high] list, else None."""
    if isinstance(bounds, list | tuple) and len(bounds) == 2:
        return number_pair(bounds[0], bounds[1])
    return None


def bootstrap_bounds(node: dict[str, Any]) -> tuple[float, float] | None:
    """The percentile-bootstrap interval of an entry (ci_low/ci_high, ci95_low/ci95_high or a ci95 list), or None."""
    for low_key, high_key in BOOTSTRAP_KEY_PAIRS:
        bounds = number_pair(node.get(low_key), node.get(high_key))
        if bounds is not None:
            return bounds
    return list_pair(node.get(BOOTSTRAP_LIST_KEY))


def bootstrap_too_narrow(node: dict[str, Any], bounds: tuple[float, float]) -> bool:
    """True when the bootstrap interval has zero width, or the file says it is degenerate or too narrow.

    The same rule as the README headline table (governance/readme_headlines.py bootstrap_too_narrow).
    """
    return (
        bounds[0] == bounds[1] or node.get("bootstrap_degenerate") is True or bool(node.get("bootstrap_interval_note"))
    )


def read_estimate(node: Any) -> Estimate | None:
    """An Estimate from one results-file entry, or None when the entry has no value or no interval.

    Understood shapes: {value|estimate|rate|mean_difference, ci_low, ci_high}, {value, ci95_low, ci95_high},
    {..., ci95: [low, high]} (percentile bootstrap), {..., exact_ci_low, exact_ci_high} (Clopper-Pearson) and
    {rate, wilson_ci95: [low, high]}.

    The interval, in order: the bootstrap interval unless bootstrap_too_narrow(); the exact interval; the Wilson
    interval; and last the narrow bootstrap interval, marked NARROW_BOOTSTRAP so that it is not printed as if it
    were informative.
    """
    if not isinstance(node, dict):
        return None
    value = next((node[key] for key in VALUE_KEYS if is_number(node.get(key))), None)
    if value is None:
        return None
    count = next((int(node[key]) for key in COUNT_KEYS if is_number(node.get(key))), None)
    bootstrap = bootstrap_bounds(node)
    if bootstrap is not None and not bootstrap_too_narrow(node, bootstrap):
        return Estimate(float(value), bootstrap[0], bootstrap[1], BOOTSTRAP, count)
    exact = number_pair(node.get(EXACT_KEY_PAIR[0]), node.get(EXACT_KEY_PAIR[1]))
    if exact is not None:
        return Estimate(float(value), exact[0], exact[1], EXACT, count)
    wilson = list_pair(node.get(WILSON_LIST_KEY))
    if wilson is not None:
        return Estimate(float(value), wilson[0], wilson[1], WILSON, count)
    if bootstrap is not None:
        return Estimate(float(value), bootstrap[0], bootstrap[1], NARROW_BOOTSTRAP, count)
    return None


def estimate_at(data: Any, *keys: str) -> Estimate | None:
    """read_estimate() of the entry at data[key1][key2]..."""
    return read_estimate(dig(data, *keys))


def missing_reason(data: dict[str, Any] | None, file_name: str, *keys: str) -> str:
    """Why a value is not quoted: the file is absent, the file says "not measured ...", or the key is absent."""
    if data is None:
        return f"not measured: results/{file_name} does not exist"
    value = dig(data, *keys)
    if isinstance(value, str):
        return f"results/{file_name} says: {value}"
    if isinstance(value, dict) and isinstance(value.get("status"), str):
        reason = value.get("reason")
        return f"results/{file_name} says: {value['status']}" + (f" ({reason})" if isinstance(reason, str) else "")
    return f"not measured: results/{file_name} has no usable {'.'.join(keys)}"
