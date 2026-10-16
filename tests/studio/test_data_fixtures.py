"""Every Studio fixture says where it came from, its checksum matches, and it holds no secret."""

import hashlib
import json

import pytest
from studio_helpers import FIXTURES

RECORDED = "recorded from the live API on "
EXCERPT = "excerpt of a response recorded from the live API on "
HAND_WRITTEN = "hand-written from documentation, NOT recorded from the live API"
SECRET_WORDS = ("api_key", "registrationkey", "Bearer ", '"key"', "key=")

FIXTURE_FILES = sorted(path for path in FIXTURES.rglob("*.json"))


def test_there_are_recorded_and_hand_written_fixtures() -> None:
    origins = [json.loads(path.read_text(encoding="utf-8"))["fixture_origin"] for path in FIXTURE_FILES]
    assert any(origin.startswith(RECORDED) for origin in origins)
    assert any(origin.startswith(HAND_WRITTEN) for origin in origins)


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_fixture_is_labeled_and_checksummed(path) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    origin = record["fixture_origin"]
    assert origin.startswith((RECORDED, EXCERPT, HAND_WRITTEN)), origin
    assert record["url"].startswith("https://")
    assert hashlib.sha256(record["body"].encode("utf-8")).hexdigest() == record["body_sha256"]
    if origin.startswith(HAND_WRITTEN):
        assert "SYNTHETIC" in origin or "SYNTHETIC" in record.get("note", "")


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_fixture_holds_no_secret(path) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    request = record["url"] + json.dumps(record.get("params", {}))
    for word in SECRET_WORDS:
        assert word not in request, (path.name, word)
