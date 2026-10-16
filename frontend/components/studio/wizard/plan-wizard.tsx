"use client";

import { ArrowLeft, ArrowRight, Check, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { PageIntro } from "@/components/layout/page-intro";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { SignInPrompt } from "@/components/session/sign-in-prompt";
import { MetroModeBadge } from "@/components/studio/metro-mode-badge";
import { PrototypeDataNotice } from "@/components/studio/prototype-data-notice";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { useSession } from "@/lib/auth";
import { formatUsd } from "@/lib/format";
import { studioApi } from "@/lib/studio-api";
import type { StudioOptions } from "@/lib/studio-types";
import { useApiResource } from "@/lib/use-api";
import { cn } from "@/lib/utils";

import { AmountField } from "./amount-field";
import { ChoiceCards } from "./choice-cards";
import { checkStep, EMPTY_DRAFT, parseAmount, STEPS, toProjectInput, type Draft, type DraftSite, type StepErrors } from "./draft";
import { SitesStep } from "./sites-step";

/**
 * The "Start a plan" wizard: city -> business type -> idea -> money -> sites -> review, then
 * POST /api/studio/projects and on to the results page (/studio/project/?id=...).
 *
 * Accessibility: each step's heading takes focus when the step changes, errors are listed in an alert and next to
 * their fields, the step list marks the current step with aria-current, and every control is a native or Radix
 * control with a label.
 */

function StepList({ current, reached, onGo }: { current: number; reached: number; onGo: (index: number) => void }) {
  return (
    <nav aria-label="Plan steps">
      <ol className="flex gap-1.5 overflow-x-auto pb-1 sm:gap-2">
        {STEPS.map((step, index) => {
          const done = index < current;
          const isCurrent = index === current;
          const reachable = index <= reached && !isCurrent;
          const content = (
            <>
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  isCurrent && "border-accent bg-accent text-accent-ink",
                  done && "border-accent text-accent",
                  !isCurrent && !done && "border-rule-strong text-muted-foreground",
                )}
                aria-hidden="true"
              >
                {done ? <Check className="size-3.5" strokeWidth={3} /> : index + 1}
              </span>
              <span className={cn("hidden whitespace-nowrap text-sm sm:inline", isCurrent ? "font-semibold" : "text-muted-foreground")}>
                {step.title}
              </span>
              <span className="sr-only sm:hidden">{step.title}</span>
              {done ? <span className="sr-only"> (done)</span> : null}
            </>
          );
          return (
            <li key={step.id} className="flex items-center" aria-current={isCurrent ? "step" : undefined}>
              {reachable ? (
                <button type="button" onClick={() => onGo(index)} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted">
                  {content}
                </button>
              ) : (
                <span className="flex items-center gap-2 px-1.5 py-1">{content}</span>
              )}
              {index < STEPS.length - 1 ? <span className="ml-1 h-px w-3 bg-rule-strong sm:w-5" aria-hidden="true" /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function ErrorSummary({ errors }: { errors: StepErrors }) {
  const messages = Object.values(errors);
  if (messages.length === 0) {
    return null;
  }
  return (
    <Alert variant="destructive" role="alert">
      <ShieldAlert aria-hidden="true" />
      <AlertTitle>Please fix {messages.length === 1 ? "this" : `these ${messages.length} things`} to continue</AlertTitle>
      <AlertDescription className="text-foreground/85">
        <ul className="list-disc space-y-1 pl-5">
          {messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}

/** Where a site came from, for the review list; empty when the label already says it. */
function siteDetail(site: DraftSite): string {
  if (site.source === "address") {
    return site.address;
  }
  if (site.geoid) {
    return site.label.includes(site.geoid) ? "" : `tract ${site.geoid}`;
  }
  return "picked on the map";
}

function ReviewRow({ label, children, onEdit }: { label: string; children: React.ReactNode; onEdit: () => void }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[12rem_1fr_auto] sm:items-start sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm">{children}</dd>
      <dd>
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={onEdit} aria-label={`Edit ${label.toLowerCase()}`}>
          Edit
        </Button>
      </dd>
    </div>
  );
}

function WizardBody({ options }: { options: StudioOptions }) {
  const router = useRouter();
  const [stepIndex, setStepIndex] = useState(0);
  const [reached, setReached] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<StepErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitProblem, setSubmitProblem] = useState<Problem | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const firstRender = useRef(true);

  const step = STEPS[stepIndex] ?? STEPS[0];
  const limits = options.limits;
  const metro = options.metros.find((item) => item.id === draft.metroId);
  const business = options.business_types.find((item) => item.id === draft.businessTypeId);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [stepIndex]);

  function update(change: (current: Draft) => Draft) {
    setDraft((current) => change(current));
  }

  function goTo(index: number) {
    setErrors({});
    setStepIndex(index);
  }

  const assumedBuildOut = business?.build_out_assumed ?? null;

  function next() {
    const found = checkStep(step.id, draft, limits, assumedBuildOut);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      return;
    }
    const target = Math.min(stepIndex + 1, STEPS.length - 1);
    setReached((value) => Math.max(value, target));
    setStepIndex(target);
  }

  async function submit() {
    for (const [index, item] of STEPS.entries()) {
      const found = checkStep(item.id, draft, limits, assumedBuildOut);
      if (Object.keys(found).length > 0) {
        setErrors(found);
        setStepIndex(index);
        return;
      }
    }
    setSubmitting(true);
    setSubmitProblem(null);
    try {
      const project = await studioApi.createProject(toProjectInput(draft));
      router.push(`/studio/project/?id=${encodeURIComponent(project.id)}`);
    } catch (error) {
      setSubmitProblem(describeProblem(error, "Saving the plan"));
      setSubmitting(false);
    }
  }

  const rateChip = draft.ratePct.trim() === "" ? <EvidenceChip status="ASSUMPTION" /> : <EvidenceChip status="USER_STATED" />;
  // A city without a prime rate here cannot fill an empty rate: say so where the rate is typed.
  const noPrimeRate = metro !== undefined && (metro.prime_rate === null || metro.prime_rate.value === null || metro.prime_rate.status === "UNKNOWN");

  return (
    <div className="flex flex-col gap-6">
      <StepList current={stepIndex} reached={reached} onGo={goTo} />
      <Card>
        <CardContent className="flex flex-col gap-6 pt-5 sm:pt-6">
          <div className="flex flex-col gap-1">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-accent">
              Step {stepIndex + 1} of {STEPS.length}
            </p>
            <h2 ref={headingRef} tabIndex={-1} className="font-serif text-2xl font-semibold outline-none">
              {
                {
                  city: "Which city?",
                  business: "What kind of business?",
                  idea: "Describe your idea",
                  money: "The money plan",
                  sites: "Candidate sites",
                  review: "Check and create the plan",
                }[step.id]
              }
            </h2>
          </div>

          <ErrorSummary errors={errors} />

          {step.id === "city" ? (
            <ChoiceCards
              name="metro"
              legend="City"
              columns={3}
              value={draft.metroId}
              error={errors.metroId}
              onChange={(value) => update((current) => ({ ...current, metroId: value, sites: value === current.metroId ? current.sites : [] }))}
              options={options.metros.map((item) => ({
                value: item.id,
                title: item.name,
                badge: <MetroModeBadge mode={item.mode} />,
                description: item.coverage_note,
              }))}
            />
          ) : null}
          {step.id === "city" && metro && !metro.simulation_available ? (
            <Alert variant="warning">
              <ShieldAlert aria-hidden="true" />
              <AlertTitle>{metro.name}: site scores only on this server</AlertTitle>
              <AlertDescription className="text-foreground/85">
                <p>
                  This server has no data snapshot for {metro.name}, so each site&apos;s demand is unknown: you can add and score
                  sites, but the simulation and the path to approval will not run. Pick the SYNTHETIC demo city to see the
                  full flow.
                </p>
              </AlertDescription>
            </Alert>
          ) : null}

          {step.id === "business" ? (
            <ChoiceCards
              name="business"
              legend="Business type"
              columns={3}
              value={draft.businessTypeId}
              error={errors.businessTypeId}
              onChange={(value) => update((current) => ({ ...current, businessTypeId: value }))}
              options={options.business_types.map((item) => ({
                value: item.id,
                title: item.name,
                description: `Demand is estimated in ${item.demand_unit}.`,
              }))}
            />
          ) : null}

          {step.id === "idea" ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="idea">Your idea in your own words</Label>
              <textarea
                id="idea"
                rows={6}
                maxLength={limits.idea_text_max_chars}
                value={draft.idea}
                onChange={(event) => update((current) => ({ ...current, idea: event.target.value }))}
                aria-describedby="idea-hint idea-count"
                aria-invalid={errors.idea ? true : undefined}
                className="w-full rounded-md border border-input bg-card px-3 py-2 text-base text-foreground placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring aria-invalid:border-destructive sm:text-sm"
                placeholder="For example: a self-service laundromat near apartment buildings, open every day, with an attendant a few hours a day."
              />
              <p id="idea-count" className="figure-nums text-right text-xs text-muted-foreground">
                {draft.idea.length} / {limits.idea_text_max_chars}
              </p>
              <p id="idea-hint" className="text-xs leading-relaxed text-muted-foreground">
                Stored with your plan and shown back to you. The software treats it as data, never as instructions, and no
                calculation uses it. Do not include Social Security numbers, tax returns or bank account numbers.
              </p>
              {errors.idea ? <p className="text-sm font-medium text-conflicting">{errors.idea}</p> : null}
            </div>
          ) : null}

          {step.id === "money" ? (
            <div className="flex flex-col gap-6">
              <p className="text-sm leading-relaxed text-muted-foreground">
                Numbers you type are marked <strong className="font-semibold text-foreground">user stated</strong>: no
                document backs them yet. A field you leave empty uses a stated range or default instead, marked as an{" "}
                <strong className="font-semibold text-foreground">assumption</strong>.
              </p>
              <div className="grid gap-5 sm:grid-cols-2">
                <AmountField
                  id="budget"
                  label="Build-out and equipment budget"
                  prefix="$"
                  optional
                  hint={
                    business
                      ? `Everything you buy or build before opening. Empty: the simulation assumes ${formatUsd(business.build_out_assumed)}, the most likely value of the ${business.name.toLowerCase()} range of ${formatUsd(business.build_out_low)} to ${formatUsd(business.build_out_high)} (${business.build_out_status === "ASSUMPTION" ? "an assumption" : "sourced"}).`
                      : "Everything you buy or build before opening."
                  }
                  value={draft.budgetTotal}
                  onChange={(value) => update((current) => ({ ...current, budgetTotal: value }))}
                  error={errors.budgetTotal}
                  chip={draft.budgetTotal.trim() === "" ? <EvidenceChip status="ASSUMPTION" /> : <EvidenceChip status="USER_STATED" />}
                />
                <AmountField
                  id="equity"
                  label="Your own cash (equity)"
                  prefix="$"
                  hint="Cash you put in yourself. A lender checks it against bank statements."
                  value={draft.ownerEquity}
                  onChange={(value) => update((current) => ({ ...current, ownerEquity: value }))}
                  error={errors.ownerEquity}
                  chip={<EvidenceChip status="USER_STATED" />}
                />
                <AmountField
                  id="loan"
                  label="Loan amount"
                  prefix="$"
                  value={draft.loanAmount}
                  onChange={(value) => update((current) => ({ ...current, loanAmount: value }))}
                  error={errors.loanAmount}
                  chip={<EvidenceChip status="USER_STATED" />}
                />
                <AmountField
                  id="term"
                  label="Loan term"
                  suffix="months"
                  inputMode="numeric"
                  hint={`From ${limits.loan_term_months_min} to ${limits.loan_term_months_max} months.`}
                  value={draft.termMonths}
                  onChange={(value) => update((current) => ({ ...current, termMonths: value }))}
                  error={errors.termMonths}
                  chip={<EvidenceChip status="USER_STATED" />}
                />
                <AmountField
                  id="rate"
                  label="Interest rate per year"
                  suffix="%"
                  optional
                  hint={
                    noPrimeRate
                      ? `${metro?.name ?? "This city"} has no prime rate on this server, so an empty rate cannot be filled in: enter the lender's quoted rate.`
                      : `Empty: ${options.annual_rate_default.rule}`
                  }
                  value={draft.ratePct}
                  onChange={(value) => update((current) => ({ ...current, ratePct: value }))}
                  error={errors.ratePct}
                  chip={rateChip}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                Do not enter bank account numbers anywhere in this prototype.
              </p>
            </div>
          ) : null}

          {step.id === "sites" ? <SitesStep draft={draft} update={update} metro={metro} business={business} errors={errors} limits={limits} /> : null}

          {step.id === "review" ? (
            <div className="flex flex-col gap-4">
              <dl className="divide-y divide-border">
                <ReviewRow label="City" onEdit={() => goTo(0)}>
                  <span className="flex flex-wrap items-center gap-2">
                    {metro?.name}
                    {metro ? <MetroModeBadge mode={metro.mode} /> : null}
                  </span>
                </ReviewRow>
                <ReviewRow label="Business type" onEdit={() => goTo(1)}>
                  {business?.name}
                </ReviewRow>
                <ReviewRow label="Your idea" onEdit={() => goTo(2)}>
                  <span className="whitespace-pre-line break-words">{draft.idea}</span>
                </ReviewRow>
                <ReviewRow label="Money" onEdit={() => goTo(3)}>
                  <ul className="figure-nums flex flex-col gap-1">
                    <li>
                      Build-out budget:{" "}
                      {draft.budgetTotal.trim() === ""
                        ? `${formatUsd(assumedBuildOut)}, assumed for a ${business?.name.toLowerCase() ?? "business of this type"} (assumption)`
                        : formatUsd(parseAmount(draft.budgetTotal))}
                    </li>
                    <li>Your cash: {formatUsd(parseAmount(draft.ownerEquity))}</li>
                    <li>
                      Loan: {formatUsd(parseAmount(draft.loanAmount))} over {draft.termMonths} months
                    </li>
                    <li>
                      Rate: {draft.ratePct.trim() === "" ? "the city's prime rate plus a spread (assumption)" : `${draft.ratePct}% per year`}
                    </li>
                  </ul>
                </ReviewRow>
                <ReviewRow label="Sites" onEdit={() => goTo(4)}>
                  <ol className="flex flex-col gap-1">
                    {draft.sites.map((site, index) => (
                      <li key={site.key}>
                        {index + 1}. {site.label}
                        {siteDetail(site) ? <span className="text-muted-foreground"> ({siteDetail(site)})</span> : null}
                      </li>
                    ))}
                  </ol>
                </ReviewRow>
              </dl>
              <p className="text-sm leading-relaxed text-muted-foreground">
                Creating the plan saves it to your account (encrypted). The opening date is set six months ahead, marked as
                an assumption. Results (site scores, the simulation and the path to approval) are computed on the next page.
                You can delete the plan at any time.
              </p>
              {submitProblem ? <ProblemNotice problem={submitProblem} onRetry={submit} retrying={submitting} /> : null}
            </div>
          ) : null}

          <div className="flex flex-col-reverse gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between">
            {stepIndex > 0 ? (
              <Button type="button" variant="ghost" onClick={() => goTo(stepIndex - 1)}>
                <ArrowLeft aria-hidden="true" />
                Back
              </Button>
            ) : (
              <Button asChild variant="ghost">
                <Link href="/studio/">
                  <ArrowLeft aria-hidden="true" />
                  Your plans
                </Link>
              </Button>
            )}
            {step.id === "review" ? (
              <Button type="button" size="lg" onClick={submit} disabled={submitting}>
                {submitting ? "Creating the plan..." : "Create plan"}
                <ArrowRight aria-hidden="true" />
              </Button>
            ) : (
              <Button type="button" size="lg" onClick={next}>
                Continue
                <ArrowRight aria-hidden="true" />
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** The wizard page body: sign-in and loading states, then the steps. */
export function PlanWizard() {
  const { status, me } = useSession();
  const signedIn = status !== "loading" && status !== "offline" && me.signed_in;
  const options = useApiResource(signedIn ? "studio-options" : null, (signal) => studioApi.options(signal), "Loading cities and business types");

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14">
      <PageIntro
        eyebrow="Founder Studio · New plan"
        title="Start a plan"
        description={
          <p>
            Six short steps. The plan is private to your account and stored encrypted. For a real city, a street address you
            type is sent to the US Census Geocoder to find it; nothing else you type leaves the server.
          </p>
        }
      />
      <PrototypeDataNotice />
      {status === "loading" ? <WorkspaceSkeleton /> : null}
      {status === "offline" ? <ApiOfflineNotice /> : null}
      {status === "ready" && !me.signed_in ? (
        <SignInPrompt area="Founder Studio" reason="Plans are saved to your account. Sign in with Hugging Face to start one." />
      ) : null}
      {signedIn && options.status === "loading" ? <WorkspaceSkeleton /> : null}
      {options.problem ? <ProblemNotice problem={options.problem} onRetry={options.reload} retrying={options.refreshing} /> : null}
      {options.data ? <WizardBody options={options.data} /> : null}
    </div>
  );
}
