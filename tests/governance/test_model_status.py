"""The model status registry: only a model validator promotes or closes a review; monitoring only flags (SYNTHETIC)."""

import multiprocessing
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uwcopilot.governance.model_status import IN_USE, ModelEntry, ModelStatusFile, ModelStatusStore, StatusChange
from uwcopilot.security.rbac import Principal

FAMILY = "fremtpl2_frequency"
VALIDATOR = Principal("validator-1", "model_validator", "claimed")
UNDERWRITER = Principal("underwriter-1", "underwriter", "claimed")
ADMIN = Principal("admin-1", "admin", "claimed")
AT = datetime(2026, 10, 20, tzinfo=UTC)


def store_with_champion_and_challenger(tmp_path: Path) -> ModelStatusStore:
    """A registry with champion v4 and challenger v5 of one family."""
    store = ModelStatusStore(tmp_path / "model_status.json")
    store.register(ModelEntry(model_id="freq-v4", family=FAMILY, version="4", status="champion"), at=AT)
    store.register(ModelEntry(model_id="freq-v5", family=FAMILY, version="5", status="challenger"), at=AT)
    return store


def test_a_validator_promotes_a_challenger_and_the_old_champion_is_retired(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    promoted = store.promote("freq-v5", VALIDATOR, reason="better on the same window (SYNTHETIC)", at=AT)
    assert promoted.status == "champion" and store.status("freq-v4") == "retired"
    last = promoted.history[-1]
    assert (last.from_status, last.to_status, last.actor, last.role) == (
        "challenger",
        "champion",
        "validator-1",
        "model_validator",
    )


@pytest.mark.parametrize("principal", [UNDERWRITER, ADMIN])
def test_nobody_else_can_promote_and_nothing_is_written(tmp_path: Path, principal: Principal) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(PermissionError):
        store.promote("freq-v5", principal, reason="(SYNTHETIC)")
    assert store.path.read_bytes() == before


def test_monitoring_marks_under_review_and_only_a_validator_closes_it(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    flagged = store.mark_under_review("freq-v4", reason="PSI above threshold (SYNTHETIC)", alert_id="ALERT-1", at=AT)
    assert flagged.status == "under_review" and flagged.history[-1].actor == "monitor"
    assert store.mark_under_review("freq-v4", reason="again", alert_id="ALERT-2").history == flagged.history
    with pytest.raises(PermissionError):
        store.close_review("freq-v4", UNDERWRITER, reason="(SYNTHETIC)")
    assert store.close_review("freq-v4", VALIDATOR, reason="explained (SYNTHETIC)", at=AT).status == "champion"


def test_only_a_challenger_can_be_promoted_and_a_family_has_one_model_in_use(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    with pytest.raises(ValueError):
        store.promote("freq-v4", VALIDATOR, reason="(SYNTHETIC)")
    with pytest.raises(ValueError):
        store.register(ModelEntry(model_id="freq-v6", family=FAMILY, version="6", status="champion"))
    with pytest.raises(ValueError):
        store.register(ModelEntry(model_id="freq-v5", family=FAMILY, version="5", status="challenger"))
    with pytest.raises(KeyError):
        store.status("unknown")


def in_use(store: ModelStatusStore) -> list[str]:
    """The models of the family that are in use (champion or under review)."""
    return [item.model_id for item in store.load().models if item.family == FAMILY and item.status in IN_USE]


def test_an_alert_on_a_challenger_changes_nothing_and_it_can_still_be_promoted(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    before = store.path.read_bytes()
    entry = store.mark_under_review("freq-v5", reason="PSI above threshold (SYNTHETIC)", alert_id="ALERT-3", at=AT)
    assert entry.status == "challenger" and store.path.read_bytes() == before
    with pytest.raises(ValueError):
        store.close_review("freq-v5", VALIDATOR, reason="(SYNTHETIC)")
    assert in_use(store) == ["freq-v4"]
    assert store.promote("freq-v5", VALIDATOR, reason="(SYNTHETIC)", at=AT).status == "champion"
    assert in_use(store) == ["freq-v5"] and store.status("freq-v4") == "retired"


def test_closing_a_review_restores_the_earlier_status_and_never_makes_a_second_champion(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    # A registry written before this rule: monitoring marked the challenger under review.
    registry = store.load()
    challenger = registry.models[1]
    marked = StatusChange(
        at=AT, from_status="challenger", to_status="under_review", actor="monitor", reason="alert (SYNTHETIC)"
    )
    registry.models[1] = challenger.model_copy(
        update={"status": "under_review", "history": [*challenger.history, marked]}
    )
    store.save(registry)
    closed = store.close_review("freq-v5", VALIDATOR, reason="explained (SYNTHETIC)", at=AT)
    assert (closed.status, closed.history[-1].from_status) == ("challenger", "under_review")
    assert in_use(store) == ["freq-v4"]
    # A model that is under review with no earlier status (written by an older registry; register now refuses it)
    # would return to champion: refused while the family has another model in use, and nothing is written.
    legacy = store.load()
    legacy.models.append(ModelEntry(model_id="freq-v3", family=FAMILY, version="3", status="under_review"))
    store.save(legacy)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="already has a model in use"):
        store.close_review("freq-v3", VALIDATOR, reason="(SYNTHETIC)")
    assert store.path.read_bytes() == before and store.status("freq-v3") == "under_review"


def test_a_save_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    store.save(ModelStatusFile())
    assert sorted(path.name for path in tmp_path.iterdir()) == ["model_status.json", "model_status.json.lock"]


def slow_load(store: ModelStatusStore) -> ModelStatusFile:
    """load() that waits after reading, so two writers without the lock would both start from the same old copy."""
    registry = ModelStatusFile.model_validate_json(store.path.read_text(encoding="utf-8"))
    time.sleep(0.05)
    return registry


def test_two_threads_changing_the_registry_at_once_lose_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    monkeypatch.setattr(ModelStatusStore, "load", slow_load)
    challenger = ModelEntry(model_id="freq-v6", family=FAMILY, version="6", status="challenger")
    threads = [
        threading.Thread(target=store.mark_under_review, args=("freq-v4", "PSI above threshold (SYNTHETIC)", "A-9")),
        threading.Thread(target=store.register, args=(challenger,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    monkeypatch.undo()
    flagged = store.entry("freq-v4")
    assert flagged.status == "under_review" and flagged.history[-1].alert_id == "A-9"
    assert store.status("freq-v6") == "challenger"


def register_many(path: Path, worker: int, count: int) -> None:
    """Register count challengers from one forked process, each load waiting a little (slow_load)."""
    ModelStatusStore.load = slow_load  # type: ignore[method-assign]  # changes only this forked process
    store = ModelStatusStore(path)
    for number in range(count):
        store.register(ModelEntry(model_id=f"w{worker}-{number}", family=FAMILY, version="1", status="challenger"))


def test_two_processes_registering_at_once_lose_no_entry(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    context = multiprocessing.get_context("fork")  # POSIX only, like the fcntl lock itself
    workers = [context.Process(target=register_many, args=(store.path, worker, 4)) for worker in range(2)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(timeout=60)
    assert [process.exitcode for process in workers] == [0, 0]
    assert len(store.load().models) == 2 + 2 * 4
    assert not list(tmp_path.glob("*.tmp"))


def test_a_model_registered_as_under_review_cannot_join_a_family_that_has_one_in_use(tmp_path: Path) -> None:
    store = store_with_champion_and_challenger(tmp_path)
    with pytest.raises(ValueError, match="already has a model in use"):
        store.register(ModelEntry(model_id="freq-v7", family=FAMILY, version="7", status="under_review"))
