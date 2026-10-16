"""build_packet(): one SYNTHETIC packet PDF and its PacketTruth; generate_packets(): a whole seeded set.

A packet is built in this order (all randomness from one random.Random and one Faker seeded with the packet seed):
choose the problems, build the case, decide which documents look scanned, render the documents, then record the
ground truth: pages, documents, the oracle extraction and its FieldTruth rows, the injected problems, the
embedded instructions, application_as_documented and application_latent.

application_latent is application_as_documented with every problem's latent_substitutions applied
(docintel/levers.py), problem by problem in problem-id order, except that substitutions which insert loss-run
years run last (they would shift the year indexes other substitutions use).

draw=False (the --json-only mode) lays the documents out without drawing or scanning: the truth is the same except
pdf_file and pdf_sha256, which are empty strings.
"""

import random

from uwcopilot.agent.applications import parse_application
from uwcopilot.agent.policy_credit import required_documents as credit_required_documents
from uwcopilot.agent.policy_insurance import required_documents as insurance_required_documents
from uwcopilot.docintel.levers import apply_substitutions
from uwcopilot.docintel.schemas import PacketExtraction, flatten_fields
from uwcopilot.docintel.synthetic.common import seeded_faker
from uwcopilot.docintel.synthetic.credit_application import application_as_documented as credit_as_documented
from uwcopilot.docintel.synthetic.credit_generate import generate_credit_case
from uwcopilot.docintel.synthetic.credit_packet import SCAN_RATES as CREDIT_SCAN_RATES
from uwcopilot.docintel.synthetic.credit_packet import credit_documents, loan_packet, mark_scanned
from uwcopilot.docintel.synthetic.credit_records import credit_record
from uwcopilot.docintel.synthetic.documents import (
    RenderedDocument,
    doc_id_map,
    document_truths,
    injection_page,
    merge_pdf,
    page_truths,
    render_documents,
    sha256_hex,
)
from uwcopilot.docintel.synthetic.insurance_application import insurance_as_documented
from uwcopilot.docintel.synthetic.insurance_generate import generate_insurance_case
from uwcopilot.docintel.synthetic.insurance_packet import SCAN_RATES as INSURANCE_SCAN_RATES
from uwcopilot.docintel.synthetic.insurance_packet import insurance_documents, insurance_submission
from uwcopilot.docintel.synthetic.insurance_records import insurance_record
from uwcopilot.docintel.synthetic.problems import ProblemPlan, choose_problems, make_problem, scenario_name
from uwcopilot.docintel.truth import FieldTruth, InjectedProblem, InjectionTruth, PacketTruth

PDF_FILE = "packet.pdf"


def validated(application: dict) -> dict:
    """The application JSON after validation by the agent's models (normalized types, ground_truth None)."""
    return parse_application(application).model_dump(mode="json")


def latent_application(application: dict, problems: list[InjectedProblem]) -> dict:
    """application_as_documented with every latent substitution applied (see the module docstring)."""
    ordered = sorted(problems, key=lambda problem: "+loss_runs.years" in problem.latent_substitutions)
    latent = application
    for problem in ordered:
        latent = apply_substitutions(latent, problem.latent_substitutions)
    return validated(latent)


def field_truths(oracle: PacketExtraction) -> list[FieldTruth]:
    """The flattened oracle extraction as FieldTruth rows (every row has a page)."""
    rows = []
    for item in flatten_fields(oracle):
        if item.doc_id is None or item.page is None:
            raise ValueError(f"oracle field {item.field} has no citation")
        rows.append(
            FieldTruth(field=item.field, key=item.key, value=item.value, doc_id=item.doc_id, page_number=item.page)
        )
    return rows


def missing_required(application: dict) -> list[str]:
    """Checklist codes the reference policy requires that the packet does not provide."""
    parsed = parse_application(application)
    if application["line"] == "credit":
        required = credit_required_documents(parsed)
    else:
        required = insurance_required_documents(parsed)
    return [code for code in required if code not in application["documents_provided"]]


def injection_truths(
    rendered: list[RenderedDocument], plans: list[ProblemPlan], problems: list[InjectedProblem]
) -> list[InjectionTruth]:
    """InjectionTruth for every prompt_injection problem (document, page, text, technique)."""
    truths = []
    for plan, problem in zip(plans, problems, strict=True):
        if plan.problem_type != "prompt_injection":
            continue
        doc_id = problem.doc_ids[0]
        text = plan.details["text"]
        page = injection_page(rendered, doc_id, text)
        truths.append(InjectionTruth(doc_id=doc_id, page_number=page, text=text, technique=plan.details["technique"]))
    return truths


def assemble(
    packet_id: str,
    line: str,
    split: str,
    seed: int,
    plans: list[ProblemPlan],
    rendered: list[RenderedDocument],
    oracle: PacketExtraction,
    problems: list[InjectedProblem],
    application: dict,
    draw: bool,
) -> tuple[bytes | None, PacketTruth]:
    """The merged PDF (when drawn) and the validated PacketTruth."""
    pdf = merge_pdf(rendered) if draw else None
    truth = PacketTruth(
        packet_id=packet_id,
        line=line,
        split=split,
        seed=seed,
        scenario=scenario_name(plans),
        pdf_file=PDF_FILE if pdf is not None else "",
        pdf_sha256=sha256_hex(pdf) if pdf is not None else "",
        documents=document_truths(rendered),
        pages=page_truths(rendered),
        fields=field_truths(oracle),
        problems=problems,
        injections=injection_truths(rendered, plans, problems),
        oracle_extraction=oracle.model_dump(mode="json"),
        application_as_documented=application,
        application_latent=latent_application(application, problems),
        missing_required_documents=missing_required(application),
    )
    return pdf, truth


def build_credit_packet(packet_id: str, seed: int, split: str, draw: bool) -> tuple[bytes | None, PacketTruth]:
    """One SYNTHETIC loan packet."""
    rng = random.Random(seed)
    fake = seeded_faker(seed)
    plans = choose_problems(rng, "credit")
    case = generate_credit_case(rng, fake, int(packet_id[-3:]), plans)
    specs = credit_documents(case)
    mark_scanned(rng, specs, CREDIT_SCAN_RATES)
    rendered = render_documents(specs, draw, seed)
    doc_ids = doc_id_map(rendered)
    application = validated(credit_as_documented(case))
    problems = [
        make_problem(index, plan, credit_record(case, plan, application, doc_ids))
        for index, plan in enumerate(plans, start=1)
    ]
    oracle = loan_packet(packet_id, rendered)
    return assemble(packet_id, "credit", split, seed, plans, rendered, oracle, problems, application, draw)


def build_insurance_packet(packet_id: str, seed: int, split: str, draw: bool) -> tuple[bytes | None, PacketTruth]:
    """One SYNTHETIC insurance submission packet."""
    rng = random.Random(seed)
    fake = seeded_faker(seed)
    plans = choose_problems(rng, "insurance")
    case = generate_insurance_case(rng, fake, int(packet_id[-3:]), plans)
    specs = insurance_documents(case)
    mark_scanned(rng, specs, INSURANCE_SCAN_RATES)
    rendered = render_documents(specs, draw, seed)
    doc_ids = doc_id_map(rendered)
    application = validated(insurance_as_documented(case))
    problems = [
        make_problem(index, plan, insurance_record(case, plan, application, doc_ids))
        for index, plan in enumerate(plans, start=1)
    ]
    oracle = insurance_submission(packet_id, rendered)
    return assemble(packet_id, "insurance", split, seed, plans, rendered, oracle, problems, application, draw)


def build_packet(
    packet_id: str, line: str, seed: int, split: str, draw: bool = True
) -> tuple[bytes | None, PacketTruth]:
    """One SYNTHETIC packet: (PDF bytes or None when draw=False, PacketTruth). seed is the packet's own seed."""
    if line == "credit":
        return build_credit_packet(packet_id, seed, split, draw)
    return build_insurance_packet(packet_id, seed, split, draw)
