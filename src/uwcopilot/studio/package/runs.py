"""The model runs behind the ledger's MODEL_ESTIMATE claims, listed once in the evidence appendix.

Hundreds of claims come from the same run (the simulation of the chosen site: model studio-feasibility, its version,
seed, number of paths and plan SHA-256). Instead of repeating that identity on every row, the appendix lists each
run once ("run A", "run B", ...) with its settings and the uncertainty notes most of its claims share, and every
row names its run. A run is one (model name, model version, provenance details) triple; labels follow the order in
which runs first appear in the ledger.
"""

from pydantic import BaseModel, ConfigDict

from uwcopilot.agent.applications import canonical_json
from uwcopilot.evidence.ledger_models import Claim, InputValue, Ledger

COMMON_NOTE_MIN_CLAIMS = 3
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class ModelRun(BaseModel):
    """One model run: its label, model and version, settings, shared uncertainty notes and claim count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    model_name: str
    model_version: str
    details: dict[str, InputValue]
    common_notes: list[str]
    claims: int


class RunIndex(BaseModel):
    """The ledger's model runs and which run each MODEL_ESTIMATE claim belongs to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runs: list[ModelRun]
    run_of: dict[str, str]

    def run(self, label: str) -> ModelRun | None:
        """The run with this label."""
        return next((item for item in self.runs if item.label == label), None)

    def is_common(self, claim: Claim) -> bool:
        """True when the claim's uncertainty is one of its run's shared notes (printed once, in the run table)."""
        label = self.run_of.get(claim.id)
        run = self.run(label) if label is not None else None
        return run is not None and claim.uncertainty in run.common_notes


def run_key(claim: Claim) -> str:
    """The identity of the run a model claim comes from."""
    origin = claim.provenance
    return canonical_json([origin.model_name, origin.model_version, origin.details])


def run_label(position: int) -> str:
    """'run A', 'run B', ... ('run A2' after Z)."""
    letter = LETTERS[position % len(LETTERS)]
    lap = position // len(LETTERS)
    return f"run {letter}" + (str(lap + 1) if lap else "")


def model_runs(ledger: Ledger) -> RunIndex:
    """Every run of the ledger's MODEL_ESTIMATE claims (module docstring)."""
    members: dict[str, list[Claim]] = {}
    for item in ledger.claims:
        if item.status == "MODEL_ESTIMATE" and item.provenance.kind == "model" and item.provenance.model_name:
            members.setdefault(run_key(item), []).append(item)
    runs: list[ModelRun] = []
    run_of: dict[str, str] = {}
    for position, claims in enumerate(members.values()):
        label = run_label(position)
        counts: dict[str, int] = {}
        for item in claims:
            if item.uncertainty:
                counts[item.uncertainty] = counts.get(item.uncertainty, 0) + 1
        origin = claims[0].provenance
        runs.append(
            ModelRun(
                label=label,
                model_name=str(origin.model_name),
                model_version=str(origin.model_version or ""),
                details=dict(origin.details),
                common_notes=[note for note, count in counts.items() if count >= COMMON_NOTE_MIN_CLAIMS],
                claims=len(claims),
            )
        )
        for item in claims:
            run_of[item.id] = label
    return RunIndex(runs=runs, run_of=run_of)
