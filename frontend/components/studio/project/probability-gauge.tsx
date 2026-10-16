import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { SimulationLabel } from "@/components/studio/simulation-label";
import { formatShare, isNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * One simulated probability as a meter: the share in big type, the same share as "N of M simulated futures", a
 * plain-language sentence, the "simulated, based on stated assumptions" label (Studio rule E: on every simulated
 * number), the MODEL_ESTIMATE chip and, when known, the target it is compared with (labeled as a
 * planning target: the targets are editable design choices, not lender rules; their sources are in the path panel).
 *
 * The fill is the neutral series color, not a good/bad color: Studio explains, it does not grade the plan.
 * The meter is a <meter>-like div with role="meter" and aria-valuetext, so screen readers read the same sentence.
 */
export function ProbabilityGauge({
  title,
  value,
  paths,
  sentence,
  target,
  className,
}: {
  title: string;
  value: number | null | undefined;
  /** Number of simulated futures behind the share (n_paths). */
  paths: number;
  /** "In {count} of {paths} simulated futures, ..." (count and paths are filled in). */
  sentence: (count: number, paths: number) => string;
  target?: { text: string; share: number; direction: "at_least" | "at_most" } | null;
  className?: string;
}) {
  const known = isNumber(value);
  const percent = known ? Math.max(0, Math.min(100, value * 100)) : 0;
  const count = known ? Math.round(value * paths) : 0;
  const text = known ? sentence(count, paths) : "Not available: the simulation did not report this value.";

  return (
    <div className={cn("flex flex-col gap-3 rounded-xl border border-border bg-card p-4", className)}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-medium leading-snug text-foreground">{title}</h3>
        <EvidenceChip status="MODEL_ESTIMATE" />
      </div>
      <p className="text-4xl font-semibold tracking-tight text-foreground">{known ? formatShare(value) : "Not available"}</p>
      <div
        role="meter"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? Number(percent.toFixed(1)) : undefined}
        aria-valuetext={text}
        className="relative h-2.5 w-full rounded-full bg-accent-soft"
      >
        <div className="absolute inset-y-0 left-0 rounded-full bg-viz-1" style={{ width: `${percent}%` }} />
        {target ? (
          <div
            className="absolute -inset-y-1 w-0.5 rounded-full bg-ink"
            style={{ left: `calc(${Math.max(0, Math.min(100, target.share * 100))}% - 1px)` }}
            aria-hidden="true"
          />
        ) : null}
      </div>
      <p className="text-sm leading-relaxed text-muted-foreground">{text}</p>
      <SimulationLabel />
      {target ? (
        <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
          <span className="mt-1 inline-block h-2.5 w-0.5 shrink-0 rounded-full bg-ink" aria-hidden="true" />
          <span>
            {target.text} A planning target (see the path to approval below), not a lender rule.
          </span>
        </p>
      ) : null}
    </div>
  );
}
