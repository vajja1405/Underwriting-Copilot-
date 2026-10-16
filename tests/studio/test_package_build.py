"""build_package (Studio S6): one founder ledger, sections that point into it, the SBA section names, the package's
own claims and the round-trip audit of the package text. SYNTHETIC golden projects, offline."""

import json

import pytest
from package_helpers import package, project

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package import PACKAGE_SCHEMA_VERSION, FounderProject, Package, build_package, package_text
from uwcopilot.studio.package.audit import audit_package, untraced_numbers
from uwcopilot.studio.package.models import Paragraph
from uwcopilot.studio.package.project import PackageOwner
from uwcopilot.studio.package.sba import SECTION_SPECS, sba_record, uncovered_sba_sections, unknown_sba_names

METRO, TYPE = "synthetic_metro", "car_wash"
CELL_REFERENCE_KINDS = ("claim", "status", "range", "source", "ref")


def test_the_package_is_versioned_json_that_round_trips() -> None:
    built = package(METRO, TYPE)
    assert built.schema_version == PACKAGE_SCHEMA_VERSION == "founder-package-v1"
    assert built.package_id.startswith("FP-") and built.data_label == "SYNTHETIC"
    assert Package.model_validate_json(built.model_dump_json()) == built
    assert json.loads(built.model_dump_json())["ledger"]["subject_id"] == f"GOLDEN-{METRO}-{TYPE}"


def test_the_same_project_gives_the_same_package() -> None:
    assert build_package(project(METRO, TYPE)) == package(METRO, TYPE)


def test_sections_follow_the_sba_record_and_show_what_they_do_not_cover() -> None:
    record = sba_record()
    assert record["final_url"].startswith("https://www.sba.gov/") and len(record["sha256"]) == 64
    assert unknown_sba_names() == []
    assert uncovered_sba_sections() == ["Marketing and sales"]
    built = package(METRO, TYPE)
    assert [section.key for section in built.sections] == [spec.key for spec in SECTION_SPECS]
    assert [section.number for section in built.sections] == list(range(1, len(SECTION_SPECS) + 1))


def test_every_cell_points_into_the_one_ledger() -> None:
    built = package(METRO, TYPE)
    claim_ids = {claim.id for claim in built.ledger.claims}
    assertion_ids = {assertion.id for assertion in built.ledger.assertions}
    for section in built.sections:
        for block in section.blocks:
            if block.kind == "table":
                for row in block.rows:
                    for cell in row:
                        if cell.kind in CELL_REFERENCE_KINDS:
                            assert cell.ref in claim_ids, (section.key, cell.ref)
                        if cell.kind == "assert":
                            assert cell.ref in assertion_ids, (section.key, cell.ref)
            if block.kind == "key_facts":
                assert all(fact.ref in claim_ids for fact in block.items)
    assert set(built.appendix_index) == claim_ids
    assert sorted(built.appendix_index.values()) == list(range(1, len(claim_ids) + 1))


def test_the_package_text_traces_to_the_ledger() -> None:
    built = package(METRO, TYPE)
    assert built.audit.untraced == [] and built.audit.numbers > 500
    assert audit_package(built) == built.audit


def test_the_audit_catches_a_number_that_is_not_in_the_ledger() -> None:
    built = package(METRO, TYPE)
    stray = Paragraph(draft="The site will earn $987,654 in year 2 and 17.37% more after Month 4.")
    first = built.sections[0].model_copy(update={"blocks": [*built.sections[0].blocks, stray]})
    tampered = built.model_copy(update={"sections": [first, *built.sections[1:]]})
    assert audit_package(tampered).untraced == ["$987,654", "17.37%"]


def test_layout_labels_never_hide_a_value() -> None:
    empty = Ledger()
    assert untraced_numbers("per year\n9.75", empty) == ["9.75"]
    assert untraced_numbers("Month 3 $4,321,987", empty) == ["$4,321,987"]
    assert untraced_numbers("Year 2.5", empty) == ["2.5"]
    assert untraced_numbers("year-\n2 below\nzero Page 3/9 P50 rank 2 Change-set 1 [anything 1234]", empty) == []
    assert untraced_numbers("-$53,735", empty) == ["53,735"]


def test_package_claims_follow_the_status_rules() -> None:
    built = package(METRO, TYPE)
    ledger = built.ledger
    rate = ledger.claim(ids.RATE_NOTE)
    note = ledger.claim("PKG.loan.note_rate_pct")
    assert note.value == rate.value and note.status == rate.status and note.display == "ratio2"
    payment = ledger.claim("PKG.loan.monthly_payment")
    if payment.status == "ASSUMPTION":
        assert payment.value_range.low <= payment.value <= payment.value_range.high
    final = max(key for key in built.appendix_index if key.startswith("PKG.loan.y") and key.endswith("end_balance"))
    assert ledger.claim(final).value >= 0
    assert ledger.claim("PKG.business_name").status == "USER_STATED"
    for claim in ledger.claims:
        if claim.id.startswith("PKG.site.c") and claim.id.endswith("_pct"):
            assert claim.status == "MODEL_ESTIMATE" and claim.provenance.model_name == "studio-feasibility"


def test_candidate_sites_are_simulated_with_the_same_seed_and_paths() -> None:
    built = package(METRO, TYPE)
    built_project = project(METRO, TYPE)
    for index in range(1, len(built_project.candidates) + 1):
        claim = built.ledger.claim(f"PKG.site.c{index}.p_dscr_pct")
        assert claim.provenance.details["seed"] == built_project.feasibility.seed
        assert claim.provenance.details["n_paths"] == built_project.feasibility.n_paths


def test_a_metro_without_a_siteiq_result_gets_no_map_and_no_factors() -> None:
    without_site = project(METRO, TYPE).model_copy(update={"site_score": None})
    built = build_package(without_site)
    assert built.map is None and built.audit.untraced == []
    assert built.ledger.claim("SITE.overall_score") is None
    market = built.section("market")
    assert all(block.kind != "figure" for block in market.blocks)
    assert "No SiteIQ result" in package_text(built)


def test_the_map_layer_is_geojson_from_tract_centroids() -> None:
    built = package(METRO, TYPE)
    kinds = {feature["properties"]["kind"] for feature in built.map.geojson["features"]}
    assert kinds == {"tract", "site", "near_ring", "market_ring"}
    assert built.map.geojson["type"] == "FeatureCollection"
    assert "no map tiles" in built.map.geojson["metadata"]["geometry"]


def test_owners_must_agree_with_the_plan_facts() -> None:
    base = project(METRO, TYPE)
    owner = base.owners[0]
    wrong = owner.model_copy(update={"credit_score": (owner.credit_score or 700) - 1})
    with pytest.raises(ValueError, match="credit score"):
        FounderProject.model_validate({**base.model_dump(), "owners": [wrong.model_dump()]})


def test_an_unknown_metro_or_document_code_is_refused() -> None:
    base = project(METRO, TYPE).model_dump()
    with pytest.raises(ValueError, match="document types"):
        FounderProject.model_validate(
            {
                **base,
                "documents": [
                    {"field": "plan.owner_equity", "value": 1.0, "doc_id": "D1", "document_type": "tax_return"}
                ],
            }
        )
    case = {**base["case"], "metro_id": "springfield"}
    with pytest.raises(ValueError):
        FounderProject.model_validate({**base, "case": case})


def test_free_text_is_printed_as_data_and_traced_to_its_claim() -> None:
    base = project(METRO, TYPE)
    owner = PackageOwner.model_validate({**base.owners[0].model_dump(), "name": "Pat Route 66"})
    renamed = base.model_copy(update={"business_name": "Route 66 Wash & Co <b>", "owners": [owner]})
    built = build_package(renamed)
    assert built.audit.untraced == []
    assert built.ledger.claim("PKG.business_name").value == "Route 66 Wash & Co <b>"
    assert "Route 66 Wash & Co <b>" in package_text(built)
