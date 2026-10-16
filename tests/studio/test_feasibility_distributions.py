"""Triangular quantiles, provenance-carrying distributions and shock timing."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility.distributions import (
    Distribution,
    annual_to_monthly_hazard,
    event_month,
    fixed,
    from_factor_value,
    from_quantity,
    triangular,
    triangular_quantile,
    with_mode,
)
from uwcopilot.studio.profiles import load_profile
from uwcopilot.studio.profiles.models import Quantity


def test_triangular_quantile_hand_worked() -> None:
    # Symmetric triangle 0 / 1 / 2: P10 = sqrt(0.1 x 2 x 1), P90 = 2 - sqrt(0.1 x 2 x 1).
    u = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    values = triangular_quantile(u, 0.0, 1.0, 2.0)
    assert values == pytest.approx([0.0, math.sqrt(0.2), 1.0, 2.0 - math.sqrt(0.2), 2.0])


def test_quantile_matches_triangular_mean() -> None:
    item = triangular("x", "test", "unit", 2.0, 3.0, 10.0, "ASSUMPTION", "test")
    samples = item.quantile(np.random.default_rng(0).random(200_000))
    assert samples.mean() == pytest.approx(item.mean(), rel=0.005)
    assert samples.min() >= 2.0 and samples.max() <= 10.0
    assert item.percentile(0.1) < item.percentile(0.9)


def test_fixed_values_ignore_the_draw() -> None:
    item = fixed("rent", "Rent", "USD/month", 4200.0, "USER_STATED", "quote")
    assert not item.varies
    assert item.quantile(np.array([0.01, 0.99])).tolist() == [4200.0, 4200.0]
    assert triangular("x", "d", "u", 5.0, 5.0, 5.0, "ASSUMPTION", "s").kind == "fixed"


def test_shape_rules() -> None:
    with pytest.raises(ValidationError):
        Distribution(
            name="x",
            description="d",
            unit="u",
            kind="triangular",
            low=3,
            mode=1,
            high=4,
            status="ASSUMPTION",
            source="s",
        )
    with pytest.raises(ValidationError):
        Distribution(
            name="x", description="d", unit="u", kind="fixed", low=1, mode=1, high=2, status="ASSUMPTION", source="s"
        )


def test_from_quantity_keeps_the_assumption_label_and_note() -> None:
    price = load_profile("car_wash").price
    item = from_quantity("price", "Price", price, "profile car_wash price")
    assert item.status == "ASSUMPTION" and item.kind == "triangular"
    assert (item.low, item.mode, item.high) == (price.low, price.mode, price.high)
    assert "profile car_wash price (ASSUMPTION)" in item.source and price.note in item.source


def test_from_quantity_single_value_is_fixed() -> None:
    source = {"publisher": "P", "title": "T", "date": "2026", "url": "https://example.org/t", "page_or_table": "1"}
    quantity = Quantity(value=3.0, unit="u", status="SOURCED", source=source)
    item = from_quantity("x", "d", quantity, "test")
    assert item.kind == "fixed" and item.status == "SOURCED" and "https://example.org/t" in item.source


def test_from_factor_value() -> None:
    wage = FactorValue(
        factor="local_wages", value=20.0, low=15.0, high=30.0, unit="USD/hour", status="SYNTHETIC", reason="r"
    )
    item = from_factor_value("hourly_wage", "Wage", wage)
    assert (item.low, item.mode, item.high, item.status) == (15.0, 20.0, 30.0, "SYNTHETIC")
    assert "derived with" not in item.source
    derived = wage.model_copy(update={"status": "DATA", "assumptions": ["hours per week 25-40 (ASSUMPTION)"]})
    assert from_factor_value("hourly_wage", "Wage", derived).source.endswith(
        "derived with: hours per week 25-40 (ASSUMPTION)"
    )
    prime = FactorValue(factor="prime_rate", value=7.0, unit="percent", status="DATA", reason="FRED")
    assert from_factor_value("p", "d", prime).kind == "fixed"
    unknown = FactorValue(factor="local_wages", unit="USD/hour", status="UNKNOWN", reason="no QCEW wage")
    with pytest.raises(ValueError, match="UNKNOWN"):
        from_factor_value("hourly_wage", "Wage", unknown)


def test_with_mode_clamps_and_explains() -> None:
    item = triangular("p", "d", "u", 0.1, 0.2, 0.3, "ASSUMPTION", "base")
    assert with_mode(item, 0.5, "why").mode == 0.3
    assert with_mode(item, 0.15, "why").source == "base; why"


def test_monthly_hazard_compounds_to_the_annual_probability() -> None:
    hazard = annual_to_monthly_hazard(np.array([0.0, 0.2, 0.5]))
    assert 1 - (1 - hazard) ** 12 == pytest.approx([0.0, 0.2, 0.5])


def test_event_month_frequency_matches_the_annual_probability() -> None:
    u = np.random.default_rng(1).random(200_000)
    months = event_month(u, np.full(u.shape, 0.2), 36)
    assert np.mean((months >= 0) & (months < 12)) == pytest.approx(0.2, abs=0.005)
    assert np.mean(months >= 0) == pytest.approx(1 - 0.8**3, abs=0.005)


def test_event_month_edges_and_monotonicity() -> None:
    u = np.array([0.0, 0.3, 0.9])
    assert event_month(u, np.zeros(3), 36).tolist() == [-1, -1, -1]
    assert event_month(u, np.full(3, 1.0), 36).tolist() == [0, 0, 0]
    low = event_month(u, np.full(3, 0.1), 36)
    high = event_month(u, np.full(3, 0.4), 36)
    # Holding u fixed, a higher probability never moves an event later or removes it.
    for before, after in zip(low, high, strict=True):
        if before >= 0:
            assert 0 <= after <= before
    assert low[0] == 0 and high[0] == 0
