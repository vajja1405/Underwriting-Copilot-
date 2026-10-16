import { BookOpen, Calculator, Database, FlaskConical, Wand2, type LucideIcon } from "lucide-react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { isEvidenceStatus, type EvidenceStatus } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/**
 * The chip for a status sent by the Founder Studio code (SiteIQ, the data fabric, the simulator).
 *
 * Studio values carry a few statuses that are not ledger evidence statuses: DATA (a public dataset), SOURCED (a
 * cited publication), SYNTHETIC (the generated demo city) and DATA_X_ASSUMPTION (public data converted with a stated
 * assumption). They are shown with their own label and icon, never re-labeled as VERIFIED: a public statistic is not
 * a checked document. Two more describe plan figures: DERIVED (calculated by the server from the figures next to it)
 * and PLAN_CHANGE (written by "Apply this change": a simulated suggestion, not the founder's own number, until the
 * evidence it needs exists). FOUNDER_INPUT is the founder's own number, so it uses the USER_STATED chip. Everything
 * that is a ledger status (ASSUMPTION, MODEL_ESTIMATE, UNKNOWN, ...) uses the existing EvidenceChip unchanged.
 */

interface ExtraStyle {
  label: string;
  description: string;
  icon: LucideIcon;
  className: string;
}

const EXTRA_STATUSES: Record<string, ExtraStyle> = {
  DATA: {
    label: "Public data",
    description: "Read from a public dataset; the source is listed.",
    icon: Database,
    className: "border-rule-strong bg-card text-ink",
  },
  SOURCED: {
    label: "Sourced",
    description: "Taken from a cited publication.",
    icon: BookOpen,
    className: "border-rule-strong bg-card text-ink",
  },
  SYNTHETIC: {
    label: "Synthetic",
    description: "Computer-generated demo data. It describes no real place, person or business.",
    icon: FlaskConical,
    className: "hatch border-dashed border-rule-strong text-ink",
  },
  DATA_X_ASSUMPTION: {
    label: "Data x assumption",
    description: "Public data converted with a stated assumption (for example a weekly wage over assumed hours).",
    icon: Database,
    className: "border-dashed border-assumption-line bg-assumption-soft text-assumption",
  },
  DERIVED: {
    label: "Calculated",
    description: "Calculated by the server from the figures shown next to it.",
    icon: Calculator,
    className: "border-rule-strong bg-card text-ink",
  },
  PLAN_CHANGE: {
    label: "Suggested change",
    description:
      "Written by \"Apply this change\": a change suggested by the simulated path search, not a quote and not verified until the evidence it needs exists.",
    icon: Wand2,
    className: "border-dashed border-stated-line bg-stated-soft text-stated",
  },
};

/** Statuses that map onto an existing evidence chip. */
const ALIASES: Record<string, EvidenceStatus> = {
  FOUNDER_INPUT: "USER_STATED",
};

/** One sentence that explains a status (for tooltips, legends and screen readers). */
export function dataStatusDescription(status: string): string {
  const extra = EXTRA_STATUSES[status];
  if (extra) {
    return extra.description;
  }
  return "";
}

export function DataStatusChip({ status, className }: { status: string; className?: string }) {
  const alias = ALIASES[status];
  if (alias) {
    return <EvidenceChip status={alias} className={className} />;
  }
  if (isEvidenceStatus(status)) {
    return <EvidenceChip status={status} className={className} />;
  }
  const extra = EXTRA_STATUSES[status];
  const Icon = extra?.icon ?? Database;
  return (
    <span
      data-status={status}
      title={extra?.description}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-[0.3rem] border px-1.5 py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.07em]",
        extra?.className ?? "border-rule-strong bg-card text-ink",
        className,
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.25} aria-hidden="true" />
      <span className="sr-only">Data status: </span>
      {extra?.label ?? status.replaceAll("_", " ").toLowerCase()}
    </span>
  );
}
