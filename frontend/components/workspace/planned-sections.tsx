import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** One area of a workspace that is not built yet: what it will show, in plain words. */
export interface PlannedSection {
  icon: LucideIcon;
  title: string;
  body: string;
}

/**
 * The "what will appear here" list on empty workspaces. Each item is tagged "Not built yet" so the page never
 * implies a feature exists before it does.
 */
export function PlannedSections({ title, sections, className }: { title: string; sections: PlannedSection[]; className?: string }) {
  return (
    <section aria-label={title} className={cn("flex flex-col gap-4", className)}>
      <h2 className="text-sm font-semibold uppercase tracking-[0.1em] text-muted-foreground">{title}</h2>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {sections.map((section) => (
          <li key={section.title} className="flex flex-col gap-2 rounded-lg border border-dashed border-rule-strong bg-card/60 p-4">
            <div className="flex items-center gap-2.5">
              <section.icon className="size-5 shrink-0 text-accent" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-foreground">{section.title}</h3>
              <span className="ml-auto shrink-0 rounded-full bg-muted px-2 py-0.5 text-[0.6875rem] font-medium text-muted-foreground">
                Not built yet
              </span>
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">{section.body}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
