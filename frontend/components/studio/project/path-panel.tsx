"use client";

import { ArrowRight, CheckCircle2, CircleSlash, FileCheck2, Route, ShieldCheck, TriangleAlert, Wand2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { isEdited, TargetEditor, type TargetEdits } from "@/components/studio/project/target-editor";
import { SimulationLabel } from "@/components/studio/simulation-label";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { parseAmount } from "@/components/studio/wizard/draft";
import { formatNumber, formatPercent, formatRatio, formatShare, formatUsd, humanize, isNumber, joinWords } from "@/lib/format";
import type { ChangeSet, FieldChange, OwnerCashBound, Outcome, PathResponse, PathTarget } from "@/lib/studio-types";
import { cn } from "@/lib/utils";

/**
 * Path to approval (Studio S5, quick mode): the smallest plan changes that reach the three simulated targets.
 * Each change-set card shows EVERY figure that changes (also the ones that balance a lever: more owner cash lowers the
 * loan), the before -> after probabilities, what a lender would need to back each changed figure, the startup
 * program rules the changed plan still fails, the server's plain-language explanation, what stays the same, and
 * "Apply this change" (a new plan version, re-simulated on the same seed, so it shows the same "after" numbers).
 * The status line is the engine's own message: a "no path" answer says what was tried and that the search is not
 * exhaustive. Reaching the targets is not an approval; the card says so.
 */

const OUTCOME_ROWS = [
  { key: "p_dscr_at_least_target_year2", label: "Year-2 DSCR target met", better: "higher" },
  { key: "p_stressed_dscr_at_least_min_year2", label: "Passes the lender's revenue stress", better: "higher" },
  { key: "p_cash_negative_within_months", label: "Cash runs out", better: "lower" },
] as const;

const TARGET_NAMES: Record<string, string> = {
  dscr_year2: "the year-2 DSCR target",
  stressed_dscr_year2: "the revenue-stress target",
  cash_negative: "the cash-runway target",
};

function targetName(key: string): string {
  return TARGET_NAMES[key] ?? humanize(key);
}

function changeValue(change: { unit: string }, value: number | string): string {
  if (typeof value === "string") {
    return value;
  }
  if (change.unit === "USD") {
    return formatUsd(value);
  }
  if (change.unit.startsWith("USD")) {
    return formatUsd(value, { cents: true });
  }
  return `${formatNumber(value, 2)} ${change.unit}`;
}

/** One figure before -> after; a computed figure (the reserve) says so. */
function FieldChangeRow({ change }: { change: FieldChange }) {
  return (
    <li className="flex flex-col gap-1 rounded-lg bg-paper-sunken px-3 py-2">
      <span className="text-sm font-medium">
        {change.label}
        {change.writes_plan ? null : <span className="font-normal text-muted-foreground"> (calculated from the others)</span>}
      </span>
      <span className="figure-nums flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground line-through decoration-rule-strong">{changeValue(change, change.from_value)}</span>
        <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="sr-only">to</span>
        <span className="font-semibold">{changeValue(change, change.to_value)}</span>
      </span>
    </li>
  );
}

function OutcomeTable({ before, after, caption }: { before: Outcome; after: Outcome; caption: string }) {
  return (
    <table className="figure-nums w-full text-left text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="text-xs text-muted-foreground">
          <th scope="col" className="pb-1 font-medium">
            Simulated chance
          </th>
          <th scope="col" className="pb-1 text-right font-medium">
            Now
          </th>
          <th scope="col" className="pb-1 text-right font-medium">
            <span className="sr-only">changes to</span>
          </th>
          <th scope="col" className="pb-1 text-right font-medium">
            After
          </th>
        </tr>
      </thead>
      <tbody>
        {OUTCOME_ROWS.map((row) => {
          const from = before[row.key];
          const to = after[row.key];
          const improved = row.better === "higher" ? to > from : to < from;
          return (
            <tr key={row.key} className="border-t border-border">
              <th scope="row" className="py-1.5 pr-2 font-normal">
                {row.label}
              </th>
              <td className="py-1.5 text-right text-muted-foreground">{formatShare(from)}</td>
              <td className="px-1 py-1.5 text-right" aria-hidden="true">
                <ArrowRight className="ml-auto size-3.5 text-muted-foreground" />
              </td>
              <td className={cn("py-1.5 text-right font-semibold", improved ? "text-foreground" : "text-muted-foreground")}>{formatShare(to)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ChangeSetCard({
  changeSet,
  title,
  onApply,
  applying,
  disabled,
}: {
  changeSet: ChangeSet;
  title: string;
  onApply?: (changeSet: ChangeSet) => void;
  applying: boolean;
  disabled: boolean;
}) {
  const checks = changeSet.plan_after.checks;
  return (
    <article className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 sm:p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-serif text-lg font-semibold">{title}</h3>
        {changeSet.meets_target ? (
          <Badge variant="soft" className="max-w-full whitespace-normal">
            <CheckCircle2 aria-hidden="true" />
            Reaches all three targets (simulated)
          </Badge>
        ) : (
          <Badge variant="muted" className="max-w-full whitespace-normal">
            <CircleSlash aria-hidden="true" />
            Still misses {joinWords(changeSet.after.targets_missed.map(targetName))}
          </Badge>
        )}
      </header>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">What changes in your plan</p>
        <ul className="flex flex-col gap-2">
          {changeSet.field_changes.map((change) => (
            <FieldChangeRow key={change.field} change={change} />
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-1">
        <OutcomeTable before={changeSet.before} after={changeSet.after} caption={`${title}: simulated chances now and after the change`} />
        <SimulationLabel text={changeSet.after.label} />
      </div>

      {changeSet.evidence_needed.length > 0 ? (
        <div className="flex flex-col gap-1.5 text-sm">
          <p className="flex items-center gap-1.5 font-medium">
            <FileCheck2 className="size-4 text-accent" aria-hidden="true" />
            What a lender would need before these figures count
          </p>
          <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
            {changeSet.evidence_needed.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">Until then they are suggested changes from a simulation, not quotes and not verified.</p>
        </div>
      ) : null}

      {changeSet.program_rules_not_met.length > 0 ? (
        <div className="flex flex-col gap-1.5 rounded-lg border border-dashed border-stated-line bg-stated-soft p-3 text-sm">
          <p className="flex items-center gap-1.5 font-medium text-stated">
            <TriangleAlert className="size-4" aria-hidden="true" />
            Startup program rules the changed plan still does not meet (SYNTHETIC program)
          </p>
          <ul className="list-disc space-y-1 pl-5 text-foreground/85">
            {changeSet.program_rules_not_met.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="min-w-0 text-sm">
        <summary className="cursor-pointer font-medium text-accent">Read the full explanation</summary>
        <div className="mt-2 flex flex-col gap-2 leading-relaxed text-muted-foreground">
          <p>{changeSet.explanation}</p>
          <p>{changeSet.unchanged}</p>
          <p className="figure-nums">
            Equity injection after the change: {formatPercent(checks.equity_injection_pct, 1)} (the SYNTHETIC program asks for at
            least {formatPercent(checks.equity_minimum_pct, 1)}). Loan within program limits: {checks.loan_within_limits ? "yes" : "no"}.
            Term within limits: {checks.term_within_limits ? "yes" : "no"}. Year-2 DSCR the lender&apos;s way:{" "}
            {formatRatio(changeSet.before.projected_dscr_base_case)} to {formatRatio(changeSet.after.projected_dscr_base_case)}.
          </p>
        </div>
      </details>

      {onApply ? (
        <div className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-relaxed text-muted-foreground" id={`apply-note-${changeSet.rank}`}>
            {changeSet.apply.supported
              ? "Applying saves a new version of your plan with every figure listed above (marked as suggested changes, not as your own numbers) and runs everything again."
              : `This change cannot be applied from here: ${changeSet.apply.reason ?? "the server did not say why"}.`}
          </p>
          <Button
            type="button"
            onClick={() => onApply(changeSet)}
            disabled={disabled || applying || !changeSet.apply.supported}
            aria-describedby={`apply-note-${changeSet.rank}`}
            className="self-start sm:self-auto"
          >
            <Wand2 aria-hidden="true" />
            {applying ? "Applying..." : "Apply this change"}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

/**
 * The bound on extra owner cash the search used (the engine's default is a placeholder for the founder's own answer)
 * and a field to set it: "the most extra cash of my own I could really add". Empty = back to the default.
 */
function OwnerCashBoundEditor({
  bound,
  value,
  onChange,
  running,
}: {
  bound: OwnerCashBound | null;
  value: number | null;
  onChange: (value: number | null) => void;
  running: boolean;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (text.trim() === "") {
      setError(null);
      onChange(null);
      return;
    }
    const amount = parseAmount(text);
    if (amount === null || amount < 0) {
      setError("Enter a dollar amount of 0 or more, or leave it empty for the default.");
      return;
    }
    setError(null);
    onChange(amount);
  }

  return (
    <section aria-labelledby="owner-cash-title" className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4">
      <h3 id="owner-cash-title" className="text-sm font-semibold">
        Extra cash of your own the search may add
      </h3>
      {bound ? (
        <p className="figure-nums text-sm">
          Up to <strong>{formatUsd(bound.extra)}</strong> more ({formatUsd(bound.current)} now, at most {formatUsd(bound.high)}){" "}
          <EvidenceChip status={bound.source === "founder" ? "USER_STATED" : "ASSUMPTION"} />
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">The search did not move owner cash for this plan.</p>
      )}
      {bound ? <p className="text-xs leading-relaxed text-muted-foreground">{bound.note}</p> : null}
      <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="max-extra-cash">The most extra cash you could really add</Label>
          <div className="relative">
            <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-muted-foreground" aria-hidden="true">
              $
            </span>
            <Input
              id="max-extra-cash"
              inputMode="decimal"
              autoComplete="off"
              value={text}
              onChange={(event) => setText(event.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "max-extra-cash-error" : undefined}
              className="figure-nums pl-7 sm:w-48"
              placeholder="default"
            />
          </div>
        </div>
        <Button type="submit" variant="outline" size="sm" disabled={running}>
          {running ? "Searching..." : "Search again with this bound"}
        </Button>
      </form>
      {error ? (
        <p id="max-extra-cash-error" className="text-sm font-medium text-conflicting">
          {error}
        </p>
      ) : null}
    </section>
  );
}

export function PathPanel({
  result,
  onApply,
  applyingRank,
  canApply,
  defaults,
  edits,
  onEditTargets,
  maxExtraCash,
  onMaxExtraCash,
  searching,
}: {
  result: PathResponse;
  onApply: (changeSet: ChangeSet) => void;
  applyingRank: number | null;
  canApply: boolean;
  /** The default targets (from /api/studio/options), for the editor's "Default: 80%" hints. */
  defaults: PathTarget | null;
  /** The founder's edits to the target shares, or null for the defaults. */
  edits: TargetEdits | null;
  onEditTargets: (edits: TargetEdits | null) => void;
  /** The founder's bound on extra owner cash (USD), or null for the server's default. */
  maxExtraCash: number | null;
  onMaxExtraCash: (value: number | null) => void;
  /** True while the search runs again (new targets or a new plan version). */
  searching: boolean;
}) {
  const target = result.target;
  // Each target: the share of futures (a planning ASSUMPTION, editable) and the threshold it applies to (from the
  // SYNTHETIC guideline or the simulator). Both sources are shown, as the server sent them.
  const targets = [
    {
      text: `Year-2 DSCR at least ${target.dscr_min.toFixed(2)} in at least ${formatShare(target.p_dscr_min, 0)} of simulated futures`,
      shareField: "p_dscr_min",
      ruleFields: ["dscr_min"],
    },
    {
      text: `Year-2 DSCR at least ${target.stressed_dscr_min.toFixed(2)} with revenue ${formatShare(target.stress_revenue_share, 0)} lower, in at least ${formatShare(target.p_stressed_min, 0)} of futures`,
      shareField: "p_stressed_min",
      ruleFields: ["stress_revenue_share", "stressed_dscr_min"],
    },
    {
      text: `Cash below $0 within ${target.cash_months} months in at most ${formatShare(target.p_cash_negative_max, 0)} of futures`,
      shareField: "p_cash_negative_max",
      ruleFields: ["cash_months"],
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <div
        className={cn(
          "flex items-start gap-3 rounded-xl border p-4",
          result.status === "no_path_within_bounds" ? "border-dashed border-unknown-line bg-unknown-soft" : "border-accent/30 bg-accent-soft",
        )}
        role="status"
      >
        <Route className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="font-semibold">
            {
              {
                already_meets_target: "Your plan already reaches the three simulated targets.",
                paths_found: `Found ${result.change_sets.length} way${result.change_sets.length === 1 ? "" : "s"} to reach the simulated targets.`,
                no_path_within_bounds: "The search found no path within your bounds.",
              }[result.status]
            }
          </p>
          {/* The engine's own words: what was tried, "not exhaustive", or that it stopped early on its budget. */}
          <p className="text-sm leading-relaxed text-foreground/85">{result.message}</p>
        </div>
      </div>

      <p className="text-sm font-medium">{result.guardrail_note}</p>

      <section aria-labelledby="targets-title" className="flex flex-col gap-2">
        <h3 id="targets-title" className="text-sm font-semibold">
          The three targets (planning targets you can edit, not lender rules)
        </h3>
        <ul className="flex flex-col gap-2">
          {targets.map((item) => (
            <li key={item.shareField} className="flex flex-wrap items-center gap-2 text-sm">
              <EvidenceChip status={isEdited(target, item.shareField) ? "USER_STATED" : "ASSUMPTION"} />
              {item.text}
            </li>
          ))}
        </ul>
        <details className="min-w-0 text-sm">
          <summary className="cursor-pointer font-medium text-accent">Where the targets come from</summary>
          <ul className="mt-2 flex flex-col gap-2 text-xs leading-relaxed text-muted-foreground">
            {targets.map((item) => {
              const edited = isEdited(target, item.shareField);
              const ruleSources = [...new Set(item.ruleFields.map((field) => target.sources[field]).filter(Boolean))];
              return (
                <li key={item.shareField} className="flex flex-col gap-0.5">
                  <span className="font-medium text-foreground">{item.text}</span>
                  <span>Share of futures: {edited ? "edited by you" : (target.sources[item.shareField] ?? "not stated")}</span>
                  {ruleSources.length > 0 ? <span>Threshold: {ruleSources.join("; ")}</span> : null}
                </li>
              );
            })}
          </ul>
        </details>
        <TargetEditor key={JSON.stringify(edits ?? {})} target={target} defaults={defaults} edits={edits} onRun={onEditTargets} running={searching} />
      </section>

      <OwnerCashBoundEditor key={String(maxExtraCash)} bound={result.owner_cash_bound} value={maxExtraCash} onChange={onMaxExtraCash} running={searching} />

      {result.change_sets.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {result.change_sets.map((changeSet) => (
            <ChangeSetCard
              key={changeSet.rank}
              changeSet={changeSet}
              title={`Option ${changeSet.rank}: ${changeSet.changes.map((change) => change.label.toLowerCase()).join(" and ")}`}
              onApply={canApply ? onApply : undefined}
              applying={applyingRank === changeSet.rank}
              disabled={applyingRank !== null}
            />
          ))}
        </div>
      ) : null}

      {result.closest && result.change_sets.length === 0 ? (
        <div className="max-w-3xl">
        <ChangeSetCard
          changeSet={result.closest}
          title="The closest result found"
          onApply={canApply ? onApply : undefined}
          applying={applyingRank === result.closest.rank}
          disabled={applyingRank !== null}
        />
        </div>
      ) : null}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        {Object.keys(result.levers_not_available).length > 0 ? (
          <section aria-labelledby="levers-off-title" className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4">
            <h3 id="levers-off-title" className="text-sm font-semibold">
              Changes not tried, and why
            </h3>
            <ul className="flex flex-col gap-1.5 text-sm">
              {Object.entries(result.levers_not_available).map(([lever, reason]) => (
                <li key={lever}>
                  <span className="font-medium">{humanize(lever)}:</span> <span className="text-muted-foreground">{reason}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <section aria-labelledby="guardrails-title" className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4">
          <h3 id="guardrails-title" className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="size-4 text-accent" aria-hidden="true" />
            What the search never changes
          </h3>
          <p className="text-sm text-muted-foreground">
            Facts (credit score, experience, other debts, verified figures), the site&apos;s demand estimate, the note rate and every
            assumption range stay as they are. Only your own plan choices move, inside their bounds.
          </p>
          <details className="min-w-0 text-sm">
            <summary className="cursor-pointer font-medium text-accent">All {result.guardrails.length} rules</summary>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-muted-foreground">
              {result.guardrails.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
          </details>
        </section>
      </div>

      {result.skipped_candidates.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted-foreground">
          {result.skipped_candidates.map((item) => (
            <li key={item}>Site not tried: {item}</li>
          ))}
        </ul>
      ) : null}
      {result.notes.length > 0 ? (
        <details className="min-w-0 text-xs">
          <summary className="cursor-pointer font-medium text-accent">Notes on the search ({result.notes.length})</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 leading-relaxed text-muted-foreground">
            {result.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {isNumber(result.before.projected_dscr_base_case) ? (
        <p className="figure-nums text-xs text-muted-foreground">
          Year-2 DSCR on the median, the lender&apos;s way (NSP-4.1): {formatRatio(result.before.projected_dscr_base_case)}; under the revenue
          stress: {formatRatio(result.before.stressed_dscr_base_case)}.
        </p>
      ) : null}
    </div>
  );
}
