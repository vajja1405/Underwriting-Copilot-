"""scripts/promote_model.py: only a model_validator promotes a challenger; every other role is refused (SYNTHETIC)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

import promote_model
from uwcopilot.governance.model_status import ModelEntry, ModelStatusStore

FAMILY = "synthetic_lc_credit_pd"
CHAMPION = "SYNTHETIC-lc-pd-lightgbm-to2015-aaaaaaaaaa"
CHALLENGER = "SYNTHETIC-lc-pd-lightgbm-to2016-bbbbbbbbbb"
AT = datetime(2026, 10, 26, tzinfo=UTC)


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """A registry with one SYNTHETIC champion and one SYNTHETIC challenger."""
    store = ModelStatusStore(tmp_path / "model_status.json")
    store.register(ModelEntry(model_id=CHAMPION, family=FAMILY, version="a", status="champion"), at=AT)
    store.register(ModelEntry(model_id=CHALLENGER, family=FAMILY, version="b", status="challenger"), at=AT)
    return store.path


def promote(registry: Path, model_id: str, role: str, principal: str = "person-1") -> int:
    """Run the script's main() as the command line would."""
    argv = [model_id, "--role", role, "--principal", principal, "--reason", "SYNTHETIC test", "--registry"]
    return promote_model.main([*argv, str(registry)])


@pytest.mark.parametrize("role", ["underwriter", "senior_underwriter", "admin", "system", "unknown"])
def test_every_role_but_model_validator_is_refused_and_nothing_changes(
    registry: Path, role: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = registry.read_bytes()
    assert promote(registry, CHALLENGER, role) == promote_model.REFUSED
    assert registry.read_bytes() == before
    output = capsys.readouterr().out
    assert "Refused: promote_model refused" in output and "Nothing was changed" in output
    assert f"{CHALLENGER} (family {FAMILY}" in output  # the history is printed


def test_a_model_validator_promotes_and_the_history_is_printed(
    registry: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert promote(registry, CHALLENGER, "model_validator", "validator.1") == promote_model.PROMOTED
    store = ModelStatusStore(registry)
    assert (store.status(CHALLENGER), store.status(CHAMPION)) == ("champion", "retired")
    last = store.entry(CHALLENGER).history[-1]
    assert (last.actor, last.role, last.reason) == ("validator.1", "model_validator", "SYNTHETIC test")
    output = capsys.readouterr().out
    assert "challenger -> champion  by validator.1 (model_validator): SYNTHETIC test" in output
    assert f"{CHAMPION}: retired (last change by validator.1: replaced by {CHALLENGER})" in output


@pytest.mark.parametrize(
    ("model_id", "role", "message"),
    [
        (CHAMPION, "model_validator", "only a challenger can be promoted"),
        ("SYNTHETIC-not-registered", "model_validator", "is not registered"),
        (CHALLENGER, "chief_risk_officer", "unknown role"),
    ],
)
def test_bad_requests_change_nothing(
    registry: Path, model_id: str, role: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = registry.read_bytes()
    assert promote(registry, model_id, role) == promote_model.BAD_REQUEST
    assert registry.read_bytes() == before
    assert message in capsys.readouterr().out


def test_a_bad_principal_id_is_a_bad_request(registry: Path) -> None:
    assert promote(registry, CHALLENGER, "model_validator", "no spaces allowed") == promote_model.BAD_REQUEST
    assert ModelStatusStore(registry).status(CHALLENGER) == "challenger"
