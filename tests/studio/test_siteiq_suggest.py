"""suggest_areas: ranked tracts consistent with score_site, exclusions counted, GeoJSON layer, budget check."""

import numpy as np
import pytest

from uwcopilot.studio.data.fabric import SnapshotFabric
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.siteiq import Budget, MetroContext, score_site, suggest_areas
from uwcopilot.studio.siteiq.reference import reference_for
from uwcopilot.studio.siteiq.suggest import tract_overall_scores
from uwcopilot.studio.siteiq.trade_area import scaled_rings
from uwcopilot.studio.siteiq.validation import synthetic_context

PROFILES = load_all_profiles()


@pytest.fixture(scope="module")
def ctx():
    context, _ = synthetic_context(load_metro("synthetic_metro"), 11, PROFILES, n_tracts=60)
    return context


@pytest.mark.parametrize("profile_id", sorted(PROFILES))
def test_top_areas_are_ranked_and_counted(ctx, profile_id) -> None:
    result = suggest_areas(profile_id, "synthetic_metro", context=ctx)
    scores = [area.overall_score for area in result.areas]
    assert len(result.areas) == min(5, result.n_ranked) and scores == sorted(scores, reverse=True)
    assert [area.rank for area in result.areas] == list(range(1, len(result.areas) + 1))
    assert result.n_tracts == len(ctx.tracts)
    assert sum(item.count for item in result.excluded) == result.n_tracts - result.n_ranked
    for area in result.areas:
        assert area.reasons and len(area.reasons) <= 3 and area.geoid.startswith("SYN")
    assert result.data_label == "SYNTHETIC" and any("SYNTHETIC" in note for note in result.notes)


def test_suggested_score_equals_score_site_at_the_centroid(ctx) -> None:
    result = suggest_areas("laundromat", "synthetic_metro", context=ctx)
    best = result.areas[0]
    site = score_site("laundromat", "synthetic_metro", best.lat, best.lon, context=ctx)
    assert site.tract_geoid == best.geoid and site.overall_score == pytest.approx(best.overall_score, abs=0.1)


def test_excluded_tracts_are_those_missing_required_data(ctx) -> None:
    profile = PROFILES["restaurant_cafe"]
    reference = reference_for(ctx, profile, scaled_rings(profile, ctx.metro))
    overall, reasons = tract_overall_scores(profile, reference)
    for index, reason in enumerate(reasons):
        assert (reason is None) == (not np.isnan(overall[index]))
        if reason is not None and reason.startswith("required data UNKNOWN: "):
            factor = reason.removeprefix("required data UNKNOWN: ")
            assert np.isnan(reference.values[factor][index])


def test_geojson_layer(ctx) -> None:
    result = suggest_areas("car_wash", "synthetic_metro", top_n=3, context=ctx)
    layer = result.geojson
    assert layer["type"] == "FeatureCollection" and len(layer["features"]) == 2 * len(result.areas)
    points = [item for item in layer["features"] if item["geometry"]["type"] == "Point"]
    rings = [item for item in layer["features"] if item["geometry"]["type"] == "Polygon"]
    assert [item["properties"]["rank"] for item in points] == [1, 2, 3]
    for item in rings:
        outer = item["geometry"]["coordinates"][0]
        assert outer[0] == outer[-1] and "not a tract boundary" in item["properties"]["note"]
    assert all(item["properties"]["data_label"] == "SYNTHETIC" for item in layer["features"])


def test_budget_is_checked_but_never_filters(ctx) -> None:
    budget = Budget(max_monthly_rent_usd=1.0, max_build_out_usd=1e12)
    result = suggest_areas("motel", "synthetic_metro", budget, context=ctx)
    assert result.budget.filters_tracts is False
    text = " ".join(result.budget.notes)
    assert "below the metro rent range" in text and "above the sum of the profile's capex ranges" in text
    assert suggest_areas("motel", "synthetic_metro", context=ctx).budget.notes[0] == "No budget given."


def test_real_metro_without_snapshot_suggests_nothing() -> None:
    metro = load_metro("houston_tx")
    context = MetroContext(metro=metro, fabric=SnapshotFabric(metro=metro, snapshot=None))
    result = suggest_areas("car_wash", "houston_tx", context=context)
    assert result.areas == [] and result.n_tracts == 0 and result.mode == "limited"
    assert any("No tracts" in note for note in result.notes) and result.geojson["features"] == []
    site = score_site("car_wash", "houston_tx", 29.76, -95.37, context=context)
    assert site.overall_score is None and site.coverage_score == 0.0
    assert all(item.status in ("UNKNOWN", "ASSUMPTION") for item in site.factors)
    assert any(note.startswith("No overall score") for note in site.notes)
