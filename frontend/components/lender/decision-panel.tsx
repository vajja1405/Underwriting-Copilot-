"use client";

import { Ban, CheckCircle2, Download, FileClock, Forward, Gavel, HelpCircle, Lock, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { actionStates, overrideReasonLabel, readableReason } from "@/components/lender/decision-rules";
import { actionLabel, recommendationLabel, reviewerRoleLabel } from "@/components/lender/labels";
import { PlainMarkdown } from "@/components/lender/plain-markdown";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { ApiError, errorMessages } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { formatDateTime } from "@/lib/format";
import { lenderApi } from "@/lib/lender-api";
import type { ActionCheck, DecisionSummary, LenderFile, ReviewerAction } from "@/lib/lender-types";
import { cn } from "@/lib/utils";

/**
 * The decision panel: a PERSON chooses approve, refer, request more information or decline-with-reason. The copilot
 * never decides. Buttons are disabled, with the server's reasons written under them, when validate_decision would
 * refuse the action for your role (`action_checks`); the server checks again on submit, and its refusal (422, with
 * reasons) is shown as it is. A 422 for the input itself (for example an account number in the rationale) is shown
 * as refused input, not as the checkpoint's refusal. After a decision, the DEMO decision record is shown (focus moves
 * to it) and its JSON can be downloaded.
 */

/** The server's detail line for a refusal by the graph's checkpoint (lender_demo.DecisionRefused). */
const CHECKPOINT_REFUSAL = "The checkpoint refused this decision.";

/** "a senior underwriter", "an underwriter". */
function withArticle(words: string): string {
  return `${/^[aeiou]/i.test(words) ? "an" : "a"} ${words}`;
}

const ACTION_ICONS: Record<ReviewerAction, LucideIcon> = {
  approve: CheckCircle2,
  refer: Forward,
  request_more_information: HelpCircle,
  decline: Ban,
};

const MIN_RATIONALE = 10;
const MAX_RATIONALE = 2000;

/** What the panel shows once a decision exists: its summary and its Markdown record. */
export interface RecordedDecision {
  decision: DecisionSummary;
  markdown: string;
  /** The full record JSON when it was loaded (GET .../record); fetched on demand for the download otherwise. */
  record?: Record<string, unknown> | null;
}

function RecordView({ fileId, recorded, focusOnShow = false }: { fileId: string; recorded: RecordedDecision; focusOnShow?: boolean }) {
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const decision = recorded.decision;

  // Right after a decision is recorded, the form it replaced is gone: move focus to the record's heading so keyboard
  // and screen-reader users land on what happened.
  useEffect(() => {
    if (focusOnShow) {
      headingRef.current?.focus();
    }
  }, [focusOnShow]);

  async function download() {
    setBusy(true);
    setProblem(null);
    try {
      const record = recorded.record ?? (await lenderApi.record(fileId)).record;
      const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `decision-record-${fileId}-${decision.record_id}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setProblem(describeProblem(error, "Downloading the record"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="record-title" className="flex flex-col gap-3 rounded-xl border border-verified-line bg-verified-soft/50 p-4">
      <h3 ref={headingRef} tabIndex={-1} id="record-title" className="flex items-center gap-2 font-semibold outline-none focus-visible:outline-2 focus-visible:outline-ring">
        <FileClock className="size-4 text-verified" aria-hidden="true" />
        Your decision record ({decision.label})
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Action</dt>
        <dd className="font-medium">{actionLabel(decision.action)}</dd>
        <dt className="text-muted-foreground">Status</dt>
        <dd>{decision.final_status.replaceAll("_", " ")}</dd>
        <dt className="text-muted-foreground">Role</dt>
        <dd>{reviewerRoleLabel(decision.reviewer_role)}</dd>
        <dt className="text-muted-foreground">Override</dt>
        <dd>{decision.is_override ? "yes (with an approved reason)" : "no"}</dd>
        <dt className="text-muted-foreground">Recorded</dt>
        <dd>{formatDateTime(decision.decided_at)}</dd>
        <dt className="text-muted-foreground">Record</dt>
        <dd className="break-all font-mono text-xs">{decision.record_id}</dd>
      </dl>
      <details className="min-w-0 text-sm">
        <summary className="cursor-pointer font-medium text-accent">Read the record</summary>
        <div
          role="region"
          aria-label="The decision record"
          tabIndex={0}
          className="mt-2 max-h-96 overflow-auto rounded-md border border-border bg-card p-3 outline-none focus-visible:outline-2 focus-visible:outline-ring"
        >
          <PlainMarkdown text={recorded.markdown} className="text-xs" />
        </div>
      </details>
      {problem ? <ProblemNotice problem={problem} /> : null}
      <Button type="button" variant="outline" size="sm" className="self-start" onClick={download} disabled={busy}>
        <Download aria-hidden="true" />
        {busy ? "Preparing..." : "Download the record (JSON)"}
      </Button>
    </section>
  );
}

function ActionButton({ state, selected, onSelect }: { state: ActionCheck; selected: boolean; onSelect: () => void }) {
  const Icon = ACTION_ICONS[state.action];
  const reasonId = `action-${state.action}-reason`;
  return (
    <div className="flex flex-col gap-1.5">
      <Button
        type="button"
        variant={selected ? "default" : "outline"}
        disabled={!state.allowed}
        aria-pressed={selected}
        aria-describedby={state.allowed ? undefined : reasonId}
        onClick={onSelect}
        className={cn(
          "h-auto min-h-10 w-full justify-start whitespace-normal py-2 text-left",
          state.action === "decline" && !selected && "text-conflicting",
        )}
      >
        {state.allowed ? <Icon aria-hidden="true" /> : <Lock aria-hidden="true" />}
        {actionLabel(state.action)}
      </Button>
      {!state.allowed ? (
        <div id={reasonId} className="text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Not available for you here:</span>
          <ul className="list-disc pl-4">
            {state.reasons.map((reason) => (
              <li key={reason}>{readableReason(reason)}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function DecisionPanel({
  file,
  recorded,
  onRecorded,
}: {
  file: LenderFile;
  recorded: RecordedDecision | null;
  onRecorded: (recorded: RecordedDecision) => void;
}) {
  const states = actionStates(file);
  const [action, setAction] = useState<ReviewerAction | null>(null);
  const [rationale, setRationale] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refusal, setRefusal] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [justRecorded, setJustRecorded] = useState(false);
  const chosen = states.find((state) => state.action === action) ?? null;
  const rationaleOk = rationale.trim().length >= MIN_RATIONALE;
  const overrideOk = !chosen?.needs_override_reason || overrideReason !== "";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!chosen || !rationaleOk || !overrideOk) {
      return;
    }
    setSubmitting(true);
    setRefusal(null);
    setProblem(null);
    try {
      const saved = await lenderApi.decide(file.id, {
        action: chosen.action,
        rationale: rationale.trim(),
        override_reason: chosen.needs_override_reason ? overrideReason : null,
      });
      setJustRecorded(true);
      onRecorded({ decision: saved.decision, markdown: saved.markdown });
    } catch (error) {
      const messages = error instanceof ApiError ? errorMessages(error.detail) : [];
      if (error instanceof ApiError && error.status === 422 && messages[0] === CHECKPOINT_REFUSAL) {
        const reasons = messages.slice(1);
        setRefusal(reasons.length > 0 ? reasons : [CHECKPOINT_REFUSAL]);
      } else {
        setProblem(describeProblem(error, "Recording the decision"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="decision-title" className="flex flex-col gap-4 rounded-xl border-2 border-accent bg-card p-4 sm:p-5">
      <div className="flex flex-col gap-1">
        <h2 id="decision-title" className="flex scroll-mt-24 items-center gap-2 font-serif text-xl font-semibold">
          <Gavel className="size-5 text-accent" aria-hidden="true" />
          Your decision
        </h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          The policy suggests{" "}
          <strong className="font-semibold text-foreground">{recommendationLabel(file.reference_recommendation)}</strong>. You
          decide. You act as <strong className="font-semibold text-foreground">{reviewerRoleLabel(file.your_reviewer_role)}</strong>{" "}
          on this DEMO file; this file needs{" "}
          <strong className="font-semibold text-foreground">{withArticle(reviewerRoleLabel(file.required_reviewer_role).toLowerCase())}</strong>.
        </p>
      </div>

      {recorded ? (
        <RecordView fileId={file.id} recorded={recorded} focusOnShow={justRecorded} />
      ) : (
        <>
          <div className="flex flex-col gap-3">
            {states.map((state) => (
              <ActionButton
                key={state.action}
                state={state}
                selected={action === state.action}
                onSelect={() => {
                  setAction(state.action);
                  setRefusal(null);
                }}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Unavailable actions were checked by the server against the guidelines for your role. Every decision is checked
            again when you submit it.
          </p>

          {chosen ? (
            <form onSubmit={submit} className="flex flex-col gap-4 border-t border-border pt-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="rationale">Why {actionLabel(chosen.action).toLowerCase()}? (required)</Label>
                <textarea
                  id="rationale"
                  rows={4}
                  maxLength={MAX_RATIONALE}
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                  aria-describedby="rationale-hint"
                  className="w-full rounded-md border border-input bg-card px-3 py-2 text-base focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:text-sm"
                />
                <p id="rationale-hint" className="text-xs text-muted-foreground">
                  At least {MIN_RATIONALE} characters. It goes into the decision record. Do not include account numbers or
                  Social Security numbers.
                </p>
              </div>
              {chosen.needs_override_reason ? (
                <div className="flex flex-col gap-2">
                  <Label htmlFor="override-reason">Override reason (required: this differs from the policy&apos;s suggestion)</Label>
                  <select
                    id="override-reason"
                    value={overrideReason}
                    onChange={(event) => setOverrideReason(event.target.value)}
                    className="h-10 rounded-md border border-input bg-card px-3 text-base focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:text-sm"
                  >
                    <option value="">Choose an approved reason</option>
                    {file.override_reasons.map((reason) => (
                      <option key={reason} value={reason}>
                        {overrideReasonLabel(reason)}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              {refusal ? (
                <Alert variant="destructive" role="alert">
                  <Ban aria-hidden="true" />
                  <AlertTitle>The checkpoint refused this decision</AlertTitle>
                  <AlertDescription className="text-foreground/85">
                    <ul className="list-disc space-y-1 pl-5">
                      {refusal.map((reason) => (
                        <li key={reason}>{readableReason(reason)}</li>
                      ))}
                    </ul>
                    <p>Nothing was recorded. The file is still waiting for a decision.</p>
                  </AlertDescription>
                </Alert>
              ) : null}
              {problem ? <ProblemNotice problem={problem} /> : null}
              <Button type="submit" size="lg" disabled={submitting || !rationaleOk || !overrideOk} className="self-start">
                <Gavel aria-hidden="true" />
                {submitting ? "Recording..." : `Record: ${actionLabel(chosen.action)}`}
              </Button>
            </form>
          ) : null}
        </>
      )}
    </section>
  );
}
