import { BadgeCheck, CircleHelp, Sigma, SlidersHorizontal, TriangleAlert, UserRound, type LucideIcon } from "lucide-react";

import { EVIDENCE_STATUS_INFO, type EvidenceStatus } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/**
 * The evidence chip: a small stamp that says where a number comes from.
 *
 * Each status has its own label AND icon, so the chip never relies on color alone. The three amber statuses
 * (User stated, Assumption, Model estimate) share a color family and differ by label, icon and, for
 * assumptions, a dashed border (an assumption is editable). Unknown also uses a dashed border (nothing behind it).
 */
interface StatusStyle {
  icon: LucideIcon;
  className: string;
}

const STATUS_STYLES: Record<EvidenceStatus, StatusStyle> = {
  VERIFIED: { icon: BadgeCheck, className: "border-verified-line bg-verified-soft text-verified" },
  USER_STATED: { icon: UserRound, className: "border-stated-line bg-stated-soft text-stated" },
  ASSUMPTION: { icon: SlidersHorizontal, className: "border-dashed border-assumption-line bg-assumption-soft text-assumption" },
  MODEL_ESTIMATE: { icon: Sigma, className: "border-estimate-line bg-estimate-soft text-estimate" },
  CONFLICTING: { icon: TriangleAlert, className: "border-conflicting-line bg-conflicting-soft text-conflicting" },
  UNKNOWN: { icon: CircleHelp, className: "border-dashed border-unknown-line bg-unknown-soft text-unknown" },
};

export interface EvidenceChipProps {
  status: EvidenceStatus;
  size?: "sm" | "md";
  className?: string;
}

export function EvidenceChip({ status, size = "sm", className }: EvidenceChipProps) {
  const { icon: Icon, className: statusClassName } = STATUS_STYLES[status];
  const { label } = EVIDENCE_STATUS_INFO[status];
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-[0.3rem] border font-semibold uppercase leading-none tracking-[0.07em]",
        size === "sm" ? "px-1.5 py-[0.3rem] text-[0.6875rem]" : "px-2 py-1.5 text-xs",
        statusClassName,
        className,
      )}
    >
      <Icon className={size === "sm" ? "size-3.5" : "size-4"} strokeWidth={2.25} aria-hidden="true" />
      <span className="sr-only">Evidence status: </span>
      {label}
    </span>
  );
}
