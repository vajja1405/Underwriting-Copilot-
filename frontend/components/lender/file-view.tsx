"use client";

import { AlertOctagon, ArrowLeft, Bot, CheckCircle2, CircleHelp, ListChecks, ScanSearch, ShieldAlert, TriangleAlert, Wrench } from "lucide-react";
import Link from "next/link";
import { useCallback, useRef, useState, type ReactNode } from "react";

import { CitationDrawer } from "@/components/lender/citation-drawer";
import { citationKind, CitationList, type OpenCitation } from "@/components/lender/citations";
import { DecisionPanel, type RecordedDecision } from "@/components/lender/decision-panel";
import { DemoBanner, DemoTag } from "@/components/lender/demo-banner";
import { recommendationLabel, reviewerRoleLabel, ruleLabel } from "@/components/lender/labels";
import { ResultsDisclaimer } from "@/components/layout/results-disclaimer";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { SignInPrompt } from "@/components/session/sign-in-prompt";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NeverDecidesNotice } from "@/components/workspace/never-decides-notice";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";
import { useSession } from "@/lib/auth";
import { formatDate, formatNumber, formatShare, formatUsd, isNumber, sentenceCase } from "@/lib/format";
import { lenderApi } from "@/lib/lender-api";
import type { Calculation, LenderFile } from "@/lib/lender-types";
import { useQueryParam } from "@/lib/location";
import { useApiResource } from "@/lib/use-api";
import { cn } from "@/lib/utils";

/**
 * /lender/file/?id=...: one SYNTHETIC demo file at the human checkpoint (GET /api/lender/files/{id}).
 * The server's own sentence for the policy's suggestion ("The policy suggests: ... — you decide."), findings with
 * citation chips (each opens the citation drawer), the template memo with its citations and the claim checker's
 * verdict, calculations, screening (which lists, which dates), tool status, rules not evaluated, and the decision
 * panel. Long lists start short with "Show all" so the page stays readable on a phone.
 */

const MEMO_REASONS_SHOWN = 5;
const CALCULATIONS_SHOWN = 6;
const NOT_EVALUATED_SHOWN = 4;

interface CitationProps {
  onOpen: OpenCitation;
  canOpen: (id: string) => boolean;
}

function Panel({ id, title, icon: Icon, children, className }: { id: string; title: string; icon: typeof ListChecks; children: ReactNode; className?: string }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={cn("flex min-w-0 scroll-mt-24 flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5", className)}>
      <h2 id={`${id}-title`} className="flex items-center gap-2 font-serif text-lg font-semibold">
        <Icon className="size-5 text-accent" aria-hidden="true" />
        {title}
      </h2>
      {children}
    </section>
  );
}

/** "Show all N" / "Show fewer" under a long list. */
function ShowMoreButton({ total, shown, expanded, onToggle, noun }: { total: number; shown: number; expanded: boolean; onToggle: () => void; noun: string }) {
  if (total <= shown) {
    return null;
  }
  return (
    <Button type="button" variant="outline" size="sm" className="self-start" aria-expanded={expanded} onClick={onToggle}>
      {expanded ? `Show fewer ${noun}` : `Show all ${total} ${noun}`}
    </Button>
  );
}

function calcValue(calculation: Calculation): string {
  if (!isNumber(calculation.value)) {
    return "Not available";
  }
  switch (calculation.unit) {
    case "usd":
    case "USD":
      return formatUsd(calculation.value);
    case "ratio":
      return `${calculation.value.toFixed(2)}x`;
    case "percent":
    case "pct":
      return `${formatNumber(calculation.value, 2)}%`;
    case "share":
      return formatShare(calculation.value);
    case "score":
    case "count":
      return formatNumber(calculation.value, 2);
    default:
      return `${formatNumber(calculation.value, 2)} ${calculation.unit}`;
  }
}

function inputText(value: number | string | null): string {
  if (value === null) {
    return "not available";
  }
  return typeof value === "number" ? formatNumber(value, 2) : value;
}

function Findings({ file, citations }: { file: LenderFile; citations: CitationProps }) {
  return (
    <Panel id="findings" title={`Findings (${file.findings.length})`} icon={ListChecks}>
      {file.findings.length === 0 ? <p className="text-sm text-muted-foreground">No rule failed. The file still needs a person&apos;s decision.</p> : null}
      <ol className="flex flex-col gap-3">
        {file.findings.map((finding, index) => (
          <li key={`${finding.rule}-${index}`} className={cn("flex flex-col gap-2 rounded-lg border p-3", finding.blocking ? "border-conflicting-line bg-conflicting-soft/50" : "border-border")}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{ruleLabel(finding.rule)}</span>
              <Badge variant="outline">{recommendationLabel(finding.outcome)}</Badge>
              {finding.blocking ? (
                <Badge variant="outline" className="border-conflicting-line text-conflicting">
                  <AlertOctagon aria-hidden="true" />
                  Blocking
                </Badge>
              ) : null}
            </div>
            <p className="text-sm leading-relaxed">{finding.detail}</p>
            {finding.refer_to.length > 0 ? <p className="text-xs text-muted-foreground">Refer to: {finding.refer_to.join(", ")}</p> : null}
            <CitationList ids={[...finding.section_ids, ...finding.evidence_ids]} {...citations} />
          </li>
        ))}
      </ol>
      {file.checks_passed.length > 0 ? (
        <details className="min-w-0 text-sm">
          <summary className="cursor-pointer font-medium text-accent">Rules that passed ({file.checks_passed.length})</summary>
          <ul className="mt-2 flex flex-col gap-2">
            {file.checks_passed.map((check, index) => (
              <li key={`${check.rule}-${index}`} className="flex flex-col gap-1 rounded-lg bg-paper-sunken p-2.5">
                <span className="flex items-center gap-1.5 font-medium">
                  <CheckCircle2 className="size-4 text-verified" aria-hidden="true" />
                  {ruleLabel(check.rule)}
                </span>
                <span className="text-muted-foreground">{check.detail}</span>
                <CitationList ids={[...check.section_ids, ...check.evidence_ids]} {...citations} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Panel>
  );
}

function Memo({ file, citations }: { file: LenderFile; citations: CitationProps }) {
  const [expanded, setExpanded] = useState(false);
  const memo = file.memo;
  const check = file.memo_check;
  return (
    <Panel id="memo" title="Memo" icon={Bot}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{file.memo_writer} memo</Badge>
        {check ? (
          check.passed ? (
            <Badge variant="soft">
              <CheckCircle2 aria-hidden="true" />
              Claim check passed
            </Badge>
          ) : (
            <Badge variant="outline" className="border-conflicting-line text-conflicting">
              <TriangleAlert aria-hidden="true" />
              Claim check failed
            </Badge>
          )
        ) : null}
        {file.memo_status ? <span className="text-xs text-muted-foreground">Status: {file.memo_status}</span> : null}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">{file.memo_note}</p>
      {memo ? (
        <>
          <p className="text-sm leading-relaxed">{memo.summary}</p>
          <ol className="flex flex-col gap-3">
            {(expanded ? memo.reasons : memo.reasons.slice(0, MEMO_REASONS_SHOWN)).map((reason, index) => (
              <li key={index} className="flex flex-col gap-1.5 border-l-2 border-rule-strong pl-3">
                <p className="text-sm leading-relaxed">{reason.text}</p>
                <CitationList ids={reason.citations} {...citations} />
              </li>
            ))}
          </ol>
          <ShowMoreButton total={memo.reasons.length} shown={MEMO_REASONS_SHOWN} expanded={expanded} onToggle={() => setExpanded((value) => !value)} noun="reasons" />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No usable memo for this file. The findings and calculations still stand.</p>
      )}
      {check && check.issues.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs text-conflicting">
          {check.issues.map((issue, index) => (
            <li key={index}>
              {issue.kind}: {issue.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

function Calculations({ file, citations }: { file: LenderFile; citations: CitationProps }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? file.calculations : file.calculations.slice(0, CALCULATIONS_SHOWN);
  return (
    <Panel id="calculations" title="Calculations" icon={ScanSearch}>
      <p className="text-xs text-muted-foreground">Computed by the policy code from the application, never by a language model.</p>
      {file.calculations.length === 0 ? <p className="text-sm text-muted-foreground">No calculation for this file.</p> : null}
      <ul className="flex flex-col divide-y divide-border">
        {visible.map((calculation) => (
          <li key={calculation.calc_id} className="flex flex-col gap-1.5 py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-sm font-medium">
                {calculation.label} <span className="break-all font-mono text-xs text-muted-foreground">{calculation.calc_id}</span>
              </span>
              <span className="figure-nums font-semibold">{calcValue(calculation)}</span>
            </div>
            {Object.keys(calculation.inputs).length > 0 ? (
              <p className="figure-nums text-xs leading-relaxed text-muted-foreground">
                From:{" "}
                {Object.entries(calculation.inputs)
                  .map(([name, value]) => `${name.replaceAll("_", " ")} ${inputText(value)}`)
                  .join("; ")}
              </p>
            ) : null}
            <CitationList ids={calculation.section_ids} {...citations} />
          </li>
        ))}
      </ul>
      <ShowMoreButton total={file.calculations.length} shown={CALCULATIONS_SHOWN} expanded={expanded} onToggle={() => setExpanded((value) => !value)} noun="calculations" />
    </Panel>
  );
}

function Screening({ file }: { file: LenderFile }) {
  const screening = file.screening;
  return (
    <Panel id="screening" title="Sanctions screening" icon={ShieldAlert}>
      <div className="flex flex-col gap-1.5 text-sm">
        <p className="font-medium">Lists searched ({screening.configured_lists.join(", ") || "none configured"})</p>
        {screening.lists_used.length > 0 ? (
          <ul className="flex flex-col gap-1">
            {screening.lists_used.map((list) => (
              <li key={`${list.source}-${list.file_name}`} className="text-xs leading-relaxed text-muted-foreground">
                <span className="font-semibold text-foreground">{list.source}</span> · {list.file_name} ·{" "}
                {list.snapshot_date ? `downloaded ${formatDate(list.snapshot_date)}` : "download date not recorded"}
                {list.attribution ? ` · ${list.attribution}` : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No list file is named in the results.</p>
        )}
      </div>
      {screening.results.length > 0 ? (
        <ul className="flex flex-col divide-y divide-border">
          {screening.results.map((result) => {
            const match = result.status === "potential_match";
            const clear = result.status === "clear";
            const Icon = match ? TriangleAlert : clear ? CheckCircle2 : CircleHelp;
            return (
              <li key={result.result_id} className="flex flex-col gap-1 py-2.5">
                <span className="flex flex-wrap items-center gap-2 text-sm">
                  <Icon className={cn("size-4", match ? "text-conflicting" : clear ? "text-verified" : "text-unknown")} aria-hidden="true" />
                  <span className="font-medium">{result.party_id ?? "Party"}</span>
                  <span className={cn("font-semibold", match && "text-conflicting")}>{result.status.replaceAll("_", " ")}</span>
                  <span className="font-mono text-[0.6875rem] text-muted-foreground">{result.result_id}</span>
                </span>
                {match ? (
                  <span className="figure-nums pl-6 text-xs text-muted-foreground">
                    {formatNumber(result.candidates.length)} candidate{result.candidates.length === 1 ? "" : "s"}, top score{" "}
                    {formatNumber(result.top_score, 4)}. Only Compliance can disposition a potential match.
                  </span>
                ) : null}
                {result.error ? <span className="pl-6 text-xs text-unknown">{result.error}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <p className="text-xs text-muted-foreground">{screening.note}</p>
    </Panel>
  );
}

function Tools({ file }: { file: LenderFile }) {
  const rows = [
    { name: "Guideline search", panel: file.guideline_search },
    { name: "Risk model", panel: file.risk_model },
  ];
  return (
    <Panel id="tools" title="Tools the copilot used" icon={Wrench}>
      <ul className="flex flex-col gap-2 text-sm">
        {rows.map((row) => (
          <li key={row.name} className="flex flex-col gap-0.5">
            <span>
              <span className="font-medium">{row.name}:</span>{" "}
              <span className={cn(row.panel.status === "ok" ? "text-verified" : "text-unknown")}>{row.panel.status.replaceAll("_", " ")}</span>
            </span>
            <span className="text-xs leading-relaxed text-muted-foreground">{row.panel.detail}</span>
          </li>
        ))}
      </ul>
      <details className="min-w-0 text-sm">
        <summary className="cursor-pointer font-medium text-accent">All tool calls ({file.tool_results.length})</summary>
        <ul className="mt-2 flex flex-col gap-1 text-xs">
          {file.tool_results.map((tool) => (
            <li key={tool.result_id}>
              <span className="font-mono">{tool.result_id}</span> {tool.tool} ({tool.purpose}): {tool.status}
              {tool.error ? ` - ${tool.error}` : ""}
            </li>
          ))}
        </ul>
      </details>
    </Panel>
  );
}

function NotEvaluated({ file, citations }: { file: LenderFile; citations: CitationProps }) {
  const [expanded, setExpanded] = useState(false);
  if (file.not_evaluated.length === 0) {
    return null;
  }
  return (
    <Panel id="not-evaluated" title={`Not evaluated (${file.not_evaluated.length})`} icon={CircleHelp}>
      <p className="text-xs text-muted-foreground">Rules the copilot could not check on this file, and why. A person must check them.</p>
      <ul className="flex flex-col gap-2">
        {(expanded ? file.not_evaluated : file.not_evaluated.slice(0, NOT_EVALUATED_SHOWN)).map((item) => (
          <li key={item.rule} className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{ruleLabel(item.rule)}</span>
            <span className="text-xs leading-relaxed text-muted-foreground">{item.reason}</span>
            <CitationList ids={item.section_ids} {...citations} />
          </li>
        ))}
      </ul>
      <ShowMoreButton total={file.not_evaluated.length} shown={NOT_EVALUATED_SHOWN} expanded={expanded} onToggle={() => setExpanded((value) => !value)} noun="rules" />
    </Panel>
  );
}

function FileBody({ file, recorded, onRecorded }: { file: LenderFile; recorded: RecordedDecision | null; onRecorded: (recorded: RecordedDecision) => void }) {
  const [citationId, setCitationId] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const evidence = file.evidence;

  const onOpen: OpenCitation = (id, trigger) => {
    triggerRef.current = trigger;
    setCitationId(id);
  };
  const canOpen = useCallback((id: string) => id in evidence || citationKind(id) === "section", [evidence]);
  const citations: CitationProps = { onOpen, canOpen };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-accent">Lender Desk · {file.line_label}</p>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-serif text-3xl font-semibold leading-tight sm:text-4xl">{file.applicant}</h1>
          <DemoTag />
        </div>
        <dl className="figure-nums flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-muted-foreground">File</dt>
            <dd className="font-mono font-semibold">{file.id}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{sentenceCase(file.amount_label)}</dt>
            <dd className="font-semibold">{formatUsd(file.requested_amount)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Review queue</dt>
            <dd className="font-semibold">{file.review_queue}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Needs</dt>
            <dd className="font-semibold">{reviewerRoleLabel(file.required_reviewer_role)}</dd>
          </div>
        </dl>
      </header>

      <div className="flex flex-col gap-3">
        <ResultsDisclaimer />
        <DemoBanner />
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-accent/40 bg-accent-soft p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1">
          <p className="text-lg font-medium leading-snug">{file.recommendation_text}</p>
          <a href="#decision-title" className="text-sm font-medium text-accent underline underline-offset-4 lg:hidden">
            Go to your decision
          </a>
        </div>
        {file.blocking_open ? (
          <Badge variant="outline" className="max-w-full self-start whitespace-normal border-conflicting-line bg-card text-conflicting sm:self-auto">
            <AlertOctagon aria-hidden="true" />
            Blocking referral open: {file.referral_targets.join(", ")}
          </Badge>
        ) : null}
      </div>

      {file.injection_hits.length > 0 ? (
        <Alert variant="warning">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>Instruction-like text found in the application</AlertTitle>
          <AlertDescription className="text-foreground/85">
            <p>It was treated as data and not followed. A person should look at it.</p>
            <ul className="flex flex-col gap-1">
              {file.injection_hits.map((hit, index) => (
                <li key={`${hit.field}-${index}`}>
                  <span className="font-semibold">{hit.field}</span> ({hit.patterns.join(", ")})
                  <span className="mt-0.5 block break-words rounded border border-border bg-card px-2 py-1 font-mono text-xs">{hit.excerpt}</span>
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4 shrink-0 text-verified" aria-hidden="true" />
          Instruction screen: no instruction-like text was found in the application&apos;s free-text fields.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <div className="flex min-w-0 flex-col gap-6">
          <Findings file={file} citations={citations} />
          <Memo file={file} citations={citations} />
          <Calculations file={file} citations={citations} />
        </div>
        <div className="flex min-w-0 flex-col gap-6">
          <DecisionPanel file={file} recorded={recorded} onRecorded={onRecorded} />
          <Screening file={file} />
          <Tools file={file} />
          <NotEvaluated file={file} citations={citations} />
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        Policy {file.policy_version} · agent graph {file.graph_version}
        {file.stamp ? ` · ${file.stamp}` : ""}
      </p>

      <CitationDrawer citationId={citationId} evidence={evidence} onClose={() => setCitationId(null)} triggerRef={triggerRef} />
    </div>
  );
}

export function LenderFileView() {
  const { status, me } = useSession();
  const id = useQueryParam("id");
  const signedIn = status !== "loading" && status !== "offline" && me.signed_in;
  const file = useApiResource(signedIn && id ? `file:${id}` : null, (signal) => lenderApi.file(id ?? "", signal), "Loading the file");
  const hasDecision = file.data?.decision != null;
  const record = useApiResource(hasDecision && id ? `record:${id}` : null, (signal) => lenderApi.record(id ?? "", signal), "Loading your decision record");
  const [newRecord, setNewRecord] = useState<RecordedDecision | null>(null);
  const loadedRecord: RecordedDecision | null = record.data
    ? { decision: record.data.decision, markdown: record.data.markdown, record: record.data.record }
    : null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14">
      <div>
        <Button asChild variant="ghost" size="sm">
          <Link href="/lender/">
            <ArrowLeft aria-hidden="true" />
            Demo queue
          </Link>
        </Button>
      </div>
      <NeverDecidesNotice />
      {status === "loading" ? <WorkspaceSkeleton /> : null}
      {status === "offline" ? <ApiOfflineNotice /> : null}
      {status === "ready" && !me.signed_in ? (
        <SignInPrompt area="Lender Desk" reason="Sign in to open the SYNTHETIC demo files and record a demo decision." />
      ) : null}
      {signedIn && !id ? (
        <Alert variant="warning" role="alert">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>No file was named in the address</AlertTitle>
          <AlertDescription className="text-foreground/85">
            <p>
              Pick a file from the{" "}
              <Link href="/lender/" className="font-semibold text-accent underline underline-offset-4">
                demo queue
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      {signedIn && id && file.status === "loading" ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">Running the copilot to the human checkpoint on this file... the first time can take a few seconds.</p>
          <WorkspaceSkeleton />
        </div>
      ) : null}
      {file.problem && !file.data ? <ProblemNotice problem={file.problem} onRetry={file.reload} retrying={file.refreshing} /> : null}
      {record.problem ? <ProblemNotice problem={record.problem} onRetry={record.reload} retrying={record.refreshing} /> : null}
      {file.data ? <FileBody file={file.data} recorded={newRecord ?? loadedRecord} onRecorded={setNewRecord} /> : null}
    </div>
  );
}
