"""Claim constructors for the founder ledger, and the rule for numbers computed from other claims.

The rule for a derived number (CALC.*): it is never more certain than its least certain input. Statuses from the
least to the most certain:

    UNKNOWN < CONFLICTING < MODEL_ESTIMATE < ASSUMPTION < USER_STATED < VERIFIED

- an UNKNOWN input makes the result UNKNOWN (no value is computed from a value nobody has);
- a CONFLICTING input makes it CONFLICTING, linked to the conflicting inputs;
- a MODEL_ESTIMATE input (a simulated P50, say) makes it a MODEL_ESTIMATE of the same model;
- an ASSUMPTION input makes it an ASSUMPTION, with a range the caller computes from the inputs' ranges;
- founder figures only (plus policy constants and verified values) make it USER_STATED: "your figures give ...";
- only verified values and policy constants give a VERIFIED calculation.
Every derived claim lists its inputs (claim id -> value) and its formula, so inputs_sha256 pins both, and names the
ASSUMPTION and CONFLICTING inputs it rests on (depends_on_assumption / depends_on_conflicting). This is stricter than
the lender ledger's CALC-* claims, which call the arithmetic VERIFIED and list only open dependencies: a founder must
never see a number computed from their own guesses printed as if it were checked.
"""

from collections.abc import Sequence
from typing import Any

from uwcopilot.evidence.ledger_models import (
    Claim,
    ClaimValue,
    InputValue,
    Provenance,
    ValueRange,
    is_number,
)

STATUS_ORDER: tuple[str, ...] = ("UNKNOWN", "CONFLICTING", "MODEL_ESTIMATE", "ASSUMPTION", "USER_STATED", "VERIFIED")
DERIVED_ASSUMPTION_SOURCE = "calculated from ASSUMPTION inputs ({ids}); their own sources apply"
DERIVED_MODEL_UNCERTAINTY = "computed from model estimates ({ids}); it carries their uncertainty"
DERIVED_UNKNOWN_REASON = "needs {ids}, which have no value yet"


def weakest_status(claims: Sequence[Claim]) -> str:
    """The least certain status among the claims (VERIFIED when the list is empty)."""
    ranks = [STATUS_ORDER.index(claim.status) for claim in claims]
    return STATUS_ORDER[min(ranks)] if ranks else "VERIFIED"


def scalar(value: Any) -> InputValue:
    """A claim value as a JSON scalar for an inputs dict (other types become their text)."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def ids_text(claims: Sequence[Claim]) -> str:
    """'A, B, C' of the claim ids."""
    return ", ".join(claim.id for claim in claims)


def derived_claim(
    claim_id: str,
    label: str,
    value: ClaimValue,
    unit: str,
    display: str,
    formula: str,
    inputs: Sequence[Claim],
    *,
    value_range: tuple[float, float] | None = None,
    constants: dict[str, InputValue] | None = None,
) -> Claim:
    """A CALC.* claim: value computed by `formula` from `inputs`, with the status rule of the module docstring.

    value_range (low, high) is required when the result is a numeric ASSUMPTION (the caller evaluates the formula
    at the ends of its inputs' ranges). constants are policy numbers the formula used (kept in the inputs).
    """
    recorded: dict[str, InputValue] = {"formula": formula, **(constants or {})}
    recorded.update({claim.id: scalar(claim.value) for claim in inputs})
    status = weakest_status(inputs)
    assumed = tuple(claim.id for claim in inputs if claim.status == "ASSUMPTION")
    conflicting = tuple(claim.id for claim in inputs if claim.status == "CONFLICTING")
    unknown = [claim for claim in inputs if claim.status == "UNKNOWN"]
    common: dict[str, Any] = {
        "id": claim_id,
        "label": label,
        "unit": unit,
        "display": display,
        "inputs": recorded,
        "depends_on_assumption": assumed,
        "depends_on_conflicting": conflicting,
    }
    if unknown:
        return Claim(
            **common,
            value=None,
            status="UNKNOWN",
            provenance=Provenance(kind="calculation", ref=formula),
            reason=DERIVED_UNKNOWN_REASON.format(ids=ids_text(unknown)),
            depends_on_unknown=tuple(claim.id for claim in unknown),
        )
    if status == "CONFLICTING":
        return Claim(
            **common,
            value=value,
            status="CONFLICTING",
            provenance=Provenance(kind="calculation", ref=formula),
            conflict_ids=conflicting,
        )
    if status == "MODEL_ESTIMATE":
        model = next(claim for claim in inputs if claim.status == "MODEL_ESTIMATE")
        models = [claim for claim in inputs if claim.status == "MODEL_ESTIMATE"]
        return Claim(
            **common,
            value=value,
            status="MODEL_ESTIMATE",
            provenance=Provenance(
                kind="model",
                ref=formula,
                model_name=model.provenance.model_name,
                model_version=model.provenance.model_version,
                details=dict(model.provenance.details),
            ),
            uncertainty=DERIVED_MODEL_UNCERTAINTY.format(ids=ids_text(models)),
        )
    if status == "ASSUMPTION":
        if is_number(value) and value_range is None:
            raise ValueError(f"{claim_id}: a numeric derived ASSUMPTION needs its range")
        assumptions = [claim for claim in inputs if claim.status == "ASSUMPTION"]
        low, high = value_range if value_range is not None else (None, None)
        return Claim(
            **common,
            value=value,
            status="ASSUMPTION",
            provenance=Provenance(
                kind="assumption", ref=formula, source=DERIVED_ASSUMPTION_SOURCE.format(ids=ids_text(assumptions))
            ),
            value_range=ValueRange(low=low, high=high) if low is not None and high is not None else None,
        )
    if status == "USER_STATED":
        return Claim(
            **common,
            value=value,
            status="USER_STATED",
            provenance=Provenance(kind="user_input", ref=f"calculated from the founder's figures: {formula}"),
        )
    return Claim(**common, value=value, status="VERIFIED", provenance=Provenance(kind="calculation", ref=formula))


def policy_claim(
    claim_id: str,
    label: str,
    value: ClaimValue,
    unit: str,
    display: str,
    section: str,
    source: str,
    comparator: str | None = None,
) -> Claim:
    """A threshold of the SYNTHETIC startup program or a Studio target: VERIFIED, kind policy.

    comparator ("at least", "at most", ...) says how the threshold is read; the founder checker uses it.
    """
    details: dict[str, InputValue] = {"comparator": comparator} if comparator else {}
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="VERIFIED",
        provenance=Provenance(kind="policy", ref=section, source=source, details=details),
        inputs={"section": section, "value": scalar(value)},
    )


def model_claim(
    claim_id: str,
    label: str,
    value: ClaimValue,
    unit: str,
    display: str,
    model_name: str,
    model_version: str,
    uncertainty: str,
    run: dict[str, InputValue],
    output: str,
) -> Claim:
    """A MODEL_ESTIMATE: one output of a model run. run holds the run's identity (version, seed, n paths, inputs
    hash ...); output names the field of the model's result the value was read from."""
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="MODEL_ESTIMATE",
        provenance=Provenance(
            kind="model", ref=output, model_name=model_name, model_version=model_version, details=run
        ),
        inputs={**run, "output": output, "value": scalar(value)},
        uncertainty=uncertainty,
    )


def assumption_range_claim(
    claim_id: str,
    label: str,
    value: ClaimValue,
    unit: str,
    display: str,
    source: str,
    ref: str,
    low: float | None,
    high: float | None,
    metro_id: str | None = None,
    details: dict[str, InputValue] | None = None,
) -> Claim:
    """An ASSUMPTION with its range and its source ("unsourced" when no publication backs it). The value is in the
    inputs, so the inputs hash pins it."""
    value_range = ValueRange(low=low, high=high) if low is not None and high is not None else None
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="ASSUMPTION",
        provenance=Provenance(kind="assumption", ref=ref, source=source, metro_id=metro_id, details=details or {}),
        inputs={"ref": ref, "value": scalar(value), "low": low, "high": high},
        value_range=value_range,
    )


def public_data_claim(
    claim_id: str,
    label: str,
    value: ClaimValue,
    unit: str,
    display: str,
    source: str,
    ref: str,
    metro_id: str | None = None,
    snapshot_date: str | None = None,
    details: dict[str, InputValue] | None = None,
) -> Claim:
    """VERIFIED public data (or SYNTHETIC city data in the SYNTHETIC metro), with its locator in source/details."""
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="VERIFIED",
        provenance=Provenance(
            kind="public_data",
            ref=ref,
            source=source,
            metro_id=metro_id,
            snapshot_date=snapshot_date,
            details=details or {},
        ),
        inputs={"ref": ref, "value": scalar(value), "source": source},
    )


def percent_of(share: float) -> float:
    """A share (0.84) as a percent (84.0) for the pct1 display; a probability is never printed as pct1 directly."""
    return float(share) * 100.0
