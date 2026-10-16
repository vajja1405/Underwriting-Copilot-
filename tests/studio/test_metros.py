"""Metro configs: the five real metros are filled from the Census delineation file, the SYNTHETIC one is generated."""

import json
from datetime import date

import pytest
import yaml
from pydantic import ValidationError
from studio_helpers import fixture_body

from uwcopilot.studio.metros import MetroConfig, list_metros, load_metro, metro_mode
from uwcopilot.studio.metros.loader import METROS_DIR, load_all_metros

REAL = {"new_york_ny_nj_pa", "los_angeles_ca", "chicago_il_in", "dallas_fort_worth_tx", "houston_tx"}


def raw_metro(metro_id: str) -> dict:
    """The YAML mapping of a metro config."""
    return yaml.safe_load((METROS_DIR / f"{metro_id}.yaml").read_text(encoding="utf-8"))


def test_all_configs_load() -> None:
    assert set(list_metros()) == REAL | {"synthetic_metro"}
    assert all(metro.id == metro_id for metro_id, metro in load_all_metros().items())


def test_real_metros_are_verified_from_the_delineation_file() -> None:
    for metro_id in REAL:
        metro = load_metro(metro_id)
        assert metro.verified and metro.data_label == "PUBLIC"
        assert metro.delineation is not None
        assert metro.delineation.source.url.startswith("https://www2.census.gov/")
        assert len(metro.delineation.file_sha256) == 64
        assert metro.counties and metro.cbsa_code


def test_county_fips_match_the_recorded_delineation_rows() -> None:
    """The committed configs equal the rows of the recorded delineation file excerpt (no FIPS typed by hand)."""
    rows = json.loads(fixture_body("delineation/list1_2023_rows.json"))
    header_index = next(index for index, row in enumerate(rows) if "CBSA Code" in row)
    header = rows[header_index]
    column = {name: header.index(name) for name in header}
    by_cbsa: dict[str, set[str]] = {}
    for row in rows[header_index + 1 :]:
        if row and row[0].isdigit():
            geoid = row[column["FIPS State Code"]].zfill(2) + row[column["FIPS County Code"]].zfill(3)
            by_cbsa.setdefault(row[0], set()).add(geoid)
    for metro_id in REAL:
        metro = load_metro(metro_id)
        assert set(metro.county_geoids()) == by_cbsa[metro.cbsa_code], metro_id


def test_synthetic_metro_has_no_real_geography() -> None:
    metro = load_metro("synthetic_metro")
    assert metro.data_label == "SYNTHETIC"
    assert not metro.states and not metro.counties and metro.synthetic is not None
    assert metro_mode(metro, None)[0] == "synthetic"


def test_counties_without_a_delineation_record_are_refused() -> None:
    data = raw_metro("houston_tx")
    data["delineation"] = None
    with pytest.raises(ValidationError, match="delineation"):
        MetroConfig.model_validate(data)


def test_verified_needs_counties() -> None:
    data = raw_metro("houston_tx")
    data["counties"] = []
    with pytest.raises(ValidationError, match="verified: true needs counties"):
        MetroConfig.model_validate(data)


def test_counties_must_span_exactly_the_listed_states() -> None:
    data = raw_metro("chicago_il_in")
    data["states"] = ["IL"]
    with pytest.raises(ValidationError, match="span"):
        MetroConfig.model_validate(data)


def test_enabled_state_adapter_outside_the_metro_is_refused() -> None:
    data = raw_metro("new_york_ny_nj_pa")
    for item in data["enhancements"]:
        if item["adapter"] == "PA":
            item["enabled"] = True
    with pytest.raises(ValidationError, match="'PA' is enabled"):
        MetroConfig.model_validate(data)


def test_duplicate_county_is_refused() -> None:
    data = raw_metro("los_angeles_ca")
    data["counties"].append(data["counties"][0])
    with pytest.raises(ValidationError, match="listed twice"):
        MetroConfig.model_validate(data)


def test_fips_codes_stay_strings_in_yaml() -> None:
    for metro_id in REAL:
        for county in raw_metro(metro_id)["counties"]:
            assert isinstance(county["county_fips"], str) and len(county["county_fips"]) == 3


def test_metro_modes() -> None:
    metro = load_metro("houston_tx")
    assert metro_mode(metro, None) == ("limited", "no data snapshot")
    snapshot = date(2026, 10, 12)
    assert metro_mode(metro, None, snapshot_date=snapshot)[0] == "limited"
    assert metro_mode(metro, 0.5, snapshot_date=snapshot)[0] == "limited"
    assert metro_mode(metro, 0.9, snapshot_date=snapshot)[0] == "full"


def test_unverified_metro_runs_limited_even_with_good_coverage() -> None:
    data = raw_metro("houston_tx")
    data.update({"verified": False, "counties": [], "delineation": None})
    metro = MetroConfig.model_validate(data)
    assert metro_mode(metro, 0.99, snapshot_date=date(2026, 10, 12))[0] == "limited"


def test_enabled_enhancements_by_kind() -> None:
    metro = load_metro("new_york_ny_nj_pa")
    adapters = [item.adapter for item in metro.enabled_enhancements("traffic_state")]
    assert adapters == ["NY", "NJ"]
