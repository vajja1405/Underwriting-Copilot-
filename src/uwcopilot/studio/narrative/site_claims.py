"""Claims for SiteIQ's result: the site's scores, its location factors and the metro inputs, each with its status.

What each factor value becomes:
- DATA (a public dataset row): VERIFIED public_data with its locator: publisher, dataset, URL, retrieval date and the
  SHA-256 of the response, plus the geography and the area the value describes (the SourceRecord SiteIQ kept);
- SYNTHETIC (the generated city): VERIFIED public_data whose label and source say SYNTHETIC;
- ASSUMPTION / SOURCED / DATA_X_ASSUMPTION: ASSUMPTION with the range the metro input gives (or a zero-width range
  with a note when SiteIQ reports none), sourced or "unsourced";
- FOUNDER_INPUT (for example a rent quote typed for this site): USER_STATED;
- UNKNOWN (no data): UNKNOWN with SiteIQ's reason. Nothing is filled in (Studio rule G).
The overall score is a MODEL_ESTIMATE of the SiteIQ scoring method (its uncertainty is not measured per site); the
coverage score is a VERIFIED calculation (the weighted share of required factors that have data).
"""

from uwcopilot.evidence.ledger_models import UNSOURCED, Claim, InputValue, Provenance, unknown_claim
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import assumption_range_claim, model_claim, public_data_claim
from uwcopilot.studio.narrative.units import format_for
from uwcopilot.studio.siteiq.models import SCORING_METHOD, FactorScore, SiteScore

SITEIQ_MODEL = "siteiq"
SITEIQ_VERSION = SCORING_METHOD.split(":", 1)[0]
NO_RANGE_NOTE = "SiteIQ reports no range for this value; the range shown is the value itself"
NO_SCORE_REASON = "no factor of this site has data here (limited mode), so SiteIQ gives no overall score"


def locator(record: SourceRecord) -> str:
    """'publisher, dataset (vintage); url; retrieved YYYY-MM-DD; response sha256 abc123def456' for a source record."""
    vintage = f" ({record.vintage})" if record.vintage else ""
    prefix = "SYNTHETIC city data: " if record.data_label == "SYNTHETIC" else ""
    return (
        f"{prefix}{record.publisher}, {record.dataset}{vintage}; {record.url}; retrieved "
        f"{record.retrieved_at.date().isoformat()}; response sha256 {record.sha256[:12]}"
    )


def record_details(record: SourceRecord | None, area: str | None = None) -> dict[str, InputValue]:
    """The locator fields of a source record for a claim's details."""
    details: dict[str, InputValue] = {"area": area} if area else {}
    if record is None:
        return details
    details.update(
        {
            "url": record.url,
            "sha256": record.sha256,
            "geography": record.geography,
            "origin": record.origin,
            "data_label": record.data_label,
            "retrieved_at": record.retrieved_at.isoformat(),
        }
    )
    return details


def value_claim(
    claim_id: str,
    label: str,
    value: float,
    unit: str,
    status: str,
    record: SourceRecord | None,
    reason: str,
    metro_id: str,
    low: float | None = None,
    high: float | None = None,
    area: str | None = None,
) -> Claim:
    """One SiteIQ or metro value with the status rule of the module docstring (value must not be None)."""
    fmt = format_for(unit, value)
    scaled = fmt.scale(value)
    details = record_details(record, area)
    details["reason"] = reason
    synthetic = status == "SYNTHETIC" or (record is not None and record.data_label == "SYNTHETIC")
    if status in ("DATA", "SYNTHETIC"):
        source = locator(record) if record is not None else f"{status}: {reason}"
        shown = f"{label} (SYNTHETIC)" if synthetic else label
        snapshot = record.retrieved_at.date().isoformat() if record is not None else None
        return public_data_claim(
            claim_id, shown, scaled, fmt.unit, fmt.display, source, claim_id, metro_id, snapshot, details
        )
    if status == "FOUNDER_INPUT":
        return Claim(
            id=claim_id,
            label=label,
            value=scaled,
            unit=fmt.unit,
            display=fmt.display,
            status="USER_STATED",
            provenance=Provenance(kind="user_input", ref=claim_id, metro_id=metro_id, details=details),
            inputs={"field": claim_id, "value": scaled},
        )
    if low is None or high is None:
        low, high = value, value
        details["range_note"] = NO_RANGE_NOTE
    source = UNSOURCED if status == "ASSUMPTION" or record is None else locator(record)
    if synthetic and source == UNSOURCED:
        details["data_label"] = "SYNTHETIC"
    return assumption_range_claim(
        claim_id,
        label,
        scaled,
        fmt.unit,
        fmt.display,
        source,
        claim_id,
        fmt.scale(min(low, value)),
        fmt.scale(max(high, value)),
        metro_id,
        details,
    )


def factor_claims(score: SiteScore) -> list[Claim]:
    """One claim per location factor (and its 0-100 score when SiteIQ gave one)."""
    claims: list[Claim] = []
    for factor in score.factors:
        claim_id = ids.site_factor_id(factor.factor)
        label = f"{factor.description} ({factor.area})"
        if factor.raw_value is None or factor.status == "UNKNOWN":
            claims.append(
                unknown_claim(claim_id, label, "NONE", "text", factor.reason, kind="public_data", ref=claim_id)
            )
            continue
        metro_value = score.metro_inputs.get(factor.factor)
        low = metro_value.low if metro_value is not None else None
        high = metro_value.high if metro_value is not None else None
        record = factor.sources[0] if factor.sources else None
        claims.append(
            value_claim(
                claim_id,
                label,
                factor.raw_value,
                factor.unit,
                factor.status,
                record,
                factor.reason,
                score.metro_id,
                low,
                high,
                factor.area,
            )
        )
        if factor.normalized_score is not None:
            claims.append(factor_score_claim(score, factor))
    return claims


def factor_score_claim(score: SiteScore, factor: FactorScore) -> Claim:
    """The factor's 0-100 percentile score within the metro (MODEL_ESTIMATE of the SiteIQ scoring method)."""
    return model_claim(
        ids.safe_id("SITE", "factor", factor.factor, "score"),
        f"SiteIQ score for {factor.description} (0-100, percentile rank within the metro)",
        factor.normalized_score,
        "SCORE",
        "int",
        SITEIQ_MODEL,
        SITEIQ_VERSION,
        "not measured per site",
        {"metro_id": score.metro_id, "normalization": factor.normalization, "reference_size": factor.reference_size},
        f"factors.{factor.factor}.normalized_score",
    )


def metro_input_claims(score: SiteScore) -> list[Claim]:
    """The metro inputs SiteIQ hands to the simulator (wages, rent range, prime rate), with their status."""
    claims: list[Claim] = []
    for name, item in sorted(score.metro_inputs.items()):
        claims.append(metro_input_claim(name, item, score.metro_id))
    return claims


def metro_input_claim(name: str, item: FactorValue, metro_id: str) -> Claim:
    """One metro input (UNKNOWN with the reason when it has no value)."""
    claim_id = ids.safe_id("SITE", "metro", name)
    label = f"Metro input: {name.replace('_', ' ')} ({item.unit})"
    if item.value is None or item.status == "UNKNOWN":
        return unknown_claim(claim_id, label, "NONE", "text", item.reason, kind="public_data", ref=claim_id)
    reason = item.reason + (f"; derived with: {'; '.join(item.assumptions)}" if item.assumptions else "")
    return value_claim(
        claim_id, label, item.value, item.unit, item.status, item.source, reason, metro_id, item.low, item.high
    )


def score_claims(score: SiteScore) -> list[Claim]:
    """The overall score (MODEL_ESTIMATE, or UNKNOWN in limited mode) and the coverage score (VERIFIED calculation)."""
    run: dict[str, InputValue] = {"metro_id": score.metro_id, "profile_id": score.profile_id, "mode": score.mode}
    if score.overall_score is None:
        overall = unknown_claim(
            ids.SITE_OVERALL, "SiteIQ overall site score (0-100)", "SCORE", "int", NO_SCORE_REASON, "model", "siteiq"
        )
    else:
        overall = model_claim(
            ids.SITE_OVERALL,
            "SiteIQ overall site score (0-100, weighted percentile rank of the scored factors)",
            score.overall_score,
            "SCORE",
            "int",
            SITEIQ_MODEL,
            SITEIQ_VERSION,
            "not measured per site",
            run,
            "overall_score",
        )
    coverage = Claim(
        id=ids.SITE_COVERAGE,
        label="SiteIQ data coverage (weighted share of the required factors backed by data, 0 to 1)",
        value=score.coverage_score,
        unit="RATIO",
        display="ratio2",
        status="VERIFIED",
        provenance=Provenance(kind="calculation", ref="SiteIQ coverage_score", metro_id=score.metro_id),
        inputs={**run, "value": score.coverage_score},
    )
    return [overall, coverage]


def site_claims(score: SiteScore | None) -> list[Claim]:
    """Every SiteIQ claim of one site (empty when no SiteIQ result was given)."""
    if score is None:
        return []
    return [*score_claims(score), *factor_claims(score), *metro_input_claims(score)]
