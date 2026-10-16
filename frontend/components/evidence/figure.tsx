import { FileSearch } from "lucide-react";
import type { ReactNode } from "react";

import { DataStatusChip } from "@/components/evidence/data-status-chip";
import { Button } from "@/components/ui/button";
import type { EvidenceStatus } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/**
 * A number with its evidence: label, value (tabular numerals), status chip, an optional note and an optional
 * "Source" button. The button calls `onShowSource(button)`, which opens the evidence drawer for this figure; the
 * drawer gives keyboard focus back to that button when it closes (WCAG 2.4.3), so callers keep the element.
 *
 * status: an evidence status, or one of the Studio's own (DERIVED, PLAN_CHANGE, DATA, ...; see DataStatusChip).
 *
 * layout="tile": stacked, for grids of key figures.
 * layout="row":  one ledger line (label left, value right), for file views.
 */
export interface FigureProps {
  label: string;
  value: ReactNode;
  status: EvidenceStatus | string;
  note?: ReactNode;
  onShowSource?: (trigger: HTMLButtonElement) => void;
  layout?: "tile" | "row";
  className?: string;
}

function SourceButton({ label, onClick }: { label: string; onClick: (trigger: HTMLButtonElement) => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={(event) => onClick(event.currentTarget)}
      className="h-7 gap-1 px-2 text-xs text-accent hover:text-accent-hover"
      aria-label={`Show the source for ${label}`}
    >
      <FileSearch aria-hidden="true" />
      Source
    </Button>
  );
}

export function Figure({ label, value, status, note, onShowSource, layout = "tile", className }: FigureProps) {
  if (layout === "row") {
    return (
      <div role="group" aria-label={label} className={cn("grid grid-cols-[1fr_auto] items-start gap-x-4 gap-y-1.5 py-3", className)}>
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="figure-nums text-right text-base font-semibold text-foreground sm:text-lg">{value}</p>
        {note ? <div className="col-span-2 text-xs text-muted-foreground sm:col-span-1">{note}</div> : <span className="hidden sm:block" />}
        <div className="col-span-2 flex items-center justify-between gap-2 sm:col-span-1 sm:justify-end">
          <DataStatusChip status={status} />
          {onShowSource ? <SourceButton label={label} onClick={onShowSource} /> : null}
        </div>
      </div>
    );
  }

  return (
    <div role="group" aria-label={label} className={cn("flex flex-col gap-2 rounded-lg border border-border bg-card p-4", className)}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="figure-nums text-2xl font-semibold tracking-tight text-foreground">{value}</p>
      <div className="flex flex-wrap items-center gap-2">
        <DataStatusChip status={status} />
        {onShowSource ? <SourceButton label={label} onClick={onShowSource} /> : null}
      </div>
      {note ? <div className="text-xs leading-relaxed text-muted-foreground">{note}</div> : null}
    </div>
  );
}
