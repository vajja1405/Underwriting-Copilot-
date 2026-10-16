"""The simulator's own assumptions (config/studio/feasibility/assumptions.yaml) and the DSCR target.

A business-type profile (S1) says how a type of business makes and spends money. The simulator also needs risks
and levers that are not part of any profile: price elasticity, month-to-month demand noise, the three shocks
(a competitor opening, a rate rise on a variable-rate loan, a one-off repair or vacancy), build-out overruns, payroll
burden and the default spread over prime. They live in one YAML file, use the same Quantity model as the profiles
(every field SOURCED with a source, or an ASSUMPTION range with a note), and can be edited per plan.

The DSCR target is a lender policy parameter, not a fact: the default is the term-loan minimum of the SYNTHETIC
Northwind guideline NBC-4.2, imported from agent/policy_credit.py so it is never retyped. The founder can edit it.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.policy_credit import DSCR_MINIMUM
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.studio.profiles.models import Quantity

ASSUMPTIONS_FILE: Path = CONFIG_DIR / "studio" / "feasibility" / "assumptions.yaml"
DSCR_TARGET_LABEL = "lender policy (SYNTHETIC Northwind guideline NBC-4.2), editable"
DSCR_TARGET_SOURCE = (
    "corpus/guidelines/northwind_business_credit.md NBC-4.2, term loan minimum decision DSCR "
    "(SYNTHETIC document of a fictional institution; also the NSP-4.1 threshold of the SYNTHETIC startup program)"
)


class FeasibilityAssumptions(BaseModel):
    """Every simulator assumption that is not in a business-type profile. Field names match the YAML keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    price_elasticity: Quantity
    monthly_noise_sd: Quantity
    competitor_entry_annual_probability: Quantity
    competitor_entry_demand_loss: Quantity
    rate_shock_annual_probability: Quantity
    rate_shock_size_pct_points: Quantity
    one_off_shock_annual_probability: Quantity
    one_off_shock_revenue_loss_share: Quantity
    one_off_shock_repair_cost_share_of_capex: Quantity
    capex_overrun_share: Quantity
    payroll_burden_share: Quantity
    rate_spread_over_prime_pct: Quantity


def load_assumptions(path: Path = ASSUMPTIONS_FILE) -> FeasibilityAssumptions:
    """Read and validate the assumptions file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must hold a YAML mapping")
    return FeasibilityAssumptions.model_validate(data)


class DscrTarget(BaseModel):
    """The DSCR the simulation counts as "covered". A policy choice of the lender, shown with its source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float = Field(gt=0)
    label: str = Field(min_length=1)
    source: str = Field(min_length=1)


def default_dscr_target() -> DscrTarget:
    """The SYNTHETIC Northwind term-loan minimum (NBC-4.2), labeled as an editable lender policy."""
    return DscrTarget(value=DSCR_MINIMUM["term_loan"], label=DSCR_TARGET_LABEL, source=DSCR_TARGET_SOURCE)


def custom_dscr_target(value: float) -> DscrTarget:
    """A DSCR target typed by the user (for example their lender's own minimum)."""
    return DscrTarget(value=value, label="user-entered DSCR target", source="entered by the user")
