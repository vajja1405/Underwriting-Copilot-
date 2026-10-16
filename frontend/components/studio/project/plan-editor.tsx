"use client";

import { Pencil, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { AmountField } from "@/components/studio/wizard/amount-field";
import { parseAmount } from "@/components/studio/wizard/draft";
import { Button } from "@/components/ui/button";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { formatUsd } from "@/lib/format";
import { studioApi, type ProjectUpdate } from "@/lib/studio-api";
import type { BusinessTypeOption, Project, StudioLimits } from "@/lib/studio-types";

/**
 * "Edit the numbers": the plan's money after it was created (budget, own cash, loan, term, rate). Without it a plan
 * the simulation refuses (for example too little money for the assumed build-out) could only be deleted.
 *
 * Only the fields that differ from the plan are sent (a PUT with the plan version, so two tabs cannot overwrite each
 * other). A field typed here becomes the founder's own figure (USER_STATED) again, also when "Apply this change" had
 * written it. The server checks everything again and names the amounts when the money does not cover the build-out.
 */

interface MoneyDraft {
  budgetTotal: string;
  ownerEquity: string;
  loanAmount: string;
  termMonths: string;
  ratePct: string;
}

function draftOf(project: Project): MoneyDraft {
  const money = project.money;
  return {
    budgetTotal: money.budget_total === null ? "" : String(money.budget_total),
    ownerEquity: String(money.owner_equity),
    loanAmount: String(money.loan_amount),
    termMonths: String(money.loan_term_months),
    ratePct: money.annual_rate_pct === null ? "" : String(money.annual_rate_pct),
  };
}

/** Field name -> message for the problems the form can see before sending. */
function checkDraft(draft: MoneyDraft, limits: StudioLimits, assumedBuildOut: number | null): Record<string, string> {
  const errors: Record<string, string> = {};
  const budget = parseAmount(draft.budgetTotal);
  const equity = parseAmount(draft.ownerEquity);
  const loan = parseAmount(draft.loanAmount);
  const term = parseAmount(draft.termMonths);
  const rate = parseAmount(draft.ratePct);
  if (draft.budgetTotal.trim() !== "" && (budget === null || budget <= 0)) {
    errors.budgetTotal = "Enter the build-out and equipment budget in dollars (more than 0), or leave it empty.";
  }
  if (equity === null || equity < 0) {
    errors.ownerEquity = "Enter the cash you will put in yourself (0 or more).";
  }
  if (loan === null || loan <= 0) {
    errors.loanAmount = "Enter the loan amount in dollars (more than 0).";
  }
  if (term === null || !Number.isInteger(term) || term < limits.loan_term_months_min || term > limits.loan_term_months_max) {
    errors.termMonths = `Enter a whole number of months from ${limits.loan_term_months_min} to ${limits.loan_term_months_max}.`;
  }
  if (draft.ratePct.trim() !== "" && (rate === null || rate < 0 || rate > limits.annual_rate_pct_max)) {
    errors.ratePct = `Enter a yearly rate from 0 to ${limits.annual_rate_pct_max}, or leave it empty.`;
  }
  const buildOut = draft.budgetTotal.trim() === "" ? assumedBuildOut : budget;
  if (buildOut !== null && equity !== null && loan !== null && !errors.budgetTotal && equity + loan < buildOut) {
    errors.loanAmount =
      draft.budgetTotal.trim() === ""
        ? `Your cash plus the loan must cover the assumed build-out of ${formatUsd(buildOut)} (type your own budget to replace it).`
        : "Your cash plus the loan must cover the build-out budget; otherwise the plan opens with negative cash.";
  }
  return errors;
}

/** The PUT body: the plan version and only the fields that changed. */
function changes(project: Project, draft: MoneyDraft): ProjectUpdate {
  const money = project.money;
  const update: ProjectUpdate = { version: project.version };
  const budget = draft.budgetTotal.trim() === "" ? null : parseAmount(draft.budgetTotal);
  const rate = draft.ratePct.trim() === "" ? null : parseAmount(draft.ratePct);
  if (budget !== money.budget_total) update.budget_total = budget;
  const equity = parseAmount(draft.ownerEquity) ?? money.owner_equity;
  if (equity !== money.owner_equity) update.owner_equity = equity;
  const loan = parseAmount(draft.loanAmount) ?? money.loan_amount;
  if (loan !== money.loan_amount) update.loan_amount = loan;
  const term = parseAmount(draft.termMonths) ?? money.loan_term_months;
  if (term !== money.loan_term_months) update.loan_term_months = term;
  if (rate !== money.annual_rate_pct) update.annual_rate_pct = rate;
  return update;
}

export function PlanEditor({
  project,
  business,
  limits,
  onSaved,
}: {
  project: Project;
  business: BusinessTypeOption | null;
  limits: StudioLimits;
  /** Called with the new plan version after a successful save. */
  onSaved: (project: Project) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<MoneyDraft>(() => draftOf(project));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const openButton = useRef<HTMLButtonElement | null>(null);
  const assumed = business?.build_out_assumed ?? project.money.build_out_assumed;

  function start() {
    setDraft(draftOf(project));
    setErrors({});
    setProblem(null);
    setOpen(true);
  }

  function close() {
    setOpen(false);
    window.requestAnimationFrame(() => openButton.current?.focus());
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const found = checkDraft(draft, limits, assumed);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      return;
    }
    const update = changes(project, draft);
    if (Object.keys(update).length === 1) {
      close();
      return;
    }
    setSaving(true);
    setProblem(null);
    try {
      const updated = await studioApi.updateProject(project.id, update);
      setOpen(false);
      onSaved(updated);
    } catch (error) {
      setProblem(describeProblem(error, "Saving the numbers"));
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <Button ref={openButton} type="button" variant="outline" onClick={start} className="self-start">
        <Pencil aria-hidden="true" />
        Edit the numbers
      </Button>
    );
  }

  const set = (field: keyof MoneyDraft) => (value: string) => setDraft((current) => ({ ...current, [field]: value }));
  const typed = <EvidenceChip status="USER_STATED" />;
  const errorList = Object.values(errors);

  return (
    <form onSubmit={save} aria-labelledby="plan-editor-title" className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 sm:p-5" noValidate>
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h3 id="plan-editor-title" className="font-semibold">
            Edit the numbers
          </h3>
          <p className="text-sm text-muted-foreground">
            Saving makes a new version of the plan; the sites, the simulation and the path are computed again. Every number
            you type here counts as user stated.
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={close} aria-label="Close the editor without saving">
          <X aria-hidden="true" />
          Cancel
        </Button>
      </div>
      {errorList.length > 0 ? (
        <ul role="alert" className="list-disc space-y-1 pl-5 text-sm font-medium text-conflicting">
          {errorList.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}
      <div className="grid gap-5 sm:grid-cols-2">
        <AmountField
          id="edit-budget"
          label="Build-out and equipment budget"
          prefix="$"
          optional
          hint={
            assumed !== null
              ? `Empty: the simulation assumes ${formatUsd(assumed)}${business ? ` for a ${business.name.toLowerCase()}` : ""} (an assumption).`
              : "Everything you buy or build before opening."
          }
          value={draft.budgetTotal}
          onChange={set("budgetTotal")}
          error={errors.budgetTotal}
          chip={draft.budgetTotal.trim() === "" ? <EvidenceChip status="ASSUMPTION" /> : typed}
        />
        <AmountField id="edit-equity" label="Your own cash (equity)" prefix="$" value={draft.ownerEquity} onChange={set("ownerEquity")} error={errors.ownerEquity} chip={typed} />
        <AmountField id="edit-loan" label="Loan amount" prefix="$" value={draft.loanAmount} onChange={set("loanAmount")} error={errors.loanAmount} chip={typed} />
        <AmountField
          id="edit-term"
          label="Loan term"
          suffix="months"
          inputMode="numeric"
          hint={`From ${limits.loan_term_months_min} to ${limits.loan_term_months_max} months.`}
          value={draft.termMonths}
          onChange={set("termMonths")}
          error={errors.termMonths}
          chip={typed}
        />
        <AmountField
          id="edit-rate"
          label="Interest rate per year"
          suffix="%"
          optional
          hint="Empty: the city's prime rate plus a spread (an assumption), where the city has a prime rate."
          value={draft.ratePct}
          onChange={set("ratePct")}
          error={errors.ratePct}
          chip={draft.ratePct.trim() === "" ? <EvidenceChip status="ASSUMPTION" /> : typed}
        />
      </div>
      {problem ? <ProblemNotice problem={problem} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Save as a new version"}
        </Button>
      </div>
    </form>
  );
}
