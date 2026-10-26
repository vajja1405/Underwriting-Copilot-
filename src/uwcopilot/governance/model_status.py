"""The status of each model: which one is in use, which is a candidate, which is under review (Phase 6 B and C).

    store = ModelStatusStore(Path("data/governance/model_status.json"))
    store.register(ModelEntry(model_id="freq-lgbm-v5", family="fremtpl2_frequency", version="5", status="challenger"))
    store.mark_under_review("freq-lgbm-v4", reason="PSI of DrivAge above 0.25", alert_id="ALERT-0007")   # monitoring
    store.promote("freq-lgbm-v5", principal, reason="challenger better on the same window")         # a person only
    store.close_review("freq-lgbm-v4", principal, reason="drift explained by a new product line")

Rules:
- Statuses: champion (in use), challenger (a candidate, never used for decisions), under_review (a monitoring alert
  fired; it stays in use only until a person looks at it) and retired.
- Nothing changes a status silently. Every change is appended to the entry's history with who made it, when and why.
- A family has at most one model in use (champion or under_review). register refuses a second one, and only promote
  replaces it.
- Monitoring may only mark the champion under review. An alert on a challenger changes nothing here, because a
  challenger is not used for decisions (the alert stays in the monitoring alert log). Promoting a challenger and
  closing a review are decisions, so they need a principal whose role may take the action "promote_model" in
  security/rbac.py (model_validator only); anyone else gets PermissionError and nothing is written.
- Promoting a challenger makes it the champion of its family and retires the previous champion of that family.
- Closing a review gives the model back the status it had before it was marked (champion). It refuses to make a model
  champion while another model of the family is in use.
- Every change (register, mark_under_review, promote, close_review) loads, changes and saves the file while holding an
  exclusive lock on <registry>.lock (fcntl, POSIX), so two scripts that change the registry at the same moment never
  erase each other's change. risk/results.py locks the results files the same way.
- The file is written to a temporary file with a name of its own and then renamed, so a crash never leaves half a
  file and two writers never share a temporary file.

The file holds model ids, versions, statuses and reasons, never data rows or personal data.
"""

import fcntl
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.security.rbac import Principal, authorize

ModelStatus = Literal["champion", "challenger", "under_review", "retired"]
IN_USE: tuple[ModelStatus, ...] = ("champion", "under_review")
MONITOR_ACTOR = "monitor"
PROMOTE_ACTION = "promote_model"


class StatusChange(BaseModel):
    """One change of a model's status."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    from_status: ModelStatus | None
    to_status: ModelStatus
    actor: str = Field(description="A principal id, or 'monitor' for an automatic alert")
    role: str | None = None
    reason: str = Field(min_length=1)
    alert_id: str | None = None


class ModelEntry(BaseModel):
    """One registered model version and its history."""

    model_id: str = Field(min_length=1)
    family: str = Field(min_length=1, description="Models that can replace each other, e.g. fremtpl2_frequency")
    version: str = Field(min_length=1)
    status: ModelStatus
    data_label: str = "SYNTHETIC"
    history: list[StatusChange] = Field(default_factory=list)


class ModelStatusFile(BaseModel):
    """The whole registry file."""

    schema_version: Literal["model-status-v1"] = "model-status-v1"
    models: list[ModelEntry] = Field(default_factory=list)


def utc_now() -> datetime:
    """The current time in UTC, without microseconds."""
    return datetime.now(UTC).replace(microsecond=0)


def status_before_review(entry: ModelEntry) -> ModelStatus:
    """The status a model had before its latest under_review mark; champion when the history does not say."""
    for change in reversed(entry.history):
        if change.to_status == "under_review" and change.from_status not in (None, "under_review"):
            return change.from_status
    return "champion"


class ModelStatusStore:
    """Reads and changes the registry file (module docstring)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ModelStatusFile:
        """The registry, or an empty one when the file does not exist yet."""
        if not self.path.exists():
            return ModelStatusFile()
        return ModelStatusFile.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, registry: ModelStatusFile) -> None:
        """Write the registry atomically, through a temporary file whose name no other writer uses."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(registry.model_dump_json(indent=1) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)  # only still there when the write or the rename failed

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold an exclusive lock on <registry>.lock while one load-change-save cycle runs (module docstring).

        Do not nest it: a second lock taken inside the first one would wait forever.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def entry(self, model_id: str) -> ModelEntry:
        """One model's entry; KeyError when it is not registered."""
        for item in self.load().models:
            if item.model_id == model_id:
                return item
        raise KeyError(f"model {model_id!r} is not registered")

    def status(self, model_id: str) -> ModelStatus:
        """One model's current status."""
        return self.entry(model_id).status

    def register(self, entry: ModelEntry, reason: str = "registered", at: datetime | None = None) -> ModelEntry:
        """Add a model. Refuses a second entry with the same id, and a second model in use (champion or under review)
        in one family."""
        with self.locked():
            registry = self.load()
            if any(item.model_id == entry.model_id for item in registry.models):
                raise ValueError(f"model {entry.model_id!r} is already registered")
            if entry.status in IN_USE and any(
                item.family == entry.family and item.status in IN_USE for item in registry.models
            ):
                raise ValueError(f"family {entry.family!r} already has a model in use; promote a challenger instead")
            change = StatusChange(
                at=at or utc_now(), from_status=None, to_status=entry.status, actor="register", reason=reason
            )
            added = entry.model_copy(update={"history": [*entry.history, change]})
            registry.models.append(added)
            self.save(registry)
        return added

    def mark_under_review(self, model_id: str, reason: str, alert_id: str, at: datetime | None = None) -> ModelEntry:
        """Monitoring: the champion (the model in use) is flagged for a person to look at.

        Any other model is returned as it is. A challenger is not used for decisions, so an alert on it neither puts it
        in use nor blocks its promotion (the alert stays in the alert log). A model already under review or retired
        needs no new mark.
        """
        with self.locked():
            registry = self.load()
            for index, item in enumerate(registry.models):
                if item.model_id != model_id:
                    continue
                if item.status != "champion":
                    return item
                change = StatusChange(
                    at=at or utc_now(),
                    from_status=item.status,
                    to_status="under_review",
                    actor=MONITOR_ACTOR,
                    reason=reason,
                    alert_id=alert_id,
                )
                registry.models[index] = item.model_copy(
                    update={"status": "under_review", "history": [*item.history, change]}
                )
                self.save(registry)
                return registry.models[index]
        raise KeyError(f"model {model_id!r} is not registered")

    def promote(self, model_id: str, principal: Principal, reason: str, at: datetime | None = None) -> ModelEntry:
        """A model validator makes a challenger the champion of its family; the previous one is retired."""
        self._require_validator(principal)
        when = at or utc_now()
        with self.locked():
            registry = self.load()
            candidate = next((item for item in registry.models if item.model_id == model_id), None)
            if candidate is None:
                raise KeyError(f"model {model_id!r} is not registered")
            if candidate.status != "challenger":
                raise ValueError(f"only a challenger can be promoted; {model_id!r} is {candidate.status}")
            for index, item in enumerate(registry.models):
                if item.family == candidate.family and item.status in IN_USE:
                    registry.models[index] = self._changed(item, "retired", principal, f"replaced by {model_id}", when)
                elif item.model_id == model_id:
                    registry.models[index] = self._changed(item, "champion", principal, reason, when)
            self.save(registry)
        return next(item for item in registry.models if item.model_id == model_id)

    def close_review(self, model_id: str, principal: Principal, reason: str, at: datetime | None = None) -> ModelEntry:
        """A model validator closes a review (the alert was looked at and explained).

        The model gets back the status it had before it was marked (status_before_review: the champion). It is never
        made champion while another model of its family is in use, so a family keeps one model in use; closing the
        review is then refused with ValueError and nothing is written.
        """
        self._require_validator(principal)
        with self.locked():
            registry = self.load()
            for index, item in enumerate(registry.models):
                if item.model_id != model_id:
                    continue
                if item.status != "under_review":
                    raise ValueError(f"{model_id!r} is not under review; it is {item.status}")
                restored = status_before_review(item)
                others_in_use = [
                    other.model_id
                    for other in registry.models
                    if other.family == item.family and other.model_id != model_id and other.status in IN_USE
                ]
                if restored == "champion" and others_in_use:
                    raise ValueError(
                        f"family {item.family!r} already has a model in use ({', '.join(others_in_use)}); "
                        f"{model_id!r} cannot return to champion"
                    )
                registry.models[index] = self._changed(item, restored, principal, reason, at or utc_now())
                self.save(registry)
                return registry.models[index]
        raise KeyError(f"model {model_id!r} is not registered")

    @staticmethod
    def _require_validator(principal: Principal) -> None:
        """PermissionError unless the principal may take the action promote_model (security/rbac.py)."""
        decision = authorize(principal, PROMOTE_ACTION)
        if not decision.allowed:
            raise PermissionError(f"{PROMOTE_ACTION} refused: {decision.reason}")

    @staticmethod
    def _changed(
        item: ModelEntry, to_status: ModelStatus, principal: Principal, reason: str, at: datetime
    ) -> ModelEntry:
        """The entry with a new status and the change appended to its history."""
        change = StatusChange(
            at=at,
            from_status=item.status,
            to_status=to_status,
            actor=principal.principal_id,
            role=principal.role,
            reason=reason,
        )
        return item.model_copy(update={"status": to_status, "history": [*item.history, change]})
