import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { EVIDENCE_STATUSES, EVIDENCE_STATUS_INFO } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/** All six evidence statuses with their meaning, as a definition list. */
export function EvidenceLegend({ className }: { className?: string }) {
  return (
    <dl
      className={cn(
        "grid divide-y divide-border rounded-lg border border-border bg-card sm:grid-cols-2 sm:gap-3 sm:divide-y-0 sm:border-0 sm:bg-transparent",
        className,
      )}
    >
      {EVIDENCE_STATUSES.map((status) => (
        <div key={status} className="flex flex-col gap-2 p-4 sm:rounded-lg sm:border sm:border-border sm:bg-card">
          <dt>
            <EvidenceChip status={status} size="md" />
          </dt>
          <dd className="text-sm leading-relaxed text-muted-foreground">{EVIDENCE_STATUS_INFO[status].description}</dd>
        </div>
      ))}
    </dl>
  );
}
