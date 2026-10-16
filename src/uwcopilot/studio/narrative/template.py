"""The deterministic TEMPLATE narrative: a slot-only draft built from the ledger with fixed sentences.

It is the default writer (no model, no network, the same text for the same ledger) and the fallback whenever an LLM
draft breaks the grammar twice or the model cannot be reached. It follows the same rules as an LLM draft: prose and
{{claim:ID}} / {{assert:ID}} slots only, [NSP-x.y] citations, no digits or number words, no comparative outside a
sentence with an assert slot, no decision wording. Every sentence is chosen from what the ledger holds (a SiteIQ
paragraph only when there is a SiteIQ result, a rate-stress sentence only for a variable rate, ...), and every
finding (an assertion that is False) and every open claim (UNKNOWN or CONFLICTING) is mentioned, so the validator's
omission check (V6) passes by construction. The words around the slots come from prose.py.

Paragraphs: the plan and the program's eligibility checks; the site; the simulation (every probability labelled
"simulated, based on stated assumptions"); the program's coverage checks; the top drivers; open items; the
path-to-targets search; a closing reminder that the lender runs every rule itself.
"""

from uwcopilot.evidence.ledger_models import Assertion, Ledger
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.prose import (
    article,
    business_noun,
    claim_phrase,
    demand_unit_phrase,
    input_phrase,
    lever_phrase,
    metro_phrase,
)

TEMPLATE_ID = "founder_narrative_template_v1"
TOP_DRIVERS = 3
PATH_OUTCOMES: tuple[str, ...] = ("p_dscr_pct", "p_stressed_pct", "p_cash_negative_pct")
DRIVER_OPENERS: tuple[str, ...] = (
    "The input that moves second-year net income the most is",
    "The next is",
    "After that comes",
)
CLOSING = (
    "These checks read the plan against a SYNTHETIC startup program; a suggested change is a plan choice, not a fact, "
    "and the lender runs every rule itself and makes its own decision."
)


def c(claim_id: str) -> str:
    """A claim slot."""
    return "{{claim:" + claim_id + "}}"


def a(assertion_id: str) -> str:
    """An assert slot."""
    return "{{assert:" + assertion_id + "}}"


def cite(assertion: Assertion | None) -> str:
    """'[NSP-4.1]' or '[NSP-2.3, NBC-2.3]' for an assertion's sections ('' when it has none)."""
    if assertion is None or not assertion.section_ids:
        return ""
    return " [" + ", ".join(assertion.section_ids) + "]"


class TemplateWriter:
    """Writes the template draft of one ledger."""

    def __init__(self, ledger: Ledger) -> None:
        """Keep the ledger; `mentioned` collects every claim id a sentence has used."""
        self.ledger = ledger
        self.mentioned: set[str] = set()

    def has(self, claim_id: str) -> bool:
        """True when the ledger has this claim."""
        return self.ledger.claim(claim_id) is not None

    def text(self, claim_id: str, default: str = "") -> str:
        """The text value of a claim (default when it is missing or empty)."""
        claim = self.ledger.claim(claim_id)
        return str(claim.value) if claim is not None and claim.value is not None else default

    def slot(self, claim_id: str) -> str:
        """A claim slot, remembered as mentioned."""
        self.mentioned.add(claim_id)
        return c(claim_id)

    def plan(self) -> list[str]:
        """The plan, the money and the eligibility checks."""
        business = business_noun(self.text(ids.PLAN_BUSINESS_TYPE))
        metro = metro_phrase(self.text(ids.PLAN_METRO))
        label = "SYNTHETIC " if self.ledger.data_label == "SYNTHETIC" else ""
        rate_type = self.text(ids.PLAN_RATE_TYPE, "fixed")
        amount = self.ledger.assertion(ids.A_AMOUNT)
        sentences = [
            f"This {label}founder plan is for {article(business)} {business} in {metro}.",
            f"It asks for a {rate_type}-rate startup term loan of {self.slot(ids.FOUNDER_LOAN)}, which "
            f"{a(ids.A_AMOUNT)}, over {self.slot(ids.FOUNDER_TERM)}, which {a(ids.A_TERM)}{cite(amount)}.",
        ]
        if self.has(ids.CALC_OTHER_DEBT_AMOUNT):
            sentences.append(
                f"The plan also lists other debt of {self.slot(ids.CALC_OTHER_DEBT_AMOUNT)} with monthly payments of "
                f"{self.slot(ids.CALC_OTHER_DEBT_MONTHLY)}."
            )
        equity = self.ledger.assertion(ids.A_EQUITY)
        sentences += [
            f"Owner cash equity is {self.slot(ids.FOUNDER_EQUITY)} of a total project cost of "
            f"{self.slot(ids.CALC_PROJECT_COST)}, an equity injection of {self.slot(ids.CALC_EQUITY_PCT)} that "
            f"{a(ids.A_EQUITY)} of {self.slot(ids.PROGRAM_MIN_EQUITY)}{cite(equity)}.",
            f"The owner cash equity {a(ids.A_EQUITY_DOCS)}{cite(self.ledger.assertion(ids.A_EQUITY_DOCS))}.",
        ]
        capex = ids.FOUNDER_CAPEX if self.has(ids.FOUNDER_CAPEX) else ids.ASSUME_CAPEX
        if self.has(capex):
            sentences.append(
                f"The build-out and equipment cost in the plan is {self.slot(capex)}, which leaves a cash reserve "
                f"at opening of {self.slot(ids.CALC_CASH_RESERVE)}."
            )
        sentences += [
            f"The business type {a(ids.A_INDUSTRY)}{cite(self.ledger.assertion(ids.A_INDUSTRY))}.",
            f"The most relevant owner experience of {self.slot(ids.FOUNDER_EXPERIENCE)} {a(ids.A_EXPERIENCE)}"
            f"{cite(self.ledger.assertion(ids.A_EXPERIENCE))}, and the lowest owner credit score of "
            f"{self.slot(ids.FOUNDER_CREDIT_SCORE)} {a(ids.A_CREDIT)}{cite(self.ledger.assertion(ids.A_CREDIT))}.",
            f"The loan {a(ids.A_COLLATERAL)}{cite(self.ledger.assertion(ids.A_COLLATERAL))}.",
        ]
        return sentences

    def site(self) -> list[str]:
        """SiteIQ's scores and the site's demand range."""
        sentences = []
        if self.has(ids.SITE_OVERALL):
            sentences.append(
                f"SiteIQ gives the chosen site an overall score of {self.slot(ids.SITE_OVERALL)}, with a data "
                f"coverage of {self.slot(ids.SITE_COVERAGE)}."
            )
        if self.has(ids.SITE_DEMAND_MODE):
            unit = demand_unit_phrase(self.text(ids.PLAN_DEMAND_UNIT, "units of demand"))
            sentences.append(
                f"Most likely demand at the site once ramp-up is over, in {unit}, is "
                f"{self.slot(ids.SITE_DEMAND_MODE)}, in a range from {self.slot('SITE.demand_low')} to "
                f"{self.slot('SITE.demand_high')}."
            )
        return sentences

    def measured(self, assertion_id: str) -> str | None:
        """The probability claim a target check compared (its first evidence id), or None without the check."""
        check = self.ledger.assertion(assertion_id)
        return check.evidence_ids[0] if check is not None and check.evidence_ids else None

    def simulation(self) -> list[str]:
        """The three simulated chances against the targets, the median second year and the break-even month."""
        sentences = ["Every chance in this paragraph is simulated, based on stated assumptions."]
        p_dscr = self.measured(ids.A_TARGET_DSCR)
        if p_dscr is not None:
            sentences.append(
                f"The simulated chance that the debt service coverage ratio in the second year reaches "
                f"{self.slot(ids.TARGET_DSCR_MIN)} is {self.slot(p_dscr)}, which {a(ids.A_TARGET_DSCR)} "
                f"of {self.slot(ids.TARGET_P_DSCR)}."
            )
        p_stressed = self.measured(ids.A_TARGET_STRESS)
        if p_stressed is not None:
            sentences.append(
                f"With the stress test of the targets, which cuts revenue by {self.slot(ids.TARGET_STRESS_SHARE)} "
                f"and net operating income by the same dollars, the simulated chance that the second-year ratio "
                f"reaches {self.slot(ids.TARGET_STRESSED_MIN)} is {self.slot(p_stressed)}, which "
                f"{a(ids.A_TARGET_STRESS)} of {self.slot(ids.TARGET_P_STRESSED)}."
            )
        p_cash = self.measured(ids.A_TARGET_CASH)
        if p_cash is not None:
            sentences.append(
                f"The simulated chance that the business runs out of cash within {self.slot(ids.TARGET_CASH_MONTHS)} "
                f"is {self.slot(p_cash)}, which {a(ids.A_TARGET_CASH)} of {self.slot(ids.TARGET_P_CASH)}."
            )
        year2 = {field: ids.year_id(2, field) for field in ("revenue_p50", "noi_p50", "noi_p10", "noi_p90")}
        if all(self.has(claim_id) for claim_id in year2.values()):
            sentences.append(
                f"In the median simulated future, second-year revenue is {self.slot(year2['revenue_p50'])} and net "
                f"operating income is {self.slot(year2['noi_p50'])}, with a low case of {self.slot(year2['noi_p10'])} "
                f"and a high case of {self.slot(year2['noi_p90'])} for net operating income."
            )
        sentences.append(self.break_even())
        return sentences

    def break_even(self) -> str:
        """The median break-even month, or that the median future does not break even in the horizon."""
        median = self.ledger.claim(ids.SIM_BREAK_EVEN_P50)
        if median is not None and median.unit == "MONTHS":
            return (
                f"In the median simulated future the business breaks even in month "
                f"{self.slot(ids.SIM_BREAK_EVEN_P50)}, counting from the opening month, and "
                f"{self.slot(ids.SIM_NOT_BREAK_EVEN)} of the simulated futures do not break even within the "
                "simulated horizon."
            )
        return (
            "In the median simulated future the business does not break even within the simulated horizon, and "
            f"{self.slot(ids.SIM_NOT_BREAK_EVEN)} of the simulated futures never do."
        )

    def coverage(self) -> list[str]:
        """The program's projected DSCR, the stress tests and the reserve."""
        sentences = [
            f"With the program formula, the projected debt service coverage ratio for the second year is "
            f"{self.slot(ids.CALC_PROJECTED_DSCR)}, which {a(ids.A_PROJECTED_DSCR)} of "
            f"{self.slot(ids.PROGRAM_MIN_DSCR)}{cite(self.ledger.assertion(ids.A_PROJECTED_DSCR))}.",
            f"With the lender's revenue stress, which cuts revenue by {self.slot(ids.PROGRAM_REVENUE_STRESS_SHARE)} "
            f"and net operating income by the same dollars, the ratio is {self.slot(ids.CALC_REVENUE_STRESSED_DSCR)}, "
            f"which {a(ids.A_REVENUE_STRESS)} of {self.slot(ids.PROGRAM_REVENUE_STRESS_MIN)}"
            f"{cite(self.ledger.assertion(ids.A_REVENUE_STRESS))}.",
        ]
        rate = self.ledger.assertion(ids.A_RATE_STRESS)
        if rate is not None:
            sentences.append(
                f"With the note rate raised by the lender's rate stress, the ratio is "
                f"{self.slot(ids.CALC_RATE_STRESSED_DSCR)}, which {a(ids.A_RATE_STRESS)} of "
                f"{self.slot(ids.PROGRAM_RATE_STRESS_MIN)}{cite(rate)}."
            )
        sentences.append(
            f"The cash reserve at opening {a(ids.A_RESERVE)}, which asks for {self.slot(ids.CALC_REQUIRED_RESERVE)}"
            f"{cite(self.ledger.assertion(ids.A_RESERVE))}."
        )
        return sentences

    def drivers(self) -> list[str]:
        """The top drivers of second-year net income and whether a document backs each one (NSP-6.1)."""
        sentences = []
        for rank, opener in zip(range(1, TOP_DRIVERS + 1), DRIVER_OPENERS, strict=True):
            check = self.ledger.assertion(ids.driver_assertion_id(rank))
            if check is None:
                continue
            name = input_phrase(self.text(ids.tornado_id(rank, "input")))
            sentences.append(
                f"{opener} {name}, with a swing of {self.slot(ids.tornado_id(rank, 'swing'))}, and it "
                f"{a(check.id)}{cite(check)}."
            )
        return sentences

    def path(self) -> list[str]:
        """What the path-to-targets search found (only when the ledger has a search result)."""
        status = self.text(ids.PATH_STATUS)
        if not status:
            return []
        if status == "already_meets_target":
            return ["The search for plan changes found that the plan already reaches every simulated target."]
        rank = 1 if status == "paths_found" else 0
        changes = self.changes(rank)
        if not changes:
            return ["No change-set within the lever bounds reaches every simulated target."]
        outcomes = self.outcomes(rank)
        if rank == 1:
            return [
                f"The search for plan changes found a change-set that reaches every simulated target: {changes}, "
                f"with {outcomes}."
            ]
        return [
            "No change-set within the lever bounds reaches every simulated target; the closest change-set sets "
            f"{changes}, with {outcomes}."
        ]

    def outcomes(self, rank: int) -> str:
        """The three simulated chances after one change-set, in words around their slots."""
        after = {suffix: self.slot(ids.change_set_id(rank, f"after.{suffix}")) for suffix in PATH_OUTCOMES}
        return (
            f"a simulated chance of {after['p_dscr_pct']} for the coverage target, {after['p_stressed_pct']} for the "
            f"stress target and {after['p_cash_negative_pct']} of running out of cash"
        )

    def changes(self, rank: int) -> str:
        """'the loan term to {{claim:...}} and the price per unit to {{claim:...}}' for one change-set."""
        parts = []
        index = 1
        while self.has(ids.change_set_id(rank, f"c{index}.to")):
            to_id = ids.change_set_id(rank, f"c{index}.to")
            if self.ledger.claim(to_id).display == "text":  # type: ignore[union-attr]
                parts.append("the site to another of your candidate sites")
            else:
                lever = lever_phrase(self.text(ids.change_set_id(rank, f"c{index}.lever")))
                parts.append(f"{lever} to {self.slot(to_id)}")
            index += 1
        return " and ".join(parts)

    def open_items(self) -> list[str]:
        """Every UNKNOWN or CONFLICTING claim no other sentence mentioned (the V6 omission rule)."""
        items = [claim for claim in self.ledger.open_claims() if claim.id not in self.mentioned]
        if not items:
            return []
        parts = [f"the {claim_phrase(claim)} is {self.slot(claim.id)}" for claim in items]
        return ["Open items: " + "; ".join(parts) + "."]

    def draft(self) -> str:
        """The whole draft, paragraphs separated by blank lines."""
        paragraphs = [
            self.plan(),
            self.site(),
            self.simulation(),
            self.coverage(),
            self.drivers(),
            self.path(),
        ]
        paragraphs.append(self.open_items())
        paragraphs.append([CLOSING])
        return "\n\n".join(" ".join(sentences) for sentences in paragraphs if sentences)


def template_draft(ledger: Ledger) -> str:
    """The deterministic slot-only draft of a founder ledger."""
    return TemplateWriter(ledger).draft()


def required_refs(ledger: Ledger) -> list[str]:
    """What every draft must mention (V6): every finding (assertion False) and every open claim."""
    findings = [f"assert:{item.id}" for item in ledger.assertions if item.result is False]
    open_claims = [f"claim:{claim.id}" for claim in ledger.open_claims()]
    return findings + open_claims


def allowed_sections(ledger: Ledger) -> set[str]:
    """The section ids a draft may cite: the sections of the ledger's assertions."""
    return {section for item in ledger.assertions for section in item.section_ids}
