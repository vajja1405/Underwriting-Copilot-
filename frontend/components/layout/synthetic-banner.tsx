import { FlaskConical } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Marks SYNTHETIC (computer-generated) data wherever it is shown (CLAUDE.md rule 2).
 * A hatched "specimen tape" look, so it stands apart from real content without adding a new color.
 */
export function SyntheticBanner({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <div
      role="note"
      className={cn(
        "hatch flex items-start gap-2 rounded-md border border-dashed border-rule-strong px-3 py-2 text-xs leading-relaxed text-foreground",
        className,
      )}
    >
      <FlaskConical className="mt-px size-4 shrink-0" aria-hidden="true" />
      <p>
        <strong className="font-semibold tracking-[0.08em]">SYNTHETIC</strong>{" "}
        {children ?? "Computer-generated example data. It describes no real person, business or loan."}
      </p>
    </div>
  );
}

/** A compact inline SYNTHETIC stamp for card headers. */
export function SyntheticTag({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "hatch inline-flex items-center gap-1 rounded-[0.3rem] border border-dashed border-rule-strong px-1.5 py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.08em] text-foreground",
        className,
      )}
    >
      <FlaskConical className="size-3.5" aria-hidden="true" />
      Synthetic
    </span>
  );
}
