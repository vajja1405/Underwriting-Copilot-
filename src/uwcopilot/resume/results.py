"""Read results/*.json for the resume lines: load a file, walk its keys, read a value with its 95% interval.

Nothing here computes a metric. A value that is missing, None, or a "not measured ..." text comes back as None, and
the bullet that needed it drops the clause and says why.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The keys a results file uses for a point value, in the order they are tried.
VALUE_KEYS: tuple[str, ...] = ("value", "estimate", "rate", "mean_difference")
# Pairs of keys that hold a percentile-bootstrap interval.
BOOTSTRAP_KEY_PAIRS: tuple[tuple[str, str], ...] = (("ci_low", "ci_high"), ("ci95_low", "ci95_high"))
# Keys that hold an interval as a [low, high] list, and the method each one names.
INTERVAL_LIST_KEYS: tuple[tuple[str, str], ...] = (("wilson_ci95", "wilson"), ("ci95", "bootstrap"))
# Keys that hold the number of units behind a rate.
COUNT_KEYS: tuple[str, ...] = ("n", "n_packets")


@dataclass(frozen=True)
class Estimate:
    """A measured value and the 95% interval written next to it in a results file."""

    value: float
    low: float
    high: float
    method: str  # "bootstrap" or "wilson"
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


def read_estimate(node: Any) -> Estimate | None:
    """An Estimate from one results-file entry, or None when the entry has no value or no interval.

    Understood shapes: {value|estimate|rate|mean_difference, ci_low, ci_high}, {value, ci95_low, ci95_high},
    {rate, wilson_ci95: [low, high]} and {..., ci95: [low, high]}.
    """
    if not isinstance(node, dict):
        return None
    value = next((node[key] for key in VALUE_KEYS if is_number(node.get(key))), None)
    if value is None:
        return None
    count = next((int(node[key]) for key in COUNT_KEYS if is_number(node.get(key))), None)
    for low_key, high_key in BOOTSTRAP_KEY_PAIRS:
        low, high = node.get(low_key), node.get(high_key)
        if is_number(low) and is_number(high):
            return Estimate(float(value), float(low), float(high), "bootstrap", count)
    for key, method in INTERVAL_LIST_KEYS:
        bounds = node.get(key)
        if isinstance(bounds, list | tuple) and len(bounds) == 2 and all(is_number(bound) for bound in bounds):
            return Estimate(float(value), float(bounds[0]), float(bounds[1]), method, count)
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
