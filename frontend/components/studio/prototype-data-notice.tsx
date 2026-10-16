import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Shown on the Founder Studio pages while the privacy notice (docs/studio/privacy.md) is a draft.
 * Studio addendum rule B: the deployed app takes real founder input only once the owners approve that notice,
 * so until then visitors are asked to try the prototype with made-up plans.
 */
export function PrototypeDataNotice({ className }: { className?: string }) {
  return (
    <aside
      aria-labelledby="prototype-data-title"
      className={cn("flex items-start gap-3 rounded-xl border-2 border-dashed border-rule-strong bg-card/60 p-4 sm:p-5", className)}
    >
      <TriangleAlert className="mt-0.5 size-5 shrink-0 text-foreground" aria-hidden="true" />
      <div className="flex flex-col gap-1">
        <h2 id="prototype-data-title" className="text-sm font-semibold text-foreground">
          Prototype: please use a made-up plan
        </h2>
        <p className="text-sm leading-relaxed text-foreground/85">
          The privacy notice for this prototype is still a draft. Until it is approved, try Studio with invented figures
          and public addresses only, and do not describe real people. Plans can be deleted when the server restarts.
        </p>
      </div>
    </aside>
  );
}
