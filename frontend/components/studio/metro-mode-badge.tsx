import { CircleCheck, CircleDashed, FlaskConical, type LucideIcon } from "lucide-react";

import type { MetroMode } from "@/lib/studio-types";
import { cn } from "@/lib/utils";

/**
 * A metro's honest data mode (Studio addendum rule G): "Full data", "Limited data" or "SYNTHETIC demo city".
 * Each mode has its own word and icon; limited mode uses a dashed border like the UNKNOWN chip.
 */
const MODES: Record<MetroMode, { label: string; icon: LucideIcon; className: string; description: string }> = {
  full: {
    label: "Full data",
    icon: CircleCheck,
    className: "border-verified-line bg-verified-soft text-verified",
    description: "Public data covers the factors this city needs.",
  },
  limited: {
    label: "Limited data",
    icon: CircleDashed,
    className: "border-dashed border-unknown-line bg-unknown-soft text-unknown",
    description: "Some factors have no public data here; they show as unknown or as an assumption.",
  },
  synthetic: {
    label: "SYNTHETIC demo city",
    icon: FlaskConical,
    className: "hatch border-dashed border-rule-strong text-ink",
    description: "A computer-generated city for trying the tool. It is not a real place.",
  },
};

export function metroModeDescription(mode: MetroMode): string {
  return MODES[mode]?.description ?? "";
}

export function MetroModeBadge({ mode, className }: { mode: MetroMode | string; className?: string }) {
  const style = MODES[mode as MetroMode] ?? MODES.limited;
  const Icon = style.icon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-[0.3rem] border px-1.5 py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.07em]",
        style.className,
        className,
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.25} aria-hidden="true" />
      <span className="sr-only">Data mode: </span>
      {style.label}
    </span>
  );
}
