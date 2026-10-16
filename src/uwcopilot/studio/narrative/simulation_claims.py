"""Claims for the simulation's outputs: every one a MODEL_ESTIMATE of the S3 simulator, "simulated, based on stated
assumptions" (Studio rule E).

Each claim's provenance names the model (studio-feasibility) and its version, and its details and inputs hold the
run's identity: simulator version, seed, number of paths, months and the SHA-256 of the plan that was simulated
(plan_sha256, see founder_ledger.plan_sha256). Two runs of the same plan and seed give the same claims.
Probabilities are stored as percents (pct1); their uncertainty says how many paths the share is over.

What is recorded: P(DSCR >= target) for every year with 12 scheduled payments, P(cash < 0 within 24 months), the
lender stress tests (share of paths passing, and the year-2 base-case ratio the lender's way), the break-even month
percentiles and the share of paths that never break even, P10/P50/P90 of revenue, NOI, net income, debt service and
DSCR for every year, P10/P50/P90 of revenue, net income and cash for the first `monthly_months` months (the package
shows year 1 month by month), and the tornado bars (top drivers first).
"""

from uwcopilot.evidence.ledger_models import Claim, InputValue
from uwcopilot.studio.feasibility.result import FeasibilityResult, MonthBand, StressOutcome, TornadoBar, YearBand
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import model_claim, percent_of
from uwcopilot.studio.narrative.units import format_for

SIMULATOR_MODEL = "studio-feasibility"
NOT_WITHIN_HORIZON = "not within the simulated horizon"
PERCENTILES: tuple[tuple[str, str], ...] = (
    ("p10", "low case (P10)"),
    ("p50", "median (P50)"),
    ("p90", "high case (P90)"),
)
YEAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("revenue", "revenue"),
    ("noi", "net operating income"),
    ("net_income", "net income after debt service"),
)
MONTH_FIELDS: tuple[tuple[str, str], ...] = (
    ("revenue", "revenue"),
    ("net_income", "net income after debt service"),
    ("cash", "cash balance"),
)
DEFAULT_MONTHLY_MONTHS = 12


class SimulationClaims:
    """Builds the claims of one FeasibilityResult with one run identity."""

    def __init__(self, result: FeasibilityResult, run: dict[str, InputValue]) -> None:
        """run: the run identity every claim carries (simulator version, seed, n paths, months, plan hash ...)."""
        self.result = result
        self.run = run

    def estimate(
        self, claim_id: str, label: str, value: float | str, unit: str, display: str, output: str, uncertainty: str
    ) -> Claim:
        """One MODEL_ESTIMATE of this run."""
        return model_claim(
            claim_id,
            label,
            value,
            unit,
            display,
            SIMULATOR_MODEL,
            self.result.simulator_version,
            uncertainty,
            self.run,
            output,
        )

    def share(self, claim_id: str, label: str, share: float, output: str) -> Claim:
        """A simulated probability as a percent."""
        uncertainty = (
            f"share of {self.result.n_paths} simulated paths (seed {self.result.seed}); simulated, based on "
            "stated assumptions"
        )
        return self.estimate(
            claim_id, f"{label} (simulated)", percent_of(share), "PERCENT", "pct1", output, uncertainty
        )

    def band(self, percentile: str) -> str:
        """The uncertainty text of one percentile across the paths."""
        names = {"p10": "10th percentile", "p50": "median", "p90": "90th percentile"}
        return f"{names[percentile]} across {self.result.n_paths} simulated paths (seed {self.result.seed})"

    def probabilities(self) -> list[Claim]:
        """P(DSCR >= target) per year and P(cash < 0 within 24 months)."""
        target = self.result.dscr_target.value
        claims = [
            self.share(
                ids.safe_id("SIM", f"p_dscr_{key.replace('_', '')}"),
                f"Chance that DSCR in {key.replace('_', ' ')} is at least {target:.2f}",
                value,
                f"p_dscr_at_least_target.{key}",
            )
            for key, value in sorted(self.result.p_dscr_at_least_target.items())
        ]
        claims.append(
            self.share(
                ids.SIM_P_CASH,
                "Chance that cash falls below zero within 24 months",
                self.result.p_cash_negative_within_24_months,
                "p_cash_negative_within_24_months",
            )
        )
        return claims

    def stress(self, outcome: StressOutcome) -> list[Claim]:
        """One lender stress test: share of paths passing and the base-case ratio the lender's way."""
        if not outcome.applies:
            return []
        claims: list[Claim] = []
        if outcome.p_dscr_year2_at_least_min is not None:
            claim_id = ids.SIM_P_STRESSED_Y2 if outcome.name == "revenue_stress" else ids.stress_id(outcome.name, "p")
            claims.append(
                self.share(
                    claim_id,
                    f"Chance that year-2 DSCR is at least {outcome.min_dscr:.2f} under {outcome.description.lower()}",
                    outcome.p_dscr_year2_at_least_min,
                    f"stress.{outcome.name}.p_dscr_year2_at_least_min",
                )
            )
        if outcome.base_case_dscr is not None:
            claims.append(
                self.estimate(
                    ids.stress_id(outcome.name, "base_case_dscr"),
                    f"Year-2 base-case DSCR under {outcome.description.lower()} (simulator, loan only)",
                    outcome.base_case_dscr,
                    "RATIO",
                    "ratio2",
                    f"stress.{outcome.name}.base_case_dscr",
                    "the year-2 median (P50) with the stress applied the lender's way",
                )
            )
        return claims

    def break_even(self) -> list[Claim]:
        """Break-even month percentiles (text when fewer paths break even) and the share that never does.

        A break-even month is a month number (month 1 = the opening month), so it is printed as a plain number
        ("month 14"), not as a duration.
        """
        result = self.result.break_even
        claims: list[Claim] = []
        for claim_id, label, month, percentile in (
            (ids.SIM_BREAK_EVEN_P10, "Break-even month, low case (P10)", result.p10_month, "p10"),
            (ids.SIM_BREAK_EVEN_P50, "Break-even month, median (P50)", result.p50_month, "p50"),
            (ids.SIM_BREAK_EVEN_P90, "Break-even month, high case (P90)", result.p90_month, "p90"),
        ):
            output = f"break_even.{percentile}_month"
            if month is None:
                claims.append(
                    self.estimate(claim_id, label, NOT_WITHIN_HORIZON, "TEXT", "text", output, self.band(percentile))
                )
            else:
                claims.append(self.estimate(claim_id, label, month, "MONTHS", "int", output, self.band(percentile)))
        claims.append(
            self.share(
                ids.SIM_NOT_BREAK_EVEN,
                f"Share of paths that do not break even within {self.result.months} months",
                result.share_not_within_horizon,
                "break_even.share_not_within_horizon",
            )
        )
        return claims

    def year(self, band: YearBand) -> list[Claim]:
        """P10/P50/P90 of one year's totals, its median debt service and its DSCR percentiles (when defined)."""
        claims: list[Claim] = []
        for field, words in YEAR_FIELDS:
            for percentile, shown in PERCENTILES:
                value = getattr(band, f"{field}_{percentile}")
                claims.append(
                    self.estimate(
                        ids.year_id(band.year, f"{field}_{percentile}"),
                        f"Year {band.year} {words}, {shown}",
                        value,
                        "USD",
                        "usd0",
                        f"yearly[{band.year}].{field}_{percentile}",
                        self.band(percentile),
                    )
                )
        claims.append(
            self.estimate(
                ids.year_id(band.year, "debt_service_p50"),
                f"Year {band.year} debt service, median (P50)",
                band.debt_service_p50,
                "USD",
                "usd0",
                f"yearly[{band.year}].debt_service_p50",
                self.band("p50"),
            )
        )
        for percentile, shown in PERCENTILES:
            value = getattr(band, f"dscr_{percentile}")
            if value is not None:
                claims.append(
                    self.estimate(
                        ids.year_id(band.year, f"dscr_{percentile}"),
                        f"Year {band.year} DSCR, {shown}",
                        value,
                        "RATIO",
                        "ratio2",
                        f"yearly[{band.year}].dscr_{percentile}",
                        self.band(percentile),
                    )
                )
        return claims

    def month(self, band: MonthBand) -> list[Claim]:
        """P10/P50/P90 of one month's revenue, net income and cash."""
        claims: list[Claim] = []
        for field, words in MONTH_FIELDS:
            for percentile, shown in PERCENTILES:
                claims.append(
                    self.estimate(
                        ids.month_id(band.month, f"{field}_{percentile}"),
                        f"Month {band.month} ({band.calendar_month}) {words}, {shown}",
                        getattr(band, f"{field}_{percentile}"),
                        "USD",
                        "usd0",
                        f"monthly[{band.month}].{field}_{percentile}",
                        self.band(percentile),
                    )
                )
        return claims

    def tornado_bar(self, bar: TornadoBar) -> list[Claim]:
        """One tornado bar: which input, its P10 and P90, and the year-2 median net income at each end."""
        uncertainty = "one input swung between its P10 and P90 with every other draw held fixed"
        fmt = format_for(bar.unit, bar.input_p10)
        output = f"tornado.bars[{bar.rank}]"
        return [
            self.estimate(
                ids.tornado_id(bar.rank, "input"),
                f"Tornado rank {bar.rank}: input name",
                bar.name,
                "TEXT",
                "text",
                f"{output}.name",
                uncertainty,
            ),
            self.estimate(
                ids.tornado_id(bar.rank, "input_p10"),
                f"{bar.description}, its P10",
                fmt.scale(bar.input_p10),
                fmt.unit,
                fmt.display,
                f"{output}.input_p10",
                uncertainty,
            ),
            self.estimate(
                ids.tornado_id(bar.rank, "input_p90"),
                f"{bar.description}, its P90",
                fmt.scale(bar.input_p90),
                fmt.unit,
                fmt.display,
                f"{output}.input_p90",
                uncertainty,
            ),
            self.estimate(
                ids.tornado_id(bar.rank, "metric_at_p10"),
                f"Year-2 median net income with {bar.name} at its P10",
                bar.metric_at_p10,
                "USD",
                "usd0",
                f"{output}.metric_at_p10",
                uncertainty,
            ),
            self.estimate(
                ids.tornado_id(bar.rank, "metric_at_p90"),
                f"Year-2 median net income with {bar.name} at its P90",
                bar.metric_at_p90,
                "USD",
                "usd0",
                f"{output}.metric_at_p90",
                uncertainty,
            ),
            self.estimate(
                ids.tornado_id(bar.rank, "swing"),
                f"Swing of year-2 median net income from {bar.name}",
                bar.swing,
                "USD",
                "usd0",
                f"{output}.swing",
                uncertainty,
            ),
        ]

    def tornado(self) -> list[Claim]:
        """Every tornado bar (none when the simulation ran without a tornado)."""
        if self.result.tornado is None:
            return []
        return [claim for bar in self.result.tornado.bars for claim in self.tornado_bar(bar)]

    def all(self, monthly_months: int = DEFAULT_MONTHLY_MONTHS) -> list[Claim]:
        """Every simulation claim (see the module docstring)."""
        claims = self.probabilities()
        for outcome in self.result.stress:
            claims.extend(self.stress(outcome))
        claims.extend(self.break_even())
        for band in self.result.yearly:
            claims.extend(self.year(band))
        for band in self.result.monthly[:monthly_months]:
            claims.extend(self.month(band))
        claims.extend(self.tornado())
        return claims


def simulation_claims(
    result: FeasibilityResult, run: dict[str, InputValue], monthly_months: int = DEFAULT_MONTHLY_MONTHS
) -> list[Claim]:
    """Every MODEL_ESTIMATE claim of one simulation run."""
    return SimulationClaims(result, run).all(monthly_months)
