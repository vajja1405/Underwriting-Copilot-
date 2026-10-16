"""Claims for the path-to-targets search (Studio S5): every one a MODEL_ESTIMATE of the search engine.

Recorded: the search status (text), the plan's three simulated probabilities before any change, and for each
returned change-set (ranks 1-3, or rank 0 for the closest result when nothing reaches the targets) the number of
levers it moves, the three probabilities after the change and every lever's new value. A changed figure is a plan
change suggested by a simulation, not a quote and not verified: its uncertainty says so, and the founder's own figure
stays USER_STATED in the ledger.
"""

from uwcopilot.evidence.ledger_models import Claim, InputValue
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import model_claim, percent_of
from uwcopilot.studio.narrative.units import format_for
from uwcopilot.studio.path_to_approval.models import AppliedChange, ChangeSet, Outcome, PathResult

PATH_MODEL = "studio-path-to-approval"
CHANGE_UNCERTAINTY = "a plan change suggested by a search over a grid of values; not a quote and not verified"
OUTCOME_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("p_dscr_at_least_target_year2", "p_dscr_pct", "Chance that year-2 DSCR reaches the target"),
    ("p_stressed_dscr_at_least_min_year2", "p_stressed_pct", "Chance that year-2 DSCR passes the revenue stress"),
    ("p_cash_negative_within_months", "p_cash_negative_pct", "Chance that cash falls below zero"),
)


class PathClaims:
    """Builds the claims of one PathResult."""

    def __init__(self, result: PathResult) -> None:
        """The run identity every claim carries: engine and simulator versions, seed, paths, mode."""
        self.result = result
        settings = result.settings
        self.run: dict[str, InputValue] = {
            "engine_version": result.engine_version,
            "simulator_version": result.simulator_version,
            "seed": settings.seed,
            "n_paths": settings.n_paths,
            "mode": settings.mode,
            "metro_id": result.metro_id,
            "profile_id": result.profile_id,
        }

    def estimate(
        self, claim_id: str, label: str, value: float | str, unit: str, display: str, output: str, uncertainty: str
    ) -> Claim:
        """One MODEL_ESTIMATE of this search."""
        return model_claim(
            claim_id, label, value, unit, display, PATH_MODEL, self.result.engine_version, uncertainty, self.run, output
        )

    def outcome(self, prefix: str, outcome: Outcome, words: str) -> list[Claim]:
        """The three probabilities of one plan (before, or after a change-set)."""
        uncertainty = (
            f"share of {outcome.n_paths} simulated paths (seed {outcome.seed}, common random numbers); simulated, "
            "based on stated assumptions"
        )
        return [
            self.estimate(
                f"{prefix}.{suffix}",
                f"{label}, {words} (simulated)",
                percent_of(getattr(outcome, field)),
                "PERCENT",
                "pct1",
                f"{prefix}.{field}",
                uncertainty,
            )
            for field, suffix, label in OUTCOME_FIELDS
        ]

    def change(self, rank: int, index: int, change: AppliedChange) -> list[Claim]:
        """Which lever a change-set moves and its new value (text for a candidate site)."""
        prefix = ids.change_set_id(rank, f"c{index}")
        claims = [
            self.estimate(
                f"{prefix}.lever",
                f"Change-set {rank}, change {index}: lever",
                change.label,
                "TEXT",
                "text",
                f"change_sets[{rank}].changes[{index}].lever",
                CHANGE_UNCERTAINTY,
            )
        ]
        if isinstance(change.to_value, str):
            claims.append(
                self.estimate(
                    f"{prefix}.to",
                    f"Change-set {rank}, change {index}: new {change.label.lower()}",
                    change.to_value,
                    "TEXT",
                    "text",
                    f"change_sets[{rank}].changes[{index}].to_value",
                    CHANGE_UNCERTAINTY,
                )
            )
            return claims
        fmt = format_for(change.unit, float(change.to_value))
        claims.append(
            self.estimate(
                f"{prefix}.to",
                f"Change-set {rank}, change {index}: new {change.label.lower()}",
                fmt.scale(float(change.to_value)),
                fmt.unit,
                fmt.display,
                f"change_sets[{rank}].changes[{index}].to_value",
                CHANGE_UNCERTAINTY,
            )
        )
        return claims

    def change_set(self, change_set: ChangeSet, rank: int) -> list[Claim]:
        """One change-set: how many levers, the outcome after it, and every change."""
        claims = [
            self.estimate(
                ids.change_set_id(rank, "n_levers"),
                f"Change-set {rank}: number of levers moved",
                change_set.n_levers,
                "COUNT",
                "int",
                f"change_sets[{rank}].n_levers",
                CHANGE_UNCERTAINTY,
            )
        ]
        claims.extend(self.outcome(ids.change_set_id(rank, "after"), change_set.after, f"after change-set {rank}"))
        for index, change in enumerate(change_set.changes, start=1):
            claims.extend(self.change(rank, index, change))
        return claims

    def all(self) -> list[Claim]:
        """Every claim of the search (see the module docstring)."""
        result = self.result
        claims = [
            self.estimate(
                ids.PATH_STATUS,
                "Path-to-targets search status",
                result.status,
                "TEXT",
                "text",
                "status",
                "the search's own status for these targets and lever bounds",
            )
        ]
        claims.extend(self.outcome("PATH.before", result.before, "before any change"))
        for change_set in result.change_sets:
            claims.extend(self.change_set(change_set, change_set.rank))
        if result.closest is not None:
            claims.extend(self.change_set(result.closest, 0))
        return claims


def path_claims(result: PathResult | None) -> list[Claim]:
    """Every claim of a path search (empty when no search result was given)."""
    return [] if result is None else PathClaims(result).all()
