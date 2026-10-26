"""Promote a challenger to champion: the model validator's action (`make promote-model`, Phase 6 C).

    uv run python scripts/promote_model.py <model_id> --role model_validator --principal <id> --reason "<why>"

Calls governance/model_status.py ModelStatusStore.promote, which asks security/rbac.py whether the principal may take
the action "promote_model". Only the model_validator role may; every other role (underwriter, senior_underwriter,
admin, system, unknown) is refused and nothing is written. Promoting makes the challenger the champion of its family
and retires the previous champion; both changes are appended to the models' histories with who, when and why.

Roles are CLAIMED in this demo (there is no identity provider for the command line): --role shows how the rule is
enforced once a role is known; it does not prove who is typing. The registry holds SYNTHETIC models only.

Before promoting, a validator reads the evidence: section "champion_challenger" of results/governance_metrics.json
(scripts/champion_challenger.py) gives the comparison on the same out-of-time window and a recommendation. The
recommendation is advice; this script does not check it, because the decision is the validator's.

Exit codes: 0 promoted; 1 refused (the role may not promote); 2 bad request (unknown model or role, or the model is not
a challenger). The model's history is printed in every case where the model exists.
"""

import argparse
from pathlib import Path

from uwcopilot.common.paths import DATA_DIR
from uwcopilot.governance.model_status import ModelEntry, ModelStatusStore
from uwcopilot.governance.provenance import display_path
from uwcopilot.security.rbac import Principal

DEFAULT_REGISTRY = DATA_DIR / "governance" / "model_status.json"
PROMOTED = 0
REFUSED = 1
BAD_REQUEST = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_id", help="the challenger to promote")
    parser.add_argument("--role", required=True, help="the acting role (CLAIMED); only model_validator may promote")
    parser.add_argument("--principal", required=True, help="who is acting, e.g. validator.jdoe")
    parser.add_argument("--reason", required=True, help="why the challenger is promoted (kept in the history)")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="model status registry (JSON)")
    return parser.parse_args(argv)


def print_history(entry: ModelEntry) -> None:
    """One line per status change of a model, oldest first."""
    print(f"{entry.model_id} (family {entry.family}, version {entry.version}, {entry.data_label}): {entry.status}")
    for change in entry.history:
        role = f" ({change.role})" if change.role else ""
        print(
            f"  {change.at.isoformat()}  {change.from_status or '-'} -> {change.to_status}  "
            f"by {change.actor}{role}: {change.reason}"
        )


def print_family(store: ModelStatusStore, family: str) -> None:
    """The current status of every model of a family, with its latest change."""
    print(f"Family {family} now:")
    for item in store.load().models:
        if item.family != family:
            continue
        last = item.history[-1] if item.history else None
        latest = f" (last change by {last.actor}: {last.reason})" if last else ""
        print(f"  {item.model_id}: {item.status}{latest}")


def main(argv: list[str] | None = None) -> int:
    """Promote, or refuse; print the history either way."""
    args = parse_args(argv)
    store = ModelStatusStore(args.registry)
    try:
        principal = Principal(args.principal, args.role, "claimed")
    except ValueError as error:
        print(f"Bad request: {error}")
        return BAD_REQUEST
    try:
        entry = store.entry(args.model_id)
    except KeyError as error:
        print(f"Bad request: {error.args[0]} in {display_path(args.registry)}")
        return BAD_REQUEST

    try:
        promoted = store.promote(args.model_id, principal, reason=args.reason)
    except PermissionError as error:
        print(f"Refused: {error}. Nothing was changed.")
        print_history(entry)
        return REFUSED
    except ValueError as error:
        print(f"Bad request: {error}. Nothing was changed.")
        print_history(entry)
        return BAD_REQUEST

    print(f"Promoted {promoted.model_id} to champion by {principal.principal_id} ({principal.role}, role claimed).")
    print_history(promoted)
    print_family(store, promoted.family)
    return PROMOTED


if __name__ == "__main__":
    raise SystemExit(main())
