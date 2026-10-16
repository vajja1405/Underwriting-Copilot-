"use client";

import { BookText, Calculator, FileText, ScanSearch, ShieldAlert, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Citation chips: every id the policy or the memo cites, made clickable. A chip opens the citation drawer:
 * - a guideline or program section ("NBC-4.2", "NSP-9.1") shows the section text (GET /api/lender/sections/{id});
 * - a calculation ("CALC-dscr"), tool result ("TR-6"), application fact ("APP-requested_amount") or check
 *   ("CHK-injection_screen") shows the file's own evidence item (the `evidence` map of the file response).
 * An id the server did not describe is shown as a plain label. The kind is told by an icon and by the chip's
 * accessible name, never by color alone.
 */

export type CitationKind = "section" | "calculation" | "tool" | "fact" | "check" | "other";

export function citationKind(id: string): CitationKind {
  if (id.startsWith("CALC-")) {
    return "calculation";
  }
  if (/^TR-\d+$/.test(id)) {
    return "tool";
  }
  if (id.startsWith("APP-")) {
    return "fact";
  }
  if (id.startsWith("CHK-")) {
    return "check";
  }
  if (/^[A-Z]{2,4}-\d+(\.\d+)*$/.test(id)) {
    return "section";
  }
  return "other";
}

const ICONS: Record<CitationKind, LucideIcon> = {
  section: BookText,
  calculation: Calculator,
  tool: ScanSearch,
  fact: FileText,
  check: ShieldAlert,
  other: BookText,
};

export const CITATION_NAMES: Record<CitationKind, string> = {
  section: "guideline section",
  calculation: "calculation",
  tool: "tool result",
  fact: "application fact",
  check: "check",
  other: "reference",
};

/** Opens the drawer for a citation; gets the chip so focus can return to it when the drawer closes. */
export type OpenCitation = (id: string, trigger: HTMLButtonElement) => void;

export function CitationChip({ id, onOpen, canOpen }: { id: string; onOpen: OpenCitation; canOpen: (id: string) => boolean }) {
  const kind = citationKind(id);
  const Icon = ICONS[kind];
  if (!canOpen(id)) {
    return (
      <span className="inline-flex items-center gap-1 rounded-[0.3rem] border border-dashed border-rule-strong px-1.5 py-0.5 font-mono text-[0.6875rem] text-muted-foreground">
        <Icon className="size-3" aria-hidden="true" />
        <span className="sr-only">{CITATION_NAMES[kind]} </span>
        {id}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={(event) => onOpen(id, event.currentTarget)}
      aria-label={`Open ${CITATION_NAMES[kind]} ${id}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-[0.3rem] border bg-card px-1.5 py-0.5 font-mono text-[0.6875rem] font-semibold text-ink hover:border-accent hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
        kind === "section" ? "border-rule-strong" : "border-rule",
      )}
    >
      <Icon className="size-3" aria-hidden="true" />
      {id}
    </button>
  );
}

export function CitationList({
  ids,
  onOpen,
  canOpen,
  className,
}: {
  ids: string[];
  onOpen: OpenCitation;
  canOpen: (id: string) => boolean;
  className?: string;
}) {
  if (ids.length === 0) {
    return null;
  }
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="Citations">
      {ids.map((id) => (
        <li key={id}>
          <CitationChip id={id} onOpen={onOpen} canOpen={canOpen} />
        </li>
      ))}
    </ul>
  );
}
