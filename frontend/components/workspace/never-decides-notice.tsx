import { Scale } from "lucide-react";

import { PRODUCT_NAME } from "@/lib/config";
import { cn } from "@/lib/utils";

/** The Lender Desk's standing notice: the copilot prepares, a person decides (CLAUDE.md rule 8, addendum rule C). */
export function NeverDecidesNotice({ className }: { className?: string }) {
  return (
    <aside
      aria-labelledby="never-decides-title"
      className={cn("flex flex-col gap-3 rounded-xl border-2 border-accent bg-accent-soft p-5 sm:flex-row sm:gap-4 sm:p-6", className)}
    >
      <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-ink sm:size-11">
        <Scale className="size-5" aria-hidden="true" />
      </span>
      <div className="flex flex-col gap-1.5">
        <h2 id="never-decides-title" className="font-serif text-xl font-semibold leading-snug text-foreground">
          The copilot never decides. A person does.
        </h2>
        <p className="text-sm leading-relaxed text-foreground/85">
          {PRODUCT_NAME} prepares the file: figures with their sources, rule checks, sanctions screening and a draft memo
          (contradiction checks across documents come in a later version). Approving,
          referring, requesting more information or declining is always a person&apos;s choice. A decline needs a
          written reason from a senior lender.
        </p>
      </div>
    </aside>
  );
}
