"use client";

import { FlaskConical, TriangleAlert } from "lucide-react";
import type { RefObject } from "react";

import { CITATION_NAMES, citationKind } from "@/components/lender/citations";
import { PlainMarkdown } from "@/components/lender/plain-markdown";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { lenderApi } from "@/lib/lender-api";
import type { EvidenceItem } from "@/lib/lender-types";
import { useApiResource } from "@/lib/use-api";

/**
 * The citation drawer. One drawer serves every chip, so it has no SheetTrigger: the chip that opened it is
 * remembered and gets keyboard focus back when the drawer closes (the landing page's evidence-drawer pattern).
 * - A guideline section is fetched (GET /api/lender/sections/{id}).
 * - Any other citation is an evidence item the file response already carries (no request).
 * Text from the API is shown with PlainMarkdown or as plain text; no HTML from the API is ever rendered. Flags
 * (instruction-like phrases found in the text) are listed: the text is data, never orders.
 */
export function CitationDrawer({
  citationId,
  evidence,
  onClose,
  triggerRef,
}: {
  citationId: string | null;
  evidence: Record<string, EvidenceItem>;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const item = citationId ? evidence[citationId] : undefined;
  const isSection = citationId !== null && !item && citationKind(citationId) === "section";
  const section = useApiResource(
    isSection ? `section:${citationId}` : null,
    (signal) => lenderApi.section(citationId ?? "", signal),
    "Loading the section",
  );
  const flags = item?.flags ?? section.data?.flags ?? [];
  const kindName = citationId ? CITATION_NAMES[citationKind(citationId)] : "";
  const title = item?.title ?? section.data?.title ?? kindName;

  return (
    <Sheet open={citationId !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <SheetContent
        side="right"
        className="w-[min(34rem,94vw)]"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          triggerRef.current?.focus();
        }}
      >
        <SheetHeader>
          <p className="font-mono text-xs font-semibold text-accent">
            {citationId}
            <span className="ml-2 font-sans font-normal text-muted-foreground">{item ? item.kind.replaceAll("_", " ") : kindName}</span>
          </p>
          <SheetTitle className="font-serif text-xl">{title}</SheetTitle>
          <SheetDescription className="flex items-start gap-1.5 text-xs">
            <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {isSection
              ? "From a SYNTHETIC guideline of a fictional institution, written for this demo. Not a real lender's policy."
              : "From this SYNTHETIC demo file, as the copilot recorded it."}
          </SheetDescription>
        </SheetHeader>

        {isSection && section.status === "loading" ? (
          <div className="flex flex-col gap-2" role="status" aria-busy="true" aria-label="Loading the section">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
          </div>
        ) : null}
        {isSection && section.problem ? <ProblemNotice problem={section.problem} onRetry={section.reload} retrying={section.refreshing} /> : null}
        {isSection && section.data ? (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground">Document: {section.data.document}</p>
            <PlainMarkdown text={section.data.text} />
          </div>
        ) : null}
        {item ? <PlainMarkdown text={item.text} /> : null}

        {flags.length > 0 ? (
          <p className="flex items-start gap-1.5 rounded-md border border-stated-line bg-stated-soft p-2 text-xs">
            <TriangleAlert className="mt-px size-3.5 shrink-0 text-stated" aria-hidden="true" />
            Instruction-like wording found in this text ({flags.join(", ")}). It is treated as data and never followed.
          </p>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
