"""Plain-language names the narrative may use around its slots, and the check that a phrase is grammar-safe.

A narrative draft may contain prose and slots only: no digits, no number words, no currency or percent symbols, no
comparative words outside a sentence with an {{assert:}} slot and no decision wording (provenance/memo_grammar.py).
So the words around the slots come from fixed tables written here, and every phrase taken from a config file (a
metro name, a lever label) is checked with the grammar validator itself (is_safe) before it is used; an unsafe phrase
is replaced by a neutral one. Founder free text (site labels, the names of other debt) is never used as prose, so it
can never reach a prompt or change what a sentence says.
"""

import re

from uwcopilot.evidence.ledger_models import Claim
from uwcopilot.provenance.memo_grammar import validate
from uwcopilot.studio.metros import MetroNotFound, load_metro

PARENTHETICAL = re.compile(r"\s*\([^()]*\)")
BUSINESS_NOUNS: dict[str, str] = {
    "car_wash": "car wash",
    "laundromat": "laundromat",
    "motel": "motel or small hotel",
    "restaurant_cafe": "restaurant, cafe or dessert shop",
    "retail_storefront": "small retail storefront",
    "short_term_rental": "short-term rental",
}
# Simulator inputs (feasibility distributions) in plain words, for the top-driver sentences.
INPUT_PHRASES: dict[str, str] = {
    "mature_demand": "the demand at the site",
    "capacity": "the most demand the business can serve",
    "days_open_per_week": "the days open each week",
    "price": "the price per unit",
    "ramp_starting_share": "how much of the mature demand the opening month brings",
    "ramp_months_to_maturity": "how long demand takes to mature",
    "cost_ratio_cogs": "the cost of goods as a share of revenue",
    "cost_ratio_utilities": "utilities as a share of revenue",
    "cost_ratio_supplies": "supplies as a share of revenue",
    "cost_ratio_marketing": "marketing as a share of revenue",
    "cost_ratio_insurance": "insurance as a share of revenue",
    "cost_ratio_maintenance": "maintenance as a share of revenue",
    "paid_hours_per_month": "the paid staff hours",
    "hourly_wage": "the hourly wage",
    "monthly_rent": "the rent",
    "rent_escalation": "the yearly rent increase",
    "price_elasticity": "how volume reacts to price",
    "monthly_noise_sd": "month-to-month demand noise",
    "payroll_burden_share": "payroll taxes and benefits",
    "capex_overrun_share": "a build-out cost overrun",
    "competitor_entry_annual_probability": "the chance that a competitor opens nearby",
    "competitor_entry_demand_loss": "the demand a new competitor takes",
    "rate_shock_annual_probability": "the chance of a rate rise",
    "rate_shock_size_pct_points": "the size of a rate rise",
    "one_off_shock_annual_probability": "the chance of a repair, closure or vacancy event",
    "one_off_shock_revenue_loss_share": "the revenue lost in such an event",
    "one_off_shock_repair_cost_share_of_capex": "the repair bill of such an event",
}
# Location factors and metro inputs in plain words, for the open-items sentence.
FACTOR_PHRASES: dict[str, str] = {
    "resident_population": "number of residents near the site",
    "daytime_workers": "number of daytime workers near the site",
    "households": "number of households near the site",
    "renter_households": "number of renter households near the site",
    "median_household_income": "median household income of the site's tract",
    "traffic_aadt": "daily traffic on the nearest road",
    "competition": "number of same-type businesses near the site",
    "anchors": "number of nearby traffic generators",
    "tourism": "number of tourism anchors near the site",
    "str_comparables": "number of comparable short-term rentals near the site",
    "commercial_rent": "commercial rent for the metro",
    "local_wages": "local wage for the industry",
    "prime_rate": "prime rate for the metro",
}
CLAIM_PHRASES: dict[str, str] = {
    "FOUNDER.credit_score": "lowest owner credit score",
    "FOUNDER.experience_months": "most relevant owner experience",
    "FOUNDER.owner_equity": "owner cash equity",
    "FOUNDER.loan_amount": "loan amount",
    "FOUNDER.loan_term_months": "loan term",
    "FOUNDER.capex": "build-out and equipment cost",
    "FOUNDER.price": "price per unit",
    "FOUNDER.monthly_rent": "monthly rent",
    "FOUNDER.hourly_wage": "planned hourly wage",
    "FOUNDER.paid_hours_per_month": "planned paid staff hours",
    "SITE.overall_score": "overall site score",
    "SITE.coverage_score": "SiteIQ data coverage of the site",
    "SITE.demand_mode": "most likely demand at the site",
    "SITE.demand_low": "low end of the site's demand range",
    "SITE.demand_high": "high end of the site's demand range",
    "CALC.other_debt_amount": "other debt, total amount",
    "CALC.other_debt_monthly_payment": "other debt, total monthly payment",
    "CALC.total_project_cost": "total project cost",
    "CALC.equity_injection_pct": "equity injection",
    "CALC.cash_reserve_at_opening": "cash reserve at opening",
    "CALC.required_reserve": "working-capital reserve the program asks for",
    "CALC.projected_dscr": "projected debt service coverage ratio",
    "CALC.revenue_stressed_dscr": "revenue-stressed coverage ratio",
    "CALC.rate_stressed_dscr": "rate-stressed coverage ratio",
    "PROGRAM.min_equity_injection_pct": "program's minimum equity injection",
    "PROGRAM.min_projected_dscr": "program's minimum projected coverage ratio",
    "PROGRAM.revenue_stress_share_pct": "revenue cut of the lender's revenue stress",
    "PROGRAM.revenue_stress_min_dscr": "minimum coverage ratio under the revenue stress",
    "PROGRAM.rate_stress_min_dscr": "minimum coverage ratio under the rate stress",
    "TARGET.dscr_min": "coverage ratio the target counts as covered",
    "TARGET.stress_revenue_share_pct": "revenue cut of the stress target",
    "TARGET.p_dscr_min_pct": "Studio target for the chance of reaching that ratio",
    "TARGET.stressed_dscr_min": "coverage ratio the stress target counts",
    "TARGET.p_stressed_min_pct": "Studio target for the chance of passing the revenue stress",
    "TARGET.p_cash_negative_max_pct": "Studio target for the largest chance of running out of cash",
    "TARGET.cash_months": "months the cash check covers",
    "SIM.p_dscr_year2": "simulated chance that the second-year coverage ratio reaches the target",
    "SIM.p_stressed_dscr_year2": "simulated chance that the second-year ratio passes the revenue stress",
    "SIM.p_cash_negative_24m": "simulated chance of running out of cash",
    "SIM.y2.revenue_p50": "median second-year revenue",
    "SIM.y2.noi_p50": "median second-year net operating income",
    "SIM.y2.noi_p10": "low case of second-year net operating income",
    "SIM.y2.noi_p90": "high case of second-year net operating income",
    "SIM.break_even_p50": "median break-even month",
    "SIM.share_not_break_even": "share of simulated futures that do not break even",
    "PATH.before.p_dscr_pct": "simulated chance of reaching the coverage target, as the path search measured it",
    "PATH.before.p_stressed_pct": "simulated chance of passing the stress target, as the path search measured it",
    "PATH.before.p_cash_negative_pct": "simulated chance of running out of cash, as the path search measured it",
    "TARGET.measured.dscr": "simulated chance for the edited coverage target",
    "TARGET.measured.stress": "simulated chance for the edited stress target",
    "TARGET.measured.cash": "simulated chance for the edited cash target",
}
# Claim ids with a rank or an index in them: (pattern, phrase).
PATTERN_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^TORNADO\.r\d+\.swing$"), "swing of second-year net income from this input"),
    (
        re.compile(r"^PATH\.cs\d+\.after\.p_dscr_pct$"),
        "simulated chance of reaching the coverage target after the change",
    ),
    (re.compile(r"^PATH\.cs\d+\.c\d+\.to$"), "new value of the changed plan figure"),
)
FALLBACK_PHRASE = "item listed in the evidence appendix"
NEUTRAL_METRO = "this metro"


def is_safe(text: str) -> bool:
    """True when the text breaks no rule of the narrative grammar on its own (no slots, citations unchecked)."""
    return not validate(text, set(), set())


def safe_or(text: str, fallback: str) -> str:
    """The text when it is grammar-safe, else the fallback."""
    return text if text and is_safe(text) else fallback


def article(noun: str) -> str:
    """'a' or 'an' for a noun phrase."""
    return "an" if noun[:1].lower() in "aeiou" else "a"


def business_noun(profile_id: str) -> str:
    """The business type in plain words."""
    return safe_or(BUSINESS_NOUNS.get(profile_id, profile_id.replace("_", " ")), "business")


def metro_phrase(metro_id: str) -> str:
    """The metro's configured name when it is grammar-safe, else 'this metro'."""
    try:
        name = load_metro(metro_id).name
    except MetroNotFound:
        return NEUTRAL_METRO
    return safe_or(name, NEUTRAL_METRO)


def demand_unit_phrase(unit: str) -> str:
    """'cars/day' -> 'cars per day' (or 'units of demand' when the unit is not grammar-safe)."""
    return safe_or(unit.replace("/", " per "), "units of demand")


def input_phrase(name: str) -> str:
    """A simulator input in plain words."""
    return safe_or(INPUT_PHRASES.get(name, name.replace("_", " ")), "an input of the simulation")


def claim_phrase(claim: Claim, for_prose: bool = True) -> str:
    """A claim in plain words (curated tables first, then its own label when it is grammar-safe).

    for_prose=True (a draft sentence such as the open items): the phrase must pass the grammar on its own, else the
    neutral FALLBACK_PHRASE is used. for_prose=False (the meaning listed next to a slot in the LLM's data block): the
    curated phrase is used as it is, because the model reads it but does not copy it into the draft.
    """
    phrase = CLAIM_PHRASES.get(claim.id)
    for pattern, text in PATTERN_PHRASES:
        if phrase is None and pattern.match(claim.id):
            phrase = text
    parts = claim.id.split(".")
    if phrase is None and parts[0] == "SITE" and len(parts) >= 3 and parts[2] in FACTOR_PHRASES:
        phrase = FACTOR_PHRASES[parts[2]]
    if phrase is None:
        label = PARENTHETICAL.sub("", claim.label).strip()
        phrase = label[:1].lower() + label[1:]
    return safe_or(phrase, FALLBACK_PHRASE) if for_prose else phrase


def lever_phrase(label: str) -> str:
    """A path-search lever label ('Loan term') in plain words ('the loan term')."""
    text = PARENTHETICAL.sub("", label).strip().lower()
    return safe_or(f"the {text}", "a plan figure")
