"""NAICS codes in the profiles: verified only where the published BLS list has the code with the same title."""

import pytest
from layer_helpers import fixture_record

from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.profiles.models import NaicsCode
from uwcopilot.studio.profiles.naics_check import INDUSTRY_TITLES_URL, check_code, parse_industry_titles

RECORD = fixture_record("bls/industry_titles_excerpt.json")
LISTED = parse_industry_titles(RECORD["body"])


def test_the_excerpt_is_a_recorded_excerpt_of_the_list() -> None:
    assert RECORD["url"] == INDUSTRY_TITLES_URL and RECORD["fixture_origin"].startswith("excerpt of a response")
    assert LISTED["811192"].title == "NAICS 811192 Car washes"


def test_each_title_form_gets_its_own_status() -> None:
    assert check_code("p", "811192", "Car washes", LISTED).status == "confirmed"
    assert check_code("p", "811192", "car  WASHES", LISTED).status == "confirmed"
    assert check_code("p", "811192", "Car wash", LISTED).status == "title_differs"
    assert check_code("p", "333244", "Printing machinery and equipment mfg.", LISTED).status == "older_vintage_only"
    assert check_code("p", "10", "Total, all industries", LISTED).status == "not_a_naics_title"
    assert check_code("p", "44", "Retail trade", LISTED).status == "not_in_list"


def test_verified_codes_match_the_recorded_list_and_unverified_ones_do_not() -> None:
    for profile in load_all_profiles().values():
        for item in profile.naics:
            check = check_code(profile.id, item.code, item.title, LISTED)
            if item.verified:
                assert check.confirmed, (profile.id, item.code, check.detail)
                assert item.verification.sha256 == RECORD["full_body_sha256"]
                assert item.verification.published_title == LISTED[item.code].title
                assert item.verification.url == INDUSTRY_TITLES_URL
            else:
                assert not check.confirmed, (profile.id, item.code)


def test_verified_needs_a_record_and_a_record_needs_verified() -> None:
    record = {
        "publisher": "BLS",
        "list_name": "titles",
        "url": INDUSTRY_TITLES_URL,
        "retrieved_at_utc": "2026-10-13T14:31:09+00:00",
        "sha256": "a" * 64,
        "published_title": "NAICS 811192 Car washes",
    }
    NaicsCode(code="811192", title="Car washes", verified=True, verification=record)
    with pytest.raises(ValueError, match="no verification record"):
        NaicsCode(code="811192", title="Car washes", verified=True)
    with pytest.raises(ValueError, match="verified is false"):
        NaicsCode(code="811192", title="Car washes", verified=False, verification=record)


def test_missing_columns_are_refused() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        parse_industry_titles("code,title\n1,x\n")
