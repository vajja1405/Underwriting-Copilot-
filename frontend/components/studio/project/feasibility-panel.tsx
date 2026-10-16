"use client";

import { Import } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";

import { DataStatusChip } from "@/components/evidence/data-status-chip";
import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { ChartFrame, DataTable } from "@/components/studio/charts/chart-frame";
import { ProbabilityGauge } from "@/components/studio/project/probability-gauge";
import { SimulationLabel } from "@/components/studio/simulation-label";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber, formatPercent, formatRange, formatShare, formatUsd, formatWithUnit, humanize, isNumber, monthLabel, sentenceCase } from "@/lib/format";
import type { FeasibilityResponse, PathTarget } from "@/lib/studio-types";
import { useMediaQuery } from "@/lib/use-media-query";

const BandChart = dynamic(() => import("@/components/studio/charts/band-chart"), {
  ssr: false,
  loading: () => <Skeleton className="h-64 w-full" />,
});
const TornadoChart = dynamic(() => import("@/components/studio/charts/tornado-chart"), {
  ssr: false,
  loading: () => <Skeleton className="h-72 w-full" />,
});

const TORNADO_ROWS = 8;
const INPUT_ROWS_COLLAPSED = 6;

/** The build-out overrun the simulator takes out of the opening cash (a sampled ASSUMPTION), when it was sampled. */
function overrunInput(response: FeasibilityResponse) {
  return response.result.distributions.find((item) => item.name === "capex_overrun_share") ?? null;
}

/**
 * The label of the two blocks that list what the simulation STARTED FROM (the loan, and every sampled input): figures
 * the founder stated, assumed ranges and sourced values, each with its own status chip. They are inputs, not
 * simulated results, so these blocks do not carry the "simulated, based on stated assumptions" label.
 */
function SimulationInputsLabel({ id }: { id: string }) {
  return (
    <p id={id} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed text-muted-foreground">
      <Badge variant="outline" className="font-semibold">
        <Import aria-hidden="true" />
        Inputs, not results
      </Badge>
      <span>What the simulation was given (stated, assumed or sourced), not what it simulated.</span>
    </p>
  );
}

/**
 * The feasibility results for one site: three probability gauges, the revenue fan, the cash runway, the tornado,
 * break-even, the loan as simulated and every sampled input with its status and source. Every block of simulated
 * numbers carries the "simulated, based on stated assumptions" label; the two blocks of inputs (the loan and the
 * sampled inputs) carry SimulationInputsLabel instead. Every number comes from the FeasibilityResult the API returned.
 *
 * rateTyped: false when the founder left the rate empty, so the rate is the city's prime rate PLUS an assumed spread
 * (the simulator's rate status describes the prime rate only; the spread is shown as an assumption next to it).
 */
export function FeasibilityPanel({ response, target, rateTyped = true }: { response: FeasibilityResponse; target: PathTarget | null; rateTyped?: boolean }) {
  const wide = useMediaQuery("(min-width: 640px)");
  const [showAllInputs, setShowAllInputs] = useState(false);
  const result = response.result;
  const headline = response.headline;
  const targetValue = headline.dscr_target;
  const stressShare = headline.stress_revenue_drop_share;
  const stressMin = headline.stress_min_dscr;

  const revenue = result.monthly.map((band) => ({
    month: band.month,
    calendarMonth: band.calendar_month,
    p10: band.revenue_p10,
    p50: band.revenue_p50,
    p90: band.revenue_p90,
  }));
  const cash = result.monthly.map((band) => ({
    month: band.month,
    calendarMonth: band.calendar_month,
    p10: band.cash_p10,
    p50: band.cash_p50,
    p90: band.cash_p90,
  }));
  const tornadoBars = result.tornado?.bars.slice(0, TORNADO_ROWS) ?? [];
  const inputs = showAllInputs ? result.distributions : result.distributions.slice(0, INPUT_ROWS_COLLAPSED);
  const overrun = overrunInput(response);
  const overrunWords = overrun
    ? `a simulated build-out overrun of ${formatShare(overrun.low, 0)} to ${formatShare(overrun.high, 0)} of the build-out (most likely ${formatShare(overrun.mode, 0)}, an assumption)`
    : null;
  const rateChips = rateTyped ? (
    <DataStatusChip status={result.loan.rate_status} />
  ) : (
    <span className="flex flex-wrap items-center gap-1">
      <DataStatusChip status={result.loan.rate_status} />
      <span className="text-xs font-normal text-muted-foreground">+ spread</span>
      <EvidenceChip status="ASSUMPTION" />
    </span>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 md:grid-cols-3">
        <ProbabilityGauge
          title={`Loan payments covered with room to spare in year 2 (DSCR at least ${targetValue.toFixed(2)})`}
          value={headline.p_dscr_year2_at_least_target}
          paths={result.n_paths}
          sentence={(count, paths) =>
            `In ${formatNumber(count)} of ${formatNumber(paths)} simulated futures, year-2 income covers the loan payments at least ${targetValue.toFixed(2)} times.${
              isNumber(headline.p_dscr_year1_at_least_target)
                ? ` Year 1 (with the opening ramp-up): ${formatShare(headline.p_dscr_year1_at_least_target)}.`
                : ""
            }`
          }
          target={target ? { text: `Target used for the path to approval: at least ${formatShare(target.p_dscr_min, 0)}.`, share: target.p_dscr_min, direction: "at_least" } : null}
        />
        <ProbabilityGauge
          title={
            isNumber(stressShare) && isNumber(stressMin)
              ? `Still covered if revenue is ${formatShare(stressShare, 0)} lower (lender stress, DSCR at least ${stressMin.toFixed(2)})`
              : "Lender revenue stress test"
          }
          value={headline.p_stressed_dscr_year2_at_least_min}
          paths={result.n_paths}
          sentence={(count, paths) =>
            `In ${formatNumber(count)} of ${formatNumber(paths)} simulated futures, year-2 DSCR stays at least ${isNumber(stressMin) ? stressMin.toFixed(2) : "the minimum"} under the lender's revenue stress.`
          }
          target={target ? { text: `Target used for the path to approval: at least ${formatShare(target.p_stressed_min, 0)}.`, share: target.p_stressed_min, direction: "at_least" } : null}
        />
        <ProbabilityGauge
          title="Cash runs out (below $0) within 24 months"
          value={headline.p_cash_negative_within_24_months}
          paths={result.n_paths}
          sentence={(count, paths) =>
            `In ${formatNumber(count)} of ${formatNumber(paths)} simulated futures, cash falls below zero at some point in the first 24 months. Lower is better.`
          }
          target={target ? { text: `Target used for the path to approval: at most ${formatShare(target.p_cash_negative_max, 0)}.`, share: target.p_cash_negative_max, direction: "at_most" } : null}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {formatNumber(result.n_paths)} simulated futures over {result.months} months from {monthLabel(result.opening_date.slice(0, 7))}, seed{" "}
        {response.seed} (the same for every version of this plan, so versions are compared on the same futures). DSCR target {result.dscr_target.value.toFixed(2)}: {result.dscr_target.label}. Reaching a target is not an
        approval; lenders make their own decisions.
      </p>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartFrame
          title="Monthly revenue: the likely range"
          description="The band holds the middle 80% of simulated futures (P10 to P90); the line is the median (P50)."
          label={result.label}
          legend={[
            { label: "P10 to P90 range", color: "var(--viz-1)", kind: "area" },
            { label: "Median (P50)", color: "var(--viz-1)", kind: "line" },
          ]}
          table={
            <DataTable
              caption="Monthly revenue, P10, P50 and P90, USD"
              columns={["Month", "P10", "P50", "P90"]}
              rows={revenue.map((row) => [`${row.month} · ${monthLabel(row.calendarMonth)}`, formatUsd(row.p10), formatUsd(row.p50), formatUsd(row.p90)])}
            />
          }
        >
          <BandChart data={revenue} />
        </ChartFrame>
        <ChartFrame
          title="Cash on hand: the runway"
          description={`Cash at the end of each month. Each future opens with the planned reserve of ${formatUsd(result.planned_starting_cash)} (your cash plus the loan minus the build-out)${overrunWords ? ` minus ${overrunWords}` : ""}, so it starts at or below that reserve. Below the $0 line means the business ran out of cash.`}
          label={result.label}
          legend={[
            { label: "P10 to P90 range", color: "var(--viz-1)", kind: "area" },
            { label: "Median (P50)", color: "var(--viz-1)", kind: "line" },
            { label: "$0", color: "var(--ink-muted)", kind: "rule" },
          ]}
          table={
            <DataTable
              caption="Cash at the end of each month, P10, P50 and P90, USD"
              columns={["Month", "P10", "P50", "P90"]}
              rows={cash.map((row) => [`${row.month} · ${monthLabel(row.calendarMonth)}`, formatUsd(row.p10), formatUsd(row.p50), formatUsd(row.p90)])}
            />
          }
        >
          <BandChart data={cash} zeroLine />
        </ChartFrame>
      </div>

      {result.tornado && tornadoBars.length > 0 ? (
        <ChartFrame
          title="What moves the result most"
          description={`Each input set to its low end (P10) and then its high end (P90), everything else held fixed. Bars show the change in ${result.tornado.metric} from the base case of ${formatUsd(result.tornado.base_metric)}. Top ${tornadoBars.length} of ${result.tornado.bars.length} inputs.`}
          label={result.label}
          legend={[
            { label: "Input at its low end (P10)", color: "var(--viz-2)", kind: "bar" },
            { label: "Input at its high end (P90)", color: "var(--viz-1)", kind: "bar" },
          ]}
          table={
            <DataTable
              caption="Tornado: year-2 net income with each input at its P10 and P90"
              columns={["Input", "Input at P10", "Result", "Input at P90", "Result", "Swing"]}
              rows={result.tornado.bars.map((bar) => [
                bar.description,
                formatWithUnit(bar.input_p10, bar.unit),
                formatUsd(bar.metric_at_p10),
                formatWithUnit(bar.input_p90, bar.unit),
                formatUsd(bar.metric_at_p90),
                formatUsd(bar.swing),
              ])}
            />
          }
        >
          <TornadoChart bars={tornadoBars} baseMetric={result.tornado.base_metric} compact={!wide} />
        </ChartFrame>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="break-even-title" className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5">
          <div className="flex items-start justify-between gap-2">
            <h3 id="break-even-title" className="font-semibold">
              Break-even month
            </h3>
            <EvidenceChip status="MODEL_ESTIMATE" />
          </div>
          <SimulationLabel text={result.label} />
          <dl className="figure-nums grid grid-cols-3 gap-3 text-center">
            {(
              [
                ["Early (P10)", result.break_even.p10_month],
                ["Typical (P50)", result.break_even.p50_month],
                ["Late (P90)", result.break_even.p90_month],
              ] as const
            ).map(([label, month]) => (
              <div key={label} className="flex flex-col gap-1 rounded-lg bg-paper-sunken px-2 py-3 sm:px-3">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="text-base font-semibold leading-snug sm:text-lg">{month === null ? `Not by month ${result.months}` : `Month ${month}`}</dd>
              </div>
            ))}
          </dl>
          <p className="text-sm text-muted-foreground">
            {formatShare(result.break_even.share_not_within_horizon)} of simulated futures do not break even within {result.months} months.
            Definition: {result.break_even.definition}.
          </p>
        </section>

        <section
          aria-labelledby="loan-title"
          aria-describedby="loan-inputs-label"
          className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5"
        >
          <h3 id="loan-title" className="font-semibold">
            The loan as simulated
          </h3>
          <SimulationInputsLabel id="loan-inputs-label" />
          <dl className="flex flex-col divide-y divide-border text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 py-2">
              <dt className="text-muted-foreground">Amount and term</dt>
              <dd className="figure-nums flex items-center gap-2 font-semibold">
                {formatUsd(result.loan.amount)} over {result.loan.term_months} months
                <EvidenceChip status="USER_STATED" />
              </dd>
            </div>
            {/* One group per dt: a <dl> allows one wrapping <div> around dt/dd, not nested ones (axe: dlitem). */}
            <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 py-2">
              <dt className="text-muted-foreground">Rate ({result.loan.rate_type})</dt>
              <dd className="figure-nums flex flex-wrap items-center gap-2 font-semibold">
                {formatPercent(result.loan.annual_rate_pct)}
                {rateChips}
              </dd>
              <dd className="basis-full text-xs leading-relaxed text-muted-foreground">{result.loan.rate_source}</dd>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 py-2">
              <dt className="text-muted-foreground">Monthly payment</dt>
              <dd className="figure-nums flex flex-wrap items-center gap-2 font-semibold">
                {formatUsd(result.loan.monthly_payment)}
                {rateChips}
              </dd>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 py-2">
              <dt className="text-muted-foreground">Build-out and equipment</dt>
              <dd className="figure-nums flex items-center gap-2 font-semibold">
                {formatUsd(result.planned_capex)}
                <DataStatusChip status={result.capex_status} />
              </dd>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 py-2">
              <dt className="text-muted-foreground">Planned reserve at opening (your cash + loan - build-out)</dt>
              <dd className="figure-nums flex items-center gap-2 font-semibold">
                {formatUsd(result.planned_starting_cash)}
                <DataStatusChip status="DERIVED" />
              </dd>
              {overrunWords ? (
                <dd className="basis-full text-xs leading-relaxed text-muted-foreground">
                  Before the simulation: each simulated future then takes {overrunWords} out of it.
                </dd>
              ) : null}
            </div>
          </dl>
        </section>
      </div>

      <section
        aria-labelledby="inputs-title"
        aria-describedby="sampled-inputs-label"
        className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5"
      >
        <div className="flex flex-col gap-1">
          <h3 id="inputs-title" className="font-semibold">
            Every input the simulation sampled ({result.distributions.length})
          </h3>
          <SimulationInputsLabel id="sampled-inputs-label" />
          <p className="text-sm text-muted-foreground">
            Low, most likely and high values, with where each came from. Assumptions are ranges you can replace with your own
            figures or documents.
          </p>
        </div>
        <ul className="flex flex-col divide-y divide-border">
          {inputs.map((input) => (
            <li key={input.name} className="grid gap-1.5 py-3 sm:grid-cols-[1fr_auto] sm:gap-x-6">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="text-sm font-medium">{sentenceCase(input.description)}</p>
                <p className="text-xs leading-relaxed text-muted-foreground">{input.source}</p>
              </div>
              <div className="flex flex-col gap-1 sm:items-end">
                <p className="figure-nums text-sm">
                  {input.kind === "fixed" ? formatWithUnit(input.mode, input.unit) : formatRange(input.low, input.high, input.unit)}
                </p>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                  {input.kind !== "fixed" ? (
                    <span className="figure-nums text-xs text-muted-foreground">most likely {formatWithUnit(input.mode, input.unit)}</span>
                  ) : null}
                  <DataStatusChip status={input.status} />
                </div>
              </div>
            </li>
          ))}
        </ul>
        {result.distributions.length > INPUT_ROWS_COLLAPSED ? (
          <Button type="button" variant="outline" size="sm" className="self-start" onClick={() => setShowAllInputs((value) => !value)} aria-expanded={showAllInputs}>
            {showAllInputs ? "Show fewer inputs" : `Show all ${result.distributions.length} inputs`}
          </Button>
        ) : null}
        {result.shocks.length > 0 ? (
          <details className="min-w-0 text-sm">
            <summary className="cursor-pointer font-medium text-accent">How shocks are simulated ({result.shocks.length})</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
              {result.shocks.map((shock) => (
                <li key={shock}>{humanize(shock)}</li>
              ))}
            </ul>
          </details>
        ) : null}
        {result.notes.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            {result.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
