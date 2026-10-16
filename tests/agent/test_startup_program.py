"""The SYNTHETIC startup program document (corpus/programs/) and how its sections become citable.

The program must be labelled SYNTHETIC, parse into NSP-x.y sections, reference only sections that exist, and be
citable by the agent (evidence registry, claim checker, SIG validator) without entering the RAG corpus.
"""

import re
from pathlib import Path

import pytest

from uwcopilot.agent import policy_startup
from uwcopilot.agent.claim_checker import numbers_in
from uwcopilot.agent.evaluation import corpus_trigger_ids
from uwcopilot.agent.evidence import citable_section_ids, guideline_sections
from uwcopilot.agent.programs import PROGRAMS_DIR, STARTUP_PROGRAM_DOC_ID, load_program_chunks
from uwcopilot.mcp_server import cli
from uwcopilot.mcp_server.guideline_store import GuidelineStore, UnknownSectionError
from uwcopilot.provenance.memo_grammar import validate
from uwcopilot.provenance.numeric_check import check_numbers
from uwcopilot.provenance.reference import citable_section_texts, section_texts
from uwcopilot.rag.corpus import load_corpus
from uwcopilot.rag.guidelines import load_guideline_chunks

PROGRAM_PATH = PROGRAMS_DIR / "northwind_startup_program.md"
EXPECTED_SECTIONS = [
    "NSP-1.1",
    "NSP-1.2",
    "NSP-1.3",
    "NSP-2.1",
    "NSP-2.2",
    "NSP-2.3",
    "NSP-2.4",
    "NSP-3.1",
    "NSP-3.2",
    "NSP-4.1",
    "NSP-4.2",
    "NSP-4.3",
    "NSP-4.4",
    "NSP-5.1",
    "NSP-6.1",
    "NSP-7.1",
    "NSP-7.2",
    "NSP-7.3",
    "NSP-8.1",
    "NSP-9.1",
    "NSP-9.2",
]


def program_text() -> str:
    """The program markdown."""
    return PROGRAM_PATH.read_text(encoding="utf-8")


def test_program_is_labelled_synthetic_and_not_advice() -> None:
    banner = program_text().split("\n# ", 1)[0]
    assert "SYNTHETIC DOCUMENT. FICTIONAL INSTITUTION." in banner
    assert "illustrative" in banner and "not an industry standard" in banner
    assert "not lending, legal or compliance advice" in banner


def test_program_parses_into_the_expected_sections_in_order() -> None:
    chunks = load_program_chunks()
    assert [chunk.chunk_id for chunk in chunks] == EXPECTED_SECTIONS
    assert {chunk.doc_id for chunk in chunks} == {STARTUP_PROGRAM_DOC_ID}
    assert all(chunk.synthetic and chunk.source_file.startswith("corpus/programs/") for chunk in chunks)


def test_every_section_the_program_mentions_exists() -> None:
    text = program_text()
    sections = guideline_sections()
    mentioned = set(re.findall(r"\b(?:NSP|NBC)-\d+\.\d+\b", text))
    assert mentioned and mentioned <= set(sections), sorted(mentioned - set(sections))


def test_trigger_table_lists_r_nsp_01_to_10_and_code_uses_only_those() -> None:
    table = guideline_sections()["NSP-9.2"][1]
    in_table = set(re.findall(r"\bR-NSP-\d{2}\b", table))
    assert in_table == {f"R-NSP-{number:02d}" for number in range(1, 11)}
    code = Path(policy_startup.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r"\bR-NSP-\d{2}\b", code)) == in_table


def test_every_section_id_written_in_the_startup_policy_code_exists() -> None:
    code = Path(policy_startup.__file__).read_text(encoding="utf-8")
    cited = set(re.findall(r'"((?:NSP|NBC)-\d+\.\d+)"', code))
    assert cited and cited <= citable_section_ids(), sorted(cited - citable_section_ids())


def test_program_thresholds_match_the_policy_constants() -> None:
    sections = guideline_sections()
    assert "| Startup term loan | $50,000 | $500,000 | 60 to 120 months" in sections["NSP-8.1"][1]
    assert policy_startup.STARTUP_MIN_AMOUNT == 50_000 and policy_startup.STARTUP_MAX_AMOUNT == 500_000
    assert (policy_startup.STARTUP_MIN_TERM_MONTHS, policy_startup.STARTUP_MAX_TERM_MONTHS) == (60, 120)
    assert "at least 680" in sections["NSP-2.2"][1] and policy_startup.MIN_STARTUP_CREDIT_SCORE == 680
    assert "at least 20.0%" in sections["NSP-3.1"][1] and policy_startup.MIN_EQUITY_INJECTION_PCT == 20.0
    assert "| 1.25 or more |" in sections["NSP-4.1"][1] and policy_startup.STARTUP_DSCR_MINIMUM == 1.25
    assert "at least 3 months" in sections["NSP-5.1"][1] and policy_startup.RESERVE_MONTHS_REQUIRED == 3
    assert "at least 24 months" in sections["NSP-2.1"][1] and policy_startup.MIN_EXPERIENCE_MONTHS == 24


def test_programs_stay_out_of_the_rag_corpus() -> None:
    rag_ids = {chunk.chunk_id for chunk in load_guideline_chunks()}
    assert not any(section_id.startswith("NSP-") for section_id in rag_ids)
    corpus = load_corpus(include_regulatory=False)
    assert all(source.path.startswith("corpus/guidelines/") for source in corpus.files)
    assert STARTUP_PROGRAM_DOC_ID not in corpus.chunk_counts()


def test_evidence_sections_include_guidelines_and_programs() -> None:
    sections = guideline_sections()
    assert "NBC-4.2" in sections and "HM-8.2" in sections and "NSP-4.1" in sections
    assert sections["NSP-4.1"][0] == "NSP-4.1 Projected DSCR"
    assert citable_section_ids() == set(sections)


def test_corpus_trigger_coverage_is_unchanged_by_the_program() -> None:
    # The Phase 5 trigger-coverage metric counts R-NBC and R-HM triggers only; the program adds none of those.
    triggers = corpus_trigger_ids()
    assert len(triggers) == 31 and not any(trigger.startswith("R-NSP-") for trigger in triggers)


def test_claim_checker_treats_program_ids_as_ids_not_numbers() -> None:
    assert numbers_in("Projected DSCR 1.31 [NSP-4.1] (R-NSP-06, NSP-9.2)") == ["1.31"]


def test_sig_validator_accepts_program_citations_from_the_citable_set() -> None:
    allowed = citable_section_ids()
    assert validate("The plan {{assert:R-NSP-06}} [NSP-4.1].", set(), {"R-NSP-06"}, [], allowed) == []
    violations = validate("See the program [NSP-99.1].", set(), set(), [], allowed)
    assert [violation.code for violation in violations] == ["V9_UNKNOWN_CITATION"]


def test_sig_numeric_check_finds_program_numbers_in_the_citable_sources() -> None:
    sentence = "The projected DSCR must be at least 1.25 [NSP-4.1]."
    [guideline_only] = check_numbers(sentence, section_texts()).mentions
    assert guideline_only.status == "untraceable"  # the mutation-test sources stay guideline-only on purpose
    [mention] = check_numbers(sentence, citable_section_texts()).mentions
    assert mention.matched_source == "NSP-4.1" and mention.status != "untraceable"
    assert set(citable_section_texts()) == citable_section_ids()


def test_mcp_guideline_store_serves_program_sections_when_asked() -> None:
    default = GuidelineStore()
    assert sorted(default.documents) == ["HM", "NBC"]  # the Phase 3 default is unchanged
    with pytest.raises(UnknownSectionError):
        default.get_section("NSP-4.1")
    store = GuidelineStore(programs_dir=PROGRAMS_DIR)
    assert sorted(store.documents) == ["HM", "NBC", "NSP"]
    section = store.get_section("nsp-4.1")
    assert section.section_id == "NSP-4.1" and section.citation == "[NSP-4.1]"
    assert section.source_file == "corpus/programs/northwind_startup_program.md" and section.synthetic
    assert section.resource_uri == "guidelines://NSP/NSP-4.1"
    assert [item for item in store.section_ids() if item.startswith("NSP-")] == EXPECTED_SECTIONS
    assert "SYNTHETIC" in store.section_markdown("NSP-9.1")


def test_the_mcp_cli_serves_the_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    built = {}

    class StopServer(Exception):
        """Raised by the fake server so main() returns before serving."""

    class FakeServer:
        def run(self, *args: object, **kwargs: object) -> None:
            raise StopServer

    def fake_build_server(dependencies: object, log_level: str = "WARNING") -> FakeServer:
        built["store"] = dependencies.guideline_store  # type: ignore[attr-defined]
        return FakeServer()

    monkeypatch.setattr(cli, "build_server", fake_build_server)
    with pytest.raises(StopServer):
        cli.main([])
    assert "NSP" in built["store"].documents and "NBC" in built["store"].documents
