"use client";

import { ArrowRight, BookOpenCheck, FileSearch, GitCompareArrows, Search, Share2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DemoBanner, DemoTag } from "@/components/lender/demo-banner";
import { PageIntro } from "@/components/layout/page-intro";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { RoleBadge } from "@/components/session/role-badge";
import { SessionErrorNotice } from "@/components/session/session-error-notice";
import { SignInPrompt } from "@/components/session/sign-in-prompt";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { NeverDecidesNotice } from "@/components/workspace/never-decides-notice";
import { PlannedSections, type PlannedSection } from "@/components/workspace/planned-sections";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";
import { useSession } from "@/lib/auth";
import { formatUsd, sentenceCase } from "@/lib/format";
import { lenderApi } from "@/lib/lender-api";
import { primaryRole } from "@/lib/roles";
import { useApiResource } from "@/lib/use-api";

const LATER_SECTIONS: PlannedSection[] = [
  {
    icon: Share2,
    title: "Founder packages",
    body: "Plans a founder chooses to share with you, next to the demo files.",
  },
  {
    icon: FileSearch,
    title: "Documents with page citations",
    body: "Bank statements, leases and quotes, with each figure linked to its page.",
  },
  {
    icon: GitCompareArrows,
    title: "Contradictions and stress sliders",
    body: "Where documents disagree, and your own stress tests next to the baseline.",
  },
  {
    icon: BookOpenCheck,
    title: "Evidence ledger",
    body: "Every figure grouped by evidence status, with the next document worth asking for.",
  },
];

/** Rows shown at first; "Show more" adds this many again. */
const PAGE_SIZE = 25;

const LINES = [
  { value: "", label: "All lines" },
  { value: "startup_credit", label: "Startup loan" },
  { value: "credit", label: "Business credit" },
  { value: "insurance", label: "Commercial insurance" },
];

/** Waits until typing pauses before the search box filters the queue (one request, not one per key). */
function useDebounced(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/** Lender Desk: the DEMO queue of SYNTHETIC files, filterable by line and searchable. */
export function LenderHome() {
  const { status, me } = useSession();
  const role = primaryRole(me.roles);
  const signedIn = status !== "loading" && status !== "offline" && me.signed_in;
  const [line, setLine] = useState("");
  const [query, setQuery] = useState("");
  const search = useDebounced(query.trim(), 300);
  const queue = useApiResource(
    signedIn ? `queue:${line}:${search}` : null,
    (signal) => lenderApi.queue({ line: line || undefined, q: search || undefined }, signal),
    "Loading the queue",
  );
  const [shown, setShown] = useState(PAGE_SIZE);
  const files = queue.data?.files ?? [];
  const visible = files.slice(0, shown);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14">
      <PageIntro
        eyebrow="Lender Desk · Demo"
        title="Review the evidence. Make the call yourself."
        description={
          <p>
            Open a SYNTHETIC demo file to see what the copilot prepares at the human checkpoint: the policy&apos;s findings
            with their guideline sections, the calculations, sanctions screening, and a template memo with citations.
          </p>
        }
      />

      <NeverDecidesNotice />

      {status === "loading" ? <WorkspaceSkeleton /> : null}
      {status === "offline" ? <ApiOfflineNotice /> : null}
      {status === "error" && !me.signed_in ? <SessionErrorNotice /> : null}
      {status === "ready" && !me.signed_in ? (
        <SignInPrompt
          area="Lender Desk"
          reason="Any signed-in user can open the SYNTHETIC demo files and record a demo decision in their own sandbox."
        />
      ) : null}

      {signedIn ? (
        <section aria-labelledby="queue-title" className="flex flex-col gap-4">
          <DemoBanner />
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="queue-title" className="font-serif text-2xl font-semibold">
              Demo queue
            </h2>
            {role ? <RoleBadge role={role} /> : null}
          </div>

          <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:flex-row sm:items-end">
            <div className="flex flex-col gap-2 sm:w-56">
              <Label htmlFor="queue-line">Line</Label>
              <select
                id="queue-line"
                value={line}
                onChange={(event) => setLine(event.target.value)}
                className="h-10 rounded-md border border-input bg-card px-3 text-base text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:text-sm"
              >
                {LINES.map((item) => {
                  const count = queue.data?.lines.find((entry) => entry.id === item.value)?.count;
                  return (
                    <option key={item.value} value={item.value}>
                      {item.label}
                      {count !== undefined ? ` (${count})` : ""}
                    </option>
                  );
                })}
              </select>
            </div>
            <div className="flex flex-1 flex-col gap-2">
              <Label htmlFor="queue-search">Search by applicant or file id</Label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input id="queue-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="For example: Harbor" />
              </div>
            </div>
          </div>

          {queue.status === "loading" ? (
            <div className="flex flex-col gap-2" role="status" aria-busy="true" aria-label="Loading the queue">
              <Skeleton className="h-16 rounded-lg" />
              <Skeleton className="h-16 rounded-lg" />
              <Skeleton className="h-16 rounded-lg" />
            </div>
          ) : null}
          {queue.problem ? <ProblemNotice problem={queue.problem} onRetry={queue.reload} retrying={queue.refreshing} /> : null}

          {queue.data ? (
            <div className={queue.refreshing ? "opacity-60 transition-opacity" : undefined}>
              <p className="mb-2 text-sm text-muted-foreground" role="status">
                {files.length === 0 ? "No demo file matches." : `${queue.data.total} demo file${queue.data.total === 1 ? "" : "s"}.`}
              </p>
              {files.length > 0 ? (
                <ul className="flex flex-col divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
                  {visible.map((file) => (
                    <li key={file.id}>
                      <Link
                        href={`/lender/file/?id=${encodeURIComponent(file.id)}`}
                        className="group grid gap-2 p-4 hover:bg-muted sm:grid-cols-[1fr_auto_auto] sm:items-center sm:gap-6"
                      >
                        <span className="flex min-w-0 flex-col gap-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-foreground group-hover:underline">{file.applicant}</span>
                            <DemoTag />
                          </span>
                          <span className="text-sm text-muted-foreground">
                            {file.id} · {file.line_label}
                          </span>
                        </span>
                        <span className="figure-nums text-sm sm:text-right">
                          <span className="block text-xs text-muted-foreground">{sentenceCase(file.amount_label)}</span>
                          <span className="font-semibold">{formatUsd(file.requested_amount)}</span>
                        </span>
                        <span className="hidden items-center gap-1 text-sm font-medium text-accent sm:flex">
                          Open
                          <ArrowRight className="size-4" aria-hidden="true" />
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : null}
              {files.length > visible.length ? (
                <Button type="button" variant="outline" className="mt-3" onClick={() => setShown((value) => value + PAGE_SIZE)}>
                  Show more ({files.length - visible.length} not shown)
                </Button>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <PlannedSections title="Coming in a later version" sections={LATER_SECTIONS} />
    </div>
  );
}
