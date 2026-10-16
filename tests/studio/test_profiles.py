"""Business-type profiles: every YAML loads, every number is labeled, and the provenance rules are enforced."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from uwcopilot.studio.profiles import Profile, Quantity, iter_numeric_fields, list_profiles, load_profile
from uwcopilot.studio.profiles.factors import DEMAND_FORMULAS, LOCATION_FACTORS
from uwcopilot.studio.profiles.loader import PROFILES_DIR, apply_metro_overrides, load_all_profiles

V1_TYPES = {"restaurant_cafe", "car_wash", "motel", "short_term_rental", "laundromat", "retail_storefront"}
SOURCE = {"publisher": "P", "title": "T", "date": "2026", "url": "https://example.org/t", "page_or_table": "Table 1"}


def raw_profile(profile_id: str = "car_wash") -> dict:
    """The YAML mapping of a profile, for building broken variants."""
    return yaml.safe_load((PROFILES_DIR / f"{profile_id}.yaml").read_text(encoding="utf-8"))


def test_the_six_v1_business_types_load() -> None:
    assert set(list_profiles()) == V1_TYPES
    profiles = load_all_profiles()
    assert all(profile.id == profile_id for profile_id, profile in profiles.items())


def test_no_field_is_sourced_until_a_source_has_been_opened() -> None:
    """Every number is the author's labeled ASSUMPTION: no source could be opened and cited for a profile yet.

    When someone opens a primary source and cites it, update this test in the same commit (it guards against
    flipping a label to SOURCED without a citation).
    """
    for profile in load_all_profiles().values():
        fields = list(iter_numeric_fields(profile))
        assert fields, profile.id
        sourced = [path for path, item in fields if item.status == "SOURCED"]
        assert sourced == [], f"{profile.id} has SOURCED fields {sourced}"


def test_every_assumption_is_a_range_with_a_note() -> None:
    for profile in load_all_profiles().values():
        for path, item in iter_numeric_fields(profile):
            assert item.note, f"{profile.id}.{path} has no note"
            if isinstance(item, Quantity):
                low, high = item.bounds()
                assert low < high, f"{profile.id}.{path} is not a range"


def test_sourced_field_without_source_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SOURCED field must carry a source"):
        Quantity(value=3.0, unit="x", status="SOURCED")
    assert Quantity(value=3.0, unit="x", status="SOURCED", source=SOURCE).point() == 3.0


def test_assumption_must_be_a_range_with_a_note() -> None:
    with pytest.raises(ValidationError, match="never a single number"):
        Quantity(value=3.0, unit="x", status="ASSUMPTION", note="n")
    with pytest.raises(ValidationError, match="must carry a note"):
        Quantity(low=1, mode=2, high=3, unit="x", status="ASSUMPTION")
    with pytest.raises(ValidationError, match="low <= mode <= high"):
        Quantity(low=3, mode=2, high=4, unit="x", status="ASSUMPTION", note="n")


def test_source_url_must_be_http() -> None:
    with pytest.raises(ValidationError, match="http"):
        Quantity(value=1.0, unit="x", status="SOURCED", source={**SOURCE, "url": "file:///etc/passwd"})


def test_unknown_keys_in_yaml_are_errors() -> None:
    data = raw_profile()
    data["typo_field"] = 1
    with pytest.raises(ValidationError, match="typo_field"):
        Profile.model_validate(data)


def test_weights_must_sum_to_one_and_name_known_factors() -> None:
    data = raw_profile()
    data["design"]["location_weights"]["traffic_aadt"]["weight"] = 0.5
    with pytest.raises(ValidationError, match="sum to 1"):
        Profile.model_validate(data)
    data = raw_profile()
    data["design"]["location_weights"]["sunshine"] = data["design"]["location_weights"].pop("anchors")
    with pytest.raises(ValidationError, match="unknown location factors"):
        Profile.model_validate(data)


def test_seasonality_needs_twelve_values_averaging_one() -> None:
    data = raw_profile()
    data["seasonality"]["values"] = [1.0] * 11
    with pytest.raises(ValidationError, match="12 monthly values"):
        Profile.model_validate(data)
    data["seasonality"]["values"] = [1.2] * 12
    with pytest.raises(ValidationError, match="average 1.0"):
        Profile.model_validate(data)


def test_demand_inputs_must_match_the_formula() -> None:
    data = raw_profile()
    data["demand"]["inputs"]["extra_rate"] = copy.deepcopy(data["demand"]["inputs"]["capture_rate"])
    with pytest.raises(ValidationError, match="unexpected"):
        Profile.model_validate(data)


def test_demand_unit_matches_formula_and_factors_exist() -> None:
    for profile in load_all_profiles().values():
        formula = DEMAND_FORMULAS[profile.demand.formula]
        assert profile.demand_unit == formula.demand_unit
        assert set(formula.data_factors) <= set(LOCATION_FACTORS)


def test_osm_tags_cannot_inject_query_text() -> None:
    data = raw_profile()
    data["competitor_tags"] = ['amenity=car_wash"];out;']
    with pytest.raises(ValidationError, match="invalid OpenStreetMap tag"):
        Profile.model_validate(data)


def test_cost_ratios_are_shares_of_revenue() -> None:
    data = raw_profile()
    data["cost_ratios"]["utilities"]["high"] = 1.5
    with pytest.raises(ValidationError, match="between 0 and 1"):
        Profile.model_validate(data)


def test_metro_override_replaces_one_field_with_its_own_provenance(tmp_path) -> None:
    data = raw_profile()
    data["metro_overrides"] = {
        "houston_tx": {
            "labor.hourly_wage": {"value": 15.5, "unit": "USD/hour", "status": "SOURCED", "source": SOURCE},
        }
    }
    (tmp_path / "car_wash.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    base = load_profile("car_wash", profiles_dir=tmp_path)
    houston = load_profile("car_wash", metro_id="houston_tx", profiles_dir=tmp_path)
    assert base.labor.hourly_wage.status == "ASSUMPTION"
    assert houston.labor.hourly_wage.point() == 15.5
    assert houston.labor.hourly_wage.source.publisher == "P"
    assert load_profile("car_wash", metro_id="chicago_il_in", profiles_dir=tmp_path) == base


def test_override_of_an_unknown_path_fails() -> None:
    profile = load_profile("car_wash")
    broken = profile.model_copy(
        update={
            "metro_overrides": {"x": {"labor.no_such": Quantity(value=1.0, unit="u", status="SOURCED", source=SOURCE)}}
        }
    )
    with pytest.raises(KeyError, match="no field"):
        apply_metro_overrides(broken, "x")


def test_profile_id_must_match_file_name(tmp_path) -> None:
    data = raw_profile()
    (tmp_path / "other_name.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="must match the file name"):
        load_profile("other_name", profiles_dir=tmp_path)


def test_unknown_profile_lists_known_ones() -> None:
    with pytest.raises(LookupError, match="car_wash"):
        load_profile("bowling_alley")
