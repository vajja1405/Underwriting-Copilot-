"use client";

import { AlertTriangle, CircleCheck, ExternalLink, Gauge } from "lucide-react";

import { DataStatusChip } from "@/components/evidence/data-status-chip";
import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { ReasonText } from "@/components/studio/reason-text";
import { Skeleton } from "@/components/ui/skeleton";
import { formatEstimate, formatRange, formatScore, formatShare, formatWithUnit, humanize, isNumber } from "@/lib/format";
import type { DemandOut, FactorOut, SiteScoreOut, SiteScoresResponse, SourceRecord } from "@/lib/studio-types";
import { cn } from "@/lib/utils";

/**
 * Site cards (score, coverage, top reasons, one card per candidate site, selectable) and the selected site's
 * detail: every factor with its raw value, status, score, weight and source, plus the demand estimate.
 */

/** Coverage (0-1): the weighted share of required factors backed by data. Below half is flagged. */
export function CoverageBadge({ coverage }: { coverage: number }) {
  const low = coverage < 0.5;
  const Icon = low ? AlertTriangle : CircleCheck;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-[0.3rem] border px-1.5 py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.07em]",
        low ? "border-dashed border-unknown-line bg-unknown-soft text-unknown" : "border-rule-strong bg-card text-ink",
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.25} aria-hidden="true" />
      Data coverage {formatShare(coverage, 0)}
    </span>
  );
}

/** The factors that entered the overall score, most weight first. */
function topFactors(site: SiteScoreOut, count = 3): FactorOut[] {
  return [...site.factors]
    .filter((factor) => factor.used_in_overall)
    .sort((a, b) => b.weight - a.weight)
    .slice(0, count);
}

/** True when the site has a SiteIQ result (an error means it could not be scored at all). */
export function isScored(site: SiteScoreOut): boolean {
  return site.error === null;
}

export function SiteCards({
  response,
  selected,
  onSelect,
  simulationAvailable = true,
}: {
  response: SiteScoresResponse;
  selected: number;
  onSelect: (index: number) => void;
  /** False in a city without data here: nothing is simulated, so the selected card must not say "Simulated below". */
  simulationAvailable?: boolean;
}) {
  const dataStatus = response.data_label === "SYNTHETIC" ? "SYNTHETIC" : "DATA";
  return (
    <fieldset>
      <legend className="sr-only">Pick the site to simulate</legend>
      <div className="grid gap-4 md:grid-cols-3">
        {response.sites.map((item) => {
          const score = isScored(item) ? item : null;
          const checked = item.index === selected;
          const id = `site-card-${item.index}`;
          return (
            <label
              key={item.index}
              htmlFor={id}
              className={cn(
                "flex cursor-pointer flex-col gap-3 rounded-xl border bg-card p-4 transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring",
                checked ? "border-accent shadow-[0_0_0_1px_var(--accent)]" : "border-border hover:border-rule-strong",
                !score && "cursor-default",
              )}
            >
              <input
                id={id}
                type="radio"
                name="selected-site"
                className="sr-only"
                checked={checked}
                disabled={!score}
                onChange={() => onSelect(item.index)}
              />
              <span className="flex items-start justify-between gap-2">
                <span className="flex items-center gap-2 font-semibold leading-snug">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-viz-1 text-xs text-accent-ink" aria-hidden="true">
                    {item.index + 1}
                  </span>
                  {item.label}
                </span>
                {checked ? (
                  <span className="shrink-0 rounded-full bg-accent px-2 py-0.5 text-[0.6875rem] font-semibold text-accent-ink">
                    {simulationAvailable ? "Simulated below" : "Selected"}
                  </span>
                ) : null}
              </span>
              {score ? (
                <>
                  {isNumber(score.overall_score) ? (
                    <span className="flex items-end gap-2">
                      <span className="text-4xl font-semibold tracking-tight">{formatScore(score.overall_score)}</span>
                      <span className="pb-1 text-sm text-muted-foreground">of 100</span>
                    </span>
                  ) : (
                    <span className="text-lg font-semibold">Not scored: too little data here</span>
                  )}
                  <span className="flex flex-wrap gap-1.5">
                    {isNumber(score.coverage_score) ? <CoverageBadge coverage={score.coverage_score} /> : null}
                    {/* No data behind any factor: say UNKNOWN, not "public data". */}
                    <DataStatusChip status={isNumber(score.coverage_score) && score.coverage_score > 0 ? dataStatus : "UNKNOWN"} />
                  </span>
                  <span className="text-xs text-muted-foreground">
                    Ranks this location within {response.metro_name}; it does not predict sales.
                    {score.tract_geoid ? ` Tract ${score.tract_geoid}${score.district ? `, ${score.district}` : ""}.` : ""}
                  </span>
                  {topFactors(score).length > 0 ? (
                    <span className="flex flex-col gap-1.5 border-t border-border pt-3">
                      <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">Top reasons</span>
                      {topFactors(score).map((factor) => (
                        <span key={factor.name} className="text-sm leading-snug">
                          <span className="font-medium">{factor.description}:</span>{" "}
                          <span className="figure-nums">{formatScore(factor.score)}</span>
                          <span className="text-muted-foreground"> of 100 (weight {formatShare(factor.weight, 0)})</span>
                        </span>
                      ))}
                    </span>
                  ) : null}
                  {score.watch_out ? <ReasonText reason={score.watch_out} lead="Watch:" className="text-stated" /> : null}
                </>
              ) : (
                <span className="flex items-start gap-2 rounded-lg border border-dashed border-unknown-line bg-unknown-soft p-3 text-sm text-unknown">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  {item.error ?? "This site could not be scored."}
                </span>
              )}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function SiteCardsSkeleton({ count }: { count: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-3" role="status" aria-busy="true" aria-label="Scoring your sites">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} className="h-72 rounded-xl" />
      ))}
    </div>
  );
}

function SourceLine({ source }: { source: SourceRecord }) {
  const parts = [source.publisher, source.dataset, source.vintage].filter(Boolean).join(" · ");
  const isLink = /^https?:\/\//.test(source.url);
  return (
    <span className="text-xs leading-relaxed text-muted-foreground">
      {parts}
      {source.retrieved_at ? ` · retrieved ${source.retrieved_at.slice(0, 10)}` : ""}
      {isLink ? (
        <>
          {" "}
          <a href={source.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-accent underline underline-offset-2">
            source
            <ExternalLink className="size-3" aria-hidden="true" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </>
      ) : null}
    </span>
  );
}

/**
 * The demand estimate (MODEL_ESTIMATE): P10 / P50 / P90, hard bounds, and every input on request. When the model
 * could not estimate anything (no data), the chip says UNKNOWN. Values are rounded to whole units from 10 up: the
 * model does not know demand to a tenth of a machine turn.
 */
function DemandPanel({ demand }: { demand: DemandOut }) {
  const known = [demand.p10, demand.p50, demand.p90].some(isNumber);
  return (
    <section aria-labelledby="demand-title" className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-2">
        <h3 id="demand-title" className="flex items-center gap-2 font-semibold">
          <Gauge className="size-4 text-accent" aria-hidden="true" />
          Demand estimate
        </h3>
        <EvidenceChip status={known ? "MODEL_ESTIMATE" : "UNKNOWN"} />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{demand.formula_description}</p>
      <dl className="figure-nums grid grid-cols-3 gap-2 text-center">
        {(
          [
            ["P10", demand.p10],
            ["P50", demand.p50],
            ["P90", demand.p90],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="flex flex-col gap-1 rounded-lg bg-paper-sunken p-2.5">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-base font-semibold">{isNumber(value) ? formatEstimate(value) : "Unknown"}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        In {demand.unit}, {demand.area}.{" "}
        {isNumber(demand.low) && isNumber(demand.high)
          ? `Hard bounds (every rate at its low or high end at once): ${formatEstimate(demand.low)} to ${formatEstimate(demand.high)}. `
          : "Unknown here: the data the estimate needs is missing, so it is not guessed. "}
        Basis: {demand.basis}.
        {demand.complete ? "" : ` Incomplete: ${demand.missing.join("; ")}.`}
      </p>
      <details className="min-w-0">
        <summary className="cursor-pointer text-xs font-medium text-accent">How the estimate is built ({demand.inputs.length} inputs)</summary>
      <ul className="mt-1 flex flex-col divide-y divide-border">
        {demand.inputs.map((input) => (
          <li key={input.name} className="flex flex-col gap-1 py-2.5">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="text-sm font-medium">{humanize(input.name)}</p>
              <DataStatusChip status={input.status} />
            </div>
            <p className="figure-nums text-sm">
              {isNumber(input.value) ? formatWithUnit(input.value, input.unit) : null}
              {isNumber(input.low) && isNumber(input.high) ? (
                <span className="block text-xs text-muted-foreground">Range {formatRange(input.low, input.high, input.unit)}</span>
              ) : null}
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">{input.reason}</p>
          </li>
        ))}
      </ul>
      </details>
    </section>
  );
}

/** Every factor of the selected site, then the demand estimate with every input. */
export function SiteDetail({ site, scoringMethod }: { site: SiteScoreOut; scoringMethod: string }) {
  const demand = site.demand;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[1.25fr_1fr]">
      <section aria-labelledby="factors-title" className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-col gap-1">
          <h3 id="factors-title" className="font-semibold">
            Location factors for {site.label}
          </h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {scoringMethod}. Factors without data are shown as unknown and left out of the score, never filled in.
          </p>
        </div>
        <ul className="flex flex-col divide-y divide-border">
          {site.factors.map((factor) => (
            <li key={factor.name} className="flex flex-col gap-1.5 py-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-sm font-medium">{factor.description}</p>
                <DataStatusChip status={factor.status} />
              </div>
              <p className="figure-nums text-sm">
                {isNumber(factor.raw) ? formatWithUnit(factor.raw, factor.unit) : "Unknown"}
                <span className="text-muted-foreground"> · {factor.area}</span>
              </p>
              <p className="figure-nums text-xs text-muted-foreground">
                {factor.used_in_overall
                  ? `Score ${formatScore(factor.score)} of 100 · weight ${formatShare(factor.weight, 0)}`
                  : `Not in the score · weight ${formatShare(factor.weight, 0)}`}
              </p>
              <details className="min-w-0 text-xs">
                <summary className="cursor-pointer font-medium text-accent">How it was measured, and the source</summary>
                <div className="mt-1.5 flex flex-col gap-1.5">
                  <p className="leading-relaxed text-muted-foreground">{factor.reason}</p>
                  {factor.sources.map((source) => (
                    <SourceLine key={`${source.source_id}-${source.dataset}`} source={source} />
                  ))}
                  {factor.sources.length === 0 ? <p className="text-muted-foreground">No dataset behind this value.</p> : null}
                </div>
              </details>
            </li>
          ))}
        </ul>
        {site.notes.length > 0 ? (
          <details className="min-w-0 text-xs">
            <summary className="cursor-pointer font-medium text-accent">Notes on this score ({site.notes.length})</summary>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 leading-relaxed text-muted-foreground">
              {site.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      {demand ? (
        <DemandPanel demand={demand} />
      ) : (
        <section aria-labelledby="demand-title" className="flex flex-col gap-3 rounded-xl border border-dashed border-unknown-line bg-unknown-soft p-4 sm:p-5">
          <div className="flex items-start justify-between gap-2">
            <h3 id="demand-title" className="font-semibold">
              Demand estimate
            </h3>
            <EvidenceChip status="UNKNOWN" />
          </div>
          <p className="text-sm text-unknown">
            Unknown: the data this estimate needs is missing for this site, so it is not guessed. The simulation cannot run
            here until it is available (limited mode).
          </p>
        </section>
      )}
    </div>
  );
}
