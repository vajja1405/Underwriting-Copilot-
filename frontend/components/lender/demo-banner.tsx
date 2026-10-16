import { FlaskConical } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The DEMO label of the Lender Desk v0.1: every file is a SYNTHETIC structured application, and every decision is
 * recorded in the user's own demo sandbox. Shown at the top of the queue and of every file.
 */
export function DemoBanner({ className }: { className?: string }) {
  return (
    <div
      role="note"
      className={cn("hatch flex items-start gap-3 rounded-xl border-2 border-dashed border-rule-strong px-4 py-3 text-sm leading-relaxed", className)}
    >
      <FlaskConical className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
      <p>
        <strong className="font-semibold tracking-[0.08em]">SYNTHETIC DEMO.</strong> These files are computer-generated
        applications that describe no real business or person. Your decisions here are recorded in your own demo sandbox,
        not sent to any lender.
      </p>
    </div>
  );
}

/** A compact DEMO stamp for list rows and headers. */
export function DemoTag({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "hatch inline-flex items-center gap-1 whitespace-nowrap rounded-[0.3rem] border border-dashed border-rule-strong px-1.5 py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.08em]",
        className,
      )}
    >
      <FlaskConical className="size-3.5" aria-hidden="true" />
      Synthetic demo
    </span>
  );
}
