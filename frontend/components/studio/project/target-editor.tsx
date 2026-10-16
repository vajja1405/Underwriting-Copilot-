"use client";

import { RotateCcw, Search, SlidersHorizontal } from "lucide-react";
import { useState, type FormEvent } from "react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { AmountField } from "@/components/studio/wizard/amount-field";
import { Button } from "@/components/ui/button";
import { formatShare } from "@/lib/format";
import type { PathTarget } from "@/lib/studio-types";

/**
 * "Edit the targets": the founder sets how sure the plan should be (the three shares of simulated futures). Those
 * shares are planning ASSUMPTIONS (a design choice, not a lender rule), so they are editable. The DSCR thresholds,
 * the revenue stress and the cash window come from the SYNTHETIC guideline and the simulator, so they stay fixed
 * here: changing them would also make the simulation's numbers above mean something else.
 *
 * The edits are sent to POST path-to-approval as `targets` (only the shares that differ from the defaults); the
 * server marks each edited target "edited by the user" and checks the bounds again.
 */

/** The target fields this editor may change. */
export type TargetEdits = Partial<Pick<PathTarget, "p_dscr_min" | "p_stressed_min" | "p_cash_negative_max">>;

type ShareField = keyof TargetEdits;

const SHARE_FIELDS: ShareField[] = ["p_dscr_min", "p_stressed_min", "p_cash_negative_max"];

type Draft = Record<ShareField, string>;

/** A share 0-1 as a percent string for the input ("0.8" -> "80"). */
function toPercentText(share: number): string {
  return String(Math.round(share * 1000) / 10);
}

function draftFrom(target: PathTarget): Draft {
  return {
    p_dscr_min: toPercentText(target.p_dscr_min),
    p_stressed_min: toPercentText(target.p_stressed_min),
    p_cash_negative_max: toPercentText(target.p_cash_negative_max),
  };
}

/** A percent between 0 and 100, or null when the text is not one. */
function parsePercent(text: string): number | null {
  const cleaned = text.trim().replace(/%$/, "").trim();
  if (cleaned === "" || !/^\d+(\.\d+)?$/.test(cleaned)) {
    return null;
  }
  const value = Number(cleaned);
  return value >= 0 && value <= 100 ? value : null;
}

/** True when the founder changed this field from the default target. */
export function isEdited(target: PathTarget, field: string): boolean {
  return target.sources[field] === "edited by the user";
}

export function TargetEditor({
  target,
  defaults,
  edits,
  onRun,
  running,
}: {
  /** The targets the shown result used. */
  target: PathTarget;
  /** The default targets from /api/studio/options (null if the options did not load). */
  defaults: PathTarget | null;
  /** The current edits (null = the defaults). */
  edits: TargetEdits | null;
  onRun: (edits: TargetEdits | null) => void;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(target));
  const [errors, setErrors] = useState<Partial<Record<ShareField, string>>>({});

  function openEditor() {
    setDraft(draftFrom(target));
    setErrors({});
    setOpen(true);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const nextErrors: Partial<Record<ShareField, string>> = {};
    const nextEdits: TargetEdits = {};
    for (const field of SHARE_FIELDS) {
      const percent = parsePercent(draft[field]);
      if (percent === null) {
        nextErrors[field] = "Enter a percent from 0 to 100.";
        continue;
      }
      const share = percent / 100;
      const fallback = defaults?.[field];
      if (fallback === undefined || Math.abs(share - fallback) > 1e-9) {
        nextEdits[field] = share;
      }
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }
    onRun(Object.keys(nextEdits).length > 0 ? nextEdits : null);
    setOpen(false);
  }

  function defaultNote(field: ShareField): string {
    const fallback = defaults?.[field];
    return fallback === undefined ? "" : ` Default: ${formatShare(fallback, 0)}.`;
  }

  const fields: { field: ShareField; label: string; hint: string }[] = [
    {
      field: "p_dscr_min",
      label: `Year-2 DSCR at least ${target.dscr_min.toFixed(2)} in at least this share of futures`,
      hint: `Higher asks more of the plan.${defaultNote("p_dscr_min")}`,
    },
    {
      field: "p_stressed_min",
      label: `With revenue ${formatShare(target.stress_revenue_share, 0)} lower, DSCR at least ${target.stressed_dscr_min.toFixed(2)} in at least this share of futures`,
      hint: `Higher asks more of the plan.${defaultNote("p_stressed_min")}`,
    },
    {
      field: "p_cash_negative_max",
      label: `Cash below $0 within ${target.cash_months} months in at most this share of futures`,
      hint: `Lower asks more of the plan.${defaultNote("p_cash_negative_max")}`,
    },
  ];

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={openEditor} aria-expanded={false} aria-controls="target-editor">
          <SlidersHorizontal aria-hidden="true" />
          Edit the targets
        </Button>
        {edits ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => onRun(null)} disabled={running}>
            <RotateCcw aria-hidden="true" />
            Back to the default targets
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <form
      id="target-editor"
      onSubmit={submit}
      noValidate
      className="flex flex-col gap-4 rounded-xl border border-dashed border-assumption-line bg-card p-4"
      aria-labelledby="target-editor-title"
    >
      <div className="flex flex-col gap-1">
        <p id="target-editor-title" className="flex flex-wrap items-center gap-2 font-semibold">
          How sure should the plan be?
          <EvidenceChip status="ASSUMPTION" />
        </p>
        <p className="text-sm leading-relaxed text-muted-foreground">
          These shares are planning targets you can change; they are not lender rules. The DSCR thresholds and the revenue
          stress come from the SYNTHETIC guideline and stay as they are. A changed target is marked as yours.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {fields.map(({ field, label, hint }) => (
          <AmountField
            key={field}
            id={`target-${field}`}
            label={label}
            hint={hint}
            value={draft[field]}
            onChange={(value) => setDraft((current) => ({ ...current, [field]: value }))}
            error={errors[field]}
            suffix="%"
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={running}>
          <Search aria-hidden="true" />
          Search again with these targets
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
