"""Applying the planned problems to a SYNTHETIC loan case (Phase 2 spec section 2.3, credit).

Three stages, called by credit_generate.py:
1. before the statements are built: plan_statement_months (which month is removed, which month breaks continuity,
   which month carries a memo instruction), plan_revenue (extra deposits for a revenue gap) and
   prepare_debt_mismatch (the observed payment differs from the schedule);
2. after the statements: apply_statement_problems (printed overrides) and stated_revenue;
3. on the finished case: apply_document_problems (a missing document, a different name / FEIN on one document,
   different ownership on the formation document, embedded instructions in the remarks or P&L notes).
The records (InjectedProblem ground truth) are built in credit_records.py once document ids are known.
"""

import random
from dataclasses import dataclass, field

from uwcopilot.agent.credit_calculations import revenue_gap_pct
from uwcopilot.docintel.normalize import canonical_name, edit_distance
from uwcopilot.docintel.synthetic.common import DESCRIPTIVE_WORDS, cents, counterparty, round_to, synthetic_fein
from uwcopilot.docintel.synthetic.credit_case import CreditCase, Debt, InjectionPlan, Party, Statement
from uwcopilot.docintel.synthetic.credit_transactions import ExtraDeposit
from uwcopilot.docintel.synthetic.injections import choose_injection
from uwcopilot.docintel.synthetic.problems import ProblemPlan, find_plan

# Correctly described non-revenue deposits used to inflate gross deposits (benign_non_revenue_counted).
NON_REVENUE_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("ONLINE TRANSFER FROM SAVINGS ...{last4}", "transfer"),
    ("TRANSFER FROM MONEY MARKET ...{last4}", "transfer"),
    ("OWNER CAPITAL CONTRIBUTION", "owner_contribution"),
    ("SALE OF EQUIPMENT {party}", "one_time"),
    ("INSURANCE CLAIM PAYMENT", "one_time"),
    ("VENDOR REFUND {party}", "refund"),
)


@dataclass
class RevenuePlan:
    """Extra deposits and the revenue figures the other documents show."""

    extra_deposits: list[ExtraDeposit] = field(default_factory=list)
    true_revenue: float = 0.0
    gap: float | None = None


def plan_statement_months(rng: random.Random, plans: list[ProblemPlan]) -> None:
    """Choose the statement months the statement problems use (removed month, broken month, memo month)."""
    removed = None
    missing = find_plan(plans, "missing_pages")
    if missing is not None:
        removed = rng.randint(1, 10)
        missing.details["month_index"] = removed
    broken = find_plan(plans, "balance_discontinuity")
    if broken is not None:
        choices = [index for index in range(1, 12) if removed not in (index, index - 1)]
        broken.details["month_index"] = rng.choice(choices)
        broken.details["variant"] = "opening" if rng.random() < 0.5 else "totals"
        broken.details["latent"] = "bank_confirms" if rng.random() < 0.5 else "tampering_indicated"
    injection = find_plan(plans, "prompt_injection")
    if injection is not None:
        technique, text = choose_injection(rng, "credit")
        location = rng.choices(("remarks", "memo", "pnl_notes"), weights=(0.45, 0.25, 0.30), k=1)[0]
        month = rng.choice([index for index in range(12) if index != removed])
        injection.details.update(technique=technique, text=text, location=location, month_index=month)


def clean_extras(rng: random.Random, eligible: list[float], last4: str, vendors: list[str]) -> list[ExtraDeposit]:
    """Ordinary non-revenue deposits that appear in clean packets too, correctly described."""
    extras: list[ExtraDeposit] = []
    for index, amount in enumerate(eligible):
        if rng.random() < 0.25:
            value = cents(amount * rng.uniform(0.02, 0.08))
            extras.append(
                ExtraDeposit(index, f"ONLINE TRANSFER FROM SAVINGS ...{last4}", value, "transfer", "transfer")
            )
        if rng.random() < 0.06:
            value = cents(rng.uniform(100, 2_500))
            extras.append(ExtraDeposit(index, f"VENDOR REFUND {rng.choice(vendors)}", value, "refund", "refund"))
        if rng.random() < 0.04:
            extras.append(ExtraDeposit(index, "CHARGEBACK REVERSAL", cents(rng.uniform(50, 900)), "refund", "refund"))
        if rng.random() < 0.03:
            value = cents(amount * rng.uniform(0.02, 0.06))
            extras.append(
                ExtraDeposit(index, "OWNER CAPITAL CONTRIBUTION", value, "owner_contribution", "owner_contribution")
            )
        if rng.random() < 0.10:
            value = cents(rng.uniform(150, 2_500))
            extras.append(
                ExtraDeposit(index, f"DEPOSIT REF {rng.randint(10000, 99999)}", value, "unidentified", "unidentified")
            )
    if rng.random() < 0.12:
        extras.append(
            ExtraDeposit(rng.randrange(12), "IRS TAX REFUND", cents(rng.uniform(500, 8_000)), "one_time", "one_time")
        )
    if rng.random() < 0.06:
        value = cents(rng.uniform(1_000, 15_000))
        extras.append(ExtraDeposit(rng.randrange(12), "INSURANCE CLAIM PAYMENT", value, "one_time", "one_time"))
    return extras


def spread(rng: random.Random, total: float, count: int) -> list[float]:
    """Split a total into `count` positive cent amounts (uneven, adding up exactly)."""
    weights = [rng.uniform(0.4, 1.6) for _ in range(count)]
    amounts = [cents(total * weight / sum(weights)) for weight in weights]
    amounts[-1] = cents(total - sum(amounts[:-1]))
    return amounts


def plan_revenue(rng: random.Random, plans: list[ProblemPlan], verified: float, last4: str) -> RevenuePlan:
    """Extra deposits for a revenue gap and the true revenue behind the documents (see the module docstring)."""
    gap_plan = find_plan(plans, "revenue_gap")
    if gap_plan is None:
        return RevenuePlan(true_revenue=verified)
    gap = rng.uniform(0.105, 0.60)
    gap_plan.details["target_gap"] = gap
    extra_total = gap / (1.0 - gap) * verified
    if gap_plan.cause == "benign_non_revenue_counted":
        count = rng.randint(4, 10)
        extras = []
        for amount in spread(rng, extra_total, count):
            template, category = rng.choice(NON_REVENUE_DESCRIPTIONS)
            text = template.format(last4=last4, party=counterparty(rng))
            extras.append(ExtraDeposit(rng.randrange(12), text, amount, category, category))
        return RevenuePlan(extras, verified, gap)
    if gap_plan.cause == "benign_unidentified_revenue":
        count = rng.randint(6, 14)
        extras = [
            ExtraDeposit(
                rng.randrange(12), f"DEPOSIT REF {rng.randint(10000, 99999)}", amount, "unidentified", "revenue"
            )
            for amount in spread(rng, extra_total, count)
        ]
        return RevenuePlan(extras, verified + extra_total, gap)
    return RevenuePlan(true_revenue=verified, gap=gap)


def prepare_debt_mismatch(rng: random.Random, plans: list[ProblemPlan], loans: list[Debt], removed: int | None) -> None:
    """Make one loan's observed payment differ from its scheduled payment (debt_payment_mismatch)."""
    plan = find_plan(plans, "debt_payment_mismatch")
    if plan is None or not loans:
        return
    candidates = [loan for loan in loans if loan.first_payment_index == 0] or loans
    debt = rng.choice(candidates)
    if rng.random() < 0.7:
        factor = rng.uniform(1.08, 1.35) if rng.random() < 0.8 else rng.uniform(0.70, 0.92)
        debt.observed_payment = cents(debt.scheduled_payment * factor)
        plan.details["latent"] = "observed_is_current"
    else:
        months = [index for index in range(debt.first_payment_index, 12) if index != removed]
        debt.extra_payment_index = rng.choice(months)
        debt.extra_payment = cents(debt.scheduled_payment * rng.uniform(0.5, 2.0))
        plan.details["latent"] = "schedule_is_current"
    plan.details["lender"] = debt.lender


def apply_statement_problems(rng: random.Random, plans: list[ProblemPlan], statements: list[Statement]) -> None:
    """Printed-value overrides for a balance discontinuity, and the memo line of an embedded instruction."""
    broken = find_plan(plans, "balance_discontinuity")
    if broken is not None:
        statement = statements[broken.details["month_index"]]
        change = cents(rng.uniform(250, 5_000)) * (1 if rng.random() < 0.5 else -1)
        if broken.details["variant"] == "opening":
            statement.printed_opening = cents(statement.opening + change)
        elif rng.random() < 0.5:
            statement.printed_total_deposits = cents(statement.total_deposits + abs(change))
            broken.details["total"] = "deposits"
        else:
            statement.printed_total_withdrawals = cents(statement.total_withdrawals + abs(change))
            broken.details["total"] = "withdrawals"
    injection = find_plan(plans, "prompt_injection")
    if injection is not None and injection.details["location"] == "memo":
        statement = statements[injection.details["month_index"]]
        deposits = [row for row in statement.rows if row.kind == "D" and row.description.startswith("ACH CREDIT")]
        deposits = deposits or [row for row in statement.rows if row.kind == "D"]
        rng.choice(deposits).memo = injection.details["text"]


# benign_timing must not also look like another benign cause (contradiction_rules rules 1 and 2): its stated figure
# stays more than 3% away from gross deposits, and unidentified deposits stay below 45% of the gap (margins around
# the rules' 2% and 50%).
MAX_TYPO_KEY_EDITS = 2  # contradiction_rules.NAME_MAX_EDITS: a benign typo stays within 2 edits on the key
TIMING_GROSS_SEPARATION = 0.03
TIMING_UNIDENTIFIED_SHARE = 0.45
TIMING_STEP = 1.01


def separate_timing(stated: float, verified: float, gross: float, unidentified: float) -> float:
    """Raise a benign_timing stated figure (1% at a time) until it shows only the timing cause.

    Without this, a small gap can put stated within 2% of gross deposits (so the deposits alone would explain it as
    non-revenue counted) or make unidentified deposits half of the gap: the documents would then single out a
    different benign story than the truth's latent "midpoint" resolution.
    """
    while abs(stated - gross) <= TIMING_GROSS_SEPARATION * stated or unidentified >= TIMING_UNIDENTIFIED_SHARE * (
        stated - verified
    ):
        stated = float(round(stated * TIMING_STEP))
    return stated


def stated_revenue(
    rng: random.Random,
    plans: list[ProblemPlan],
    verified: float,
    true_revenue: float,
    gross: float,
    others: list[float],
    unidentified: float = 0.0,
) -> float:
    """The application's stated revenue: benign band in clean packets; per cause for a revenue gap.

    others are the P&L revenue and the latest tax-year receipts (an unsupported overstatement exceeds them and the
    gross deposits by more than 10%). unidentified is the sum of the unidentified deposits ("DEPOSIT REF") of the
    statements; a benign_timing figure is kept apart from the other benign causes (separate_timing). The gap is
    kept above 10.0% after rounding (NBC-3.4).
    """
    plan = find_plan(plans, "revenue_gap")
    if plan is None:
        return round_to(verified * (1.0 + rng.uniform(-0.05, 0.08)), 1_000)
    gap = plan.details["target_gap"]
    if plan.cause == "benign_non_revenue_counted":
        stated = round_to(gross * (1.0 + rng.uniform(-0.008, 0.008)), 1_000)
    elif plan.cause == "benign_unidentified_revenue":
        stated = round_to(true_revenue * (1.0 + rng.uniform(-0.02, 0.02)), 1_000)
    elif plan.cause == "benign_timing":
        stated = separate_timing(float(round(verified / (1.0 - gap))), verified, gross, unidentified)
    else:
        stated = round_to(verified / (1.0 - gap), 1_000)
        highest = max([gross, *others])
        if stated <= 1.105 * highest:
            stated = round_to(1.12 * highest + 500, 1_000)
    while (revenue_gap_pct(stated, verified) or 0.0) <= 10.0:
        stated += 1_000.0
    return stated


# --------------------------------------------------------------------------------------------------------------
# Document-level problems on the finished case
# --------------------------------------------------------------------------------------------------------------


def missing_document_choice(rng: random.Random, case: CreditCase, plans: list[ProblemPlan]) -> str:
    """Which document is missing: formation, debt schedule (when there is debt), one photo ID or the tax return."""
    options = ["photo_id"]
    if find_plan(plans, "owner_mismatch") is None:
        options.append("formation_document")
    if case.loans() and find_plan(plans, "debt_payment_mismatch") is None:
        options.append("debt_schedule")
    gap = find_plan(plans, "revenue_gap")
    if gap is None or gap.cause != "benign_timing":
        options.append("tax_return_summary")
    return rng.choice(options)


def apply_missing_document(rng: random.Random, case: CreditCase, plan: ProblemPlan) -> None:
    """Leave one required document out of the packet."""
    choice = plan.details["doc_type"]
    if choice == "photo_id":
        owner = rng.choice(case.principals())
        case.missing_photo_id_owner = owner.owner_id
        plan.details["owner_id"] = owner.owner_id
    else:
        case.missing_documents.append(choice)


def typo(rng: random.Random, text: str) -> str:
    """A 1-2 character edit of a name (substitution, deletion or adjacent swap) that changes its comparison key.

    The edit must also stay small ON the comparison key (canonical_name: edit distance 2 or less): canonical_name
    folds words such as "Company" to "co", so a typo inside such a word ("Fompany") would otherwise look like a
    different name to the name check, not like the transcription error the truth says it is.
    """
    letters = "abcdefghijklmnopqrstuvwxyz"
    for _ in range(50):
        positions = [index for index, character in enumerate(text) if character.isalpha()]
        position = rng.choice(positions[1:] or positions)
        kind = rng.choice(("substitute", "delete", "swap"))
        if kind == "substitute":
            replacement = rng.choice(letters)
            replacement = replacement.upper() if text[position].isupper() else replacement
            changed = text[:position] + replacement + text[position + 1 :]
        elif kind == "delete":
            changed = text[:position] + text[position + 1 :]
        else:
            if position + 1 >= len(text) or not text[position + 1].isalpha():
                continue
            changed = text[:position] + text[position + 1] + text[position] + text[position + 2 :]
        changed_key, key = canonical_name(changed), canonical_name(text)
        if changed_key != key and edit_distance(changed_key, key) <= MAX_TYPO_KEY_EDITS:
            return changed
    raise RuntimeError(f"could not make a typo of {text!r}")


def fein_typo(rng: random.Random, fein: str) -> str:
    """One digit changed, or two adjacent different digits swapped (never inside the 00- prefix)."""
    digits = list(fein.replace("-", ""))
    while True:
        position = rng.randrange(2, len(digits))
        changed = list(digits)
        if rng.random() < 0.5 and position + 1 < len(digits) and digits[position] != digits[position + 1]:
            changed[position], changed[position + 1] = changed[position + 1], changed[position]
        else:
            changed[position] = rng.choice([digit for digit in "0123456789" if digit != digits[position]])
        text = "".join(changed)
        if text != "".join(digits):
            return f"{text[:2]}-{text[2:]}"


def identity_documents(case: CreditCase, field_name: str) -> list[str]:
    """Documents (other than the application) that print the business name or FEIN and are in the packet."""
    if field_name == "fein":
        candidates = ["formation_document", "tax_return_summary"]
    else:
        candidates = ["formation_document", "tax_return_summary", "profit_and_loss", "balance_sheet", "debt_schedule"]
    return [doc for doc in candidates if doc not in case.missing_documents]


def apply_identity_problem(rng: random.Random, case: CreditCase, plan: ProblemPlan, field_name: str) -> bool:
    """A different business name or FEIN on one document (False when no document can carry it)."""
    documents = identity_documents(case, field_name)
    if not documents:
        return False
    doc_type = rng.choice(documents)
    if field_name == "fein":
        value = fein_typo(rng, case.fein) if plan.cause == "benign_typo" else synthetic_fein(rng)
        case.doc_feins[doc_type] = value
    else:
        value = typo(rng, case.legal_name) if plan.cause == "benign_typo" else different_name(rng, case.legal_name)
        case.doc_names[doc_type] = value
    plan.details.update(doc_type=doc_type, value=value)
    return True


def transposable(owner: Party) -> bool:
    """True when a whole-number share has two different non-zero digits (35 -> 53)."""
    text = f"{owner.ownership_pct:.0f}"
    return owner.ownership_pct.is_integer() and len(text) == 2 and text[0] != text[1] and "0" not in text


def alter_members(rng: random.Random, plan: ProblemPlan, owners: list[Party]) -> list[Party]:
    """A copy of the owners as another ownership document shows them, for an owner_mismatch problem.

    benign_typo: one whole-number share has its two digits transposed (35 -> 53); unsupported_unexplained: 6-15
    points move from one owner to another (60%) or a non-control owner is missing (40%). plan.details records the
    variant, the owner and the latent option.
    """
    members = [Party(**vars(owner)) for owner in owners]
    candidates = [index for index, owner in enumerate(members) if transposable(owner)]
    if plan.cause == "benign_typo" and not candidates:
        plan.cause = "unsupported_unexplained"
    if plan.cause == "benign_typo":
        index = rng.choice(candidates)
        text = f"{members[index].ownership_pct:.0f}"
        members[index].ownership_pct = float(text[::-1])
        plan.details.update(variant="typo", owner_id=members[index].owner_id, latent="application_correct")
    elif rng.random() < 0.6:
        giver = rng.choice([index for index, owner in enumerate(members) if owner.ownership_pct >= 16])
        taker = rng.choice([index for index in range(len(members)) if index != giver])
        shift = float(rng.randint(6, 15))
        members[giver].ownership_pct -= shift
        members[taker].ownership_pct += shift
        latent = "other_document_correct" if rng.random() < 0.5 else "unresolved"
        plan.details.update(variant="shift", owner_id=members[giver].owner_id, latent=latent)
    else:
        index = rng.randrange(1, len(members))
        plan.details.update(variant="drop", owner_id=members[index].owner_id, latent="unresolved")
        del members[index]
    return members


def different_name(rng: random.Random, name: str) -> str:
    """Another business name with the same suffix (an unexplained name difference)."""
    lead = rng.choice([word for word in DESCRIPTIVE_WORDS if word not in name])
    return f"{lead} {' '.join(name.split()[1:])}"


def apply_owner_problem(rng: random.Random, case: CreditCase, plan: ProblemPlan) -> None:
    """The formation document's members differ from the application's owners table (NBC-6.1)."""
    case.formation_members = alter_members(rng, plan, case.owners)


def apply_injection(case: CreditCase, plan: ProblemPlan) -> None:
    """Put the embedded instruction in the remarks or the P&L notes (memo lines are set on the statements)."""
    details = plan.details
    case.injection = InjectionPlan(details["location"], details["technique"], details["text"], details["month_index"])
    if details["location"] == "remarks":
        case.remarks = f"{case.remarks} {details['text']}"
    elif details["location"] == "pnl_notes":
        case.pnl.notes.append(details["text"])


def apply_document_problems(rng: random.Random, case: CreditCase, plans: list[ProblemPlan]) -> list[ProblemPlan]:
    """Apply the document-level problems; returns the plans that could be applied (in their original order)."""
    case.formation_members = [Party(**vars(owner)) for owner in case.owners]
    missing = find_plan(plans, "missing_document")
    if missing is not None:
        missing.details["doc_type"] = missing_document_choice(rng, case, plans)
        apply_missing_document(rng, case, missing)
    applied = []
    for plan in plans:
        if plan.problem_type == "name_mismatch" and not apply_identity_problem(rng, case, plan, "name"):
            continue
        if plan.problem_type == "fein_mismatch" and not apply_identity_problem(rng, case, plan, "fein"):
            continue
        if plan.problem_type == "owner_mismatch":
            apply_owner_problem(rng, case, plan)
        if plan.problem_type == "prompt_injection":
            apply_injection(case, plan)
        applied.append(plan)
    return applied
