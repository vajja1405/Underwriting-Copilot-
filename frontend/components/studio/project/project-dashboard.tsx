"use client";

import { ArrowLeft, MapPin, RotateCcw, ShieldAlert } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { DataStatusChip } from "@/components/evidence/data-status-chip";
import { Figure } from "@/components/evidence/figure";
import { PageIntro } from "@/components/layout/page-intro";
import { ResultsDisclaimer } from "@/components/layout/results-disclaimer";
import { SyntheticBanner } from "@/components/layout/synthetic-banner";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { SignInPrompt } from "@/components/session/sign-in-prompt";
import { MetroModeBadge } from "@/components/studio/metro-mode-badge";
import { FeasibilityPanel } from "@/components/studio/project/feasibility-panel";
import { PathPanel } from "@/components/studio/project/path-panel";
import { PlanActions } from "@/components/studio/project/plan-actions";
import { PlanEditor } from "@/components/studio/project/plan-editor";
import { isScored, SiteCards, SiteCardsSkeleton, SiteDetail } from "@/components/studio/project/site-panels";
import type { TargetEdits } from "@/components/studio/project/target-editor";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { useSession } from "@/lib/auth";
import { formatDate, formatPercent, formatUsd } from "@/lib/format";
import { useQueryParam } from "@/lib/location";
import { studioApi } from "@/lib/studio-api";
import type { ChangeSet, PlanChangeNote, Project, StudioOptions } from "@/lib/studio-types";
import { useApiResource } from "@/lib/use-api";

const SiteMap = dynamic(() => import("@/components/studio/map/site-map"), {
  ssr: false,
  loading: () => <Skeleton className="h-64 w-full rounded-lg" />,
});

/**
 * /studio/project/?id=...: the results of one plan.
 *
 * Order of calls (each with its own loading and error state, so one failure does not hide the rest):
 * 1. GET the plan (and the options, for the metro's map size and the default targets);
 * 2. POST sites/score for every site;
 * 3. POST feasibility for the selected site;
 * 4. POST path-to-approval for the same site (after the simulation, to keep the server's load down), with the
 *    founder's edits to the planning targets when there are any ("Edit the targets").
 * Results are keyed by the plan version, so "Apply this change" or "Edit the numbers" (a PUT that bumps the version)
 * re-runs them all. The server caches results per version, so reloading the page does not recompute, and it uses
 * the same simulation seed for every version, so an applied change shows exactly the numbers its card promised.
 * The page opens on the plan's selected site (stored with the plan, so an applied site switch survives a reload).
 */

function SectionHeading({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-accent">{eyebrow}</p>
      <h2 id={id} className="font-serif text-2xl font-semibold leading-tight sm:text-3xl">
        {title}
      </h2>
      {children ? <div className="max-w-3xl text-sm leading-relaxed text-muted-foreground">{children}</div> : null}
    </div>
  );
}

/** The note under a figure written by "Apply this change": what a lender would need before it counts. */
function PlanChangeNoteText({ note }: { note: PlanChangeNote | undefined }) {
  if (!note) {
    return null;
  }
  return (
    <span>
      Written by &ldquo;Apply this change&rdquo;, not typed by you.
      {note.evidence_needed.length > 0 ? ` A lender would need: ${note.evidence_needed.join("; ")}.` : ""}
    </span>
  );
}

/**
 * The plan's money, each figure with who wrote it: typed by the founder (user stated), left empty (assumption: the
 * value the simulation uses is shown), written by an applied change-set (suggested change, with the evidence a lender
 * would need) or calculated by the server.
 */
function PlanFigures({ project }: { project: Project }) {
  const money = project.money;
  const changes = money.plan_changes;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
      <Figure
        label="Build-out and equipment budget"
        value={money.budget_total === null ? formatUsd(money.build_out_assumed) : formatUsd(money.budget_total)}
        status={money.budget_status}
        note={
          money.budget_total === null ? (
            `You left it empty: the simulation assumes this ${project.business_type_name.toLowerCase()} build-out.`
          ) : (
            <PlanChangeNoteText note={changes.budget_total} />
          )
        }
      />
      <Figure
        label="Your own cash (equity)"
        value={formatUsd(money.owner_equity)}
        status={money.owner_equity_status}
        note={<PlanChangeNoteText note={changes.owner_equity} />}
      />
      <Figure
        label="Loan amount"
        value={formatUsd(money.loan_amount)}
        status={money.loan_amount_status}
        note={<PlanChangeNoteText note={changes.loan_amount} />}
      />
      <Figure
        label="Loan term"
        value={`${money.loan_term_months} months`}
        status={money.loan_term_status}
        note={<PlanChangeNoteText note={changes.loan_term_months} />}
      />
      <Figure
        label={`Interest rate (${money.rate_type})`}
        value={money.annual_rate_pct === null ? "Prime + spread" : formatPercent(money.annual_rate_pct)}
        status={money.annual_rate_status}
        note={money.annual_rate_pct === null ? "The simulation shows the rate it used and its source." : undefined}
      />
      <Figure
        label="Cash reserve at opening"
        value={formatUsd(money.cash_reserve_at_opening)}
        status={money.cash_reserve_status}
        note="Your cash plus the loan, minus the build-out (calculated by the server). Each simulated future also takes a simulated build-out overrun out of it."
      />
    </div>
  );
}

function SitesList({ project }: { project: Project }) {
  return (
    <ul className="grid gap-2 text-sm sm:grid-cols-3">
      {project.sites.map((site) => (
        <li key={site.index} className="flex items-start gap-2">
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-viz-1 text-[0.6875rem] font-semibold text-accent-ink" aria-hidden="true">
            {site.index + 1}
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="font-medium">{site.label}</span>
            <span className="break-words text-xs text-muted-foreground">
              {site.matched_address ?? site.address ?? (site.tract_geoid ? `Tract ${site.tract_geoid}` : "Map pin")} · {site.location_check}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Dashboard({ project, options, onProjectChange }: { project: Project; options: StudioOptions | null; onProjectChange: (project: Project) => void }) {
  const { productName, disclaimer } = useSession();
  const [selected, setSelected] = useState<number | null>(null);
  const [applyingRank, setApplyingRank] = useState<number | null>(null);
  const [applyProblem, setApplyProblem] = useState<Problem | null>(null);
  const [applied, setApplied] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [maxExtraCash, setMaxExtraCash] = useState<number | null>(null);
  const appliedRef = useRef<HTMLParagraphElement | null>(null);
  const savedRef = useRef<HTMLParagraphElement | null>(null);

  const metro = options?.metros.find((item) => item.id === project.metro_id);
  const business = options?.business_types.find((item) => item.id === project.business_type_id) ?? null;
  const version = project.version;

  const scores = useApiResource(`scores:${project.id}:${version}`, (signal) => studioApi.scoreSites(project.id, signal), "Scoring the sites");
  const scoredSites = scores.data?.sites.filter(isScored) ?? [];
  const storedChoice = scoredSites.some((item) => item.index === project.selected_site_index) ? project.selected_site_index : null;
  const siteIndex = selected ?? storedChoice ?? scoredSites[0]?.index ?? null;
  const selectedSite = scores.data?.sites.find((item) => item.index === siteIndex) ?? null;

  // After "Apply this change" or a save, move keyboard and screen-reader focus to the status line that says what
  // happened (it is far above the button that was pressed).
  useEffect(() => {
    if (applied) {
      appliedRef.current?.focus();
    }
  }, [applied]);
  useEffect(() => {
    if (saved) {
      savedRef.current?.focus();
    }
  }, [saved]);

  const feasibility = useApiResource(
    siteIndex === null || scores.status !== "ready" ? null : `feas:${project.id}:${version}:${siteIndex}`,
    (signal) => studioApi.feasibility(project.id, siteIndex ?? 0, signal),
    "The simulation",
  );
  const [targetEdits, setTargetEdits] = useState<TargetEdits | null>(null);
  const path = useApiResource(
    siteIndex === null || feasibility.status !== "ready"
      ? null
      : `path:${project.id}:${version}:${siteIndex}:${JSON.stringify(targetEdits ?? {})}:${maxExtraCash ?? "default"}`,
    (signal) => studioApi.pathToApproval(project.id, siteIndex ?? 0, signal, targetEdits, maxExtraCash),
    "The path to approval",
  );
  const tracts = useApiResource(
    metro && metro.n_tracts > 0 ? `tracts:${project.metro_id}` : null,
    (signal) => studioApi.tracts(project.metro_id, signal),
    "Loading the map",
  );

  async function apply(changeSet: ChangeSet) {
    setApplyingRank(changeSet.rank);
    setApplyProblem(null);
    setApplied(null);
    try {
      const updated = await studioApi.updateProject(project.id, {
        version: project.version,
        ...changeSet.apply.project_fields,
        plan_change: changeSet.apply.plan_change,
      });
      setSelected(changeSet.apply.site_index);
      onProjectChange(updated);
      setApplied(
        `Applied. Your plan is now version ${updated.version} (site ${changeSet.apply.site_index + 1}); the changed figures are marked as suggested changes, and the sites, the simulation and the path are being recomputed.`,
      );
    } catch (error) {
      setApplyProblem(describeProblem(error, "Applying the change"));
    } finally {
      setApplyingRank(null);
    }
  }

  function onSaved(updated: Project) {
    onProjectChange(updated);
    setSaved(`Saved. Your plan is now version ${updated.version}; the sites, the simulation and the path are being recomputed.`);
  }

  function downloadPayload(): object {
    return {
      product: productName,
      disclaimer,
      data_label: project.data_label,
      plan: project,
      site_scores: scores.data,
      feasibility: feasibility.data,
      path_to_approval: path.data,
    };
  }

  const pins = project.sites.map((site) => ({ key: `${site.index}`, number: site.index + 1, lat: site.lat, lon: site.lon }));
  const hasMap = tracts.status === "ready" && (tracts.data?.features.length ?? 0) > 0;
  const title = project.name || `${project.business_type_name} in ${project.metro_name}`;

  return (
    <div className="flex flex-col gap-12">
      <PageIntro
        eyebrow={`Founder Studio · Plan version ${version}`}
        title={title}
        description={project.idea.flagged || !project.idea.text ? null : <p className="whitespace-pre-line break-words">{project.idea.text}</p>}
      >
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <MetroModeBadge mode={project.metro_mode} />
          <span className="text-sm text-muted-foreground">{metro?.coverage_note ?? project.metro_name}</span>
        </div>
        {project.expires_at ? (
          <p className="text-xs text-muted-foreground">
            Stored encrypted. Deleted automatically on {formatDate(project.expires_at)}
            {project.retention_days ? ` (${project.retention_days}-day retention)` : ""}, or sooner if you delete it.
          </p>
        ) : null}
      </PageIntro>

      <div className="flex flex-col gap-3">
        <ResultsDisclaimer />
        {project.data_label === "SYNTHETIC" ? (
          <SyntheticBanner>
            This plan uses the generated demo city. Its tracts, places, wages, rents and rates are computer-generated and
            describe no real place.
          </SyntheticBanner>
        ) : null}
        {project.metro_mode === "limited" ? (
          <Alert variant="warning">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>Limited mode for {project.metro_name}</AlertTitle>
            <AlertDescription className="text-foreground/85">
              <p>
                {metro?.mode_reason ?? "Some factors have no public data here."} Factors without data show as unknown or as an
                assumption and are never filled in silently.
              </p>
              {metro && !metro.simulation_available ? (
                <p>
                  Without data, each site&apos;s demand is unknown, so the simulation and the path to approval do not run for this
                  city on this server. The SYNTHETIC demo city shows the full flow.
                </p>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}
        {project.idea.flagged ? (
          <Alert variant="warning">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>Your idea text was flagged</AlertTitle>
            <AlertDescription className="text-foreground/85">
              <p>
                {project.idea.note}
                {project.idea.flags.length > 0 ? ` (${project.idea.flags.join(", ").replaceAll("_", " ")})` : ""}
              </p>
              {project.idea.text ? (
                <p className="whitespace-pre-line break-words rounded-md border border-border bg-card p-2 font-mono text-xs">{project.idea.text}</p>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      <section aria-labelledby="plan-title" className="flex flex-col gap-4">
        <SectionHeading id="plan-title" eyebrow="Your plan" title="The numbers of your plan">
          Typed by you, so they count as <strong className="font-semibold text-foreground">user stated</strong> until a document backs
          them. A field you left empty uses a stated default, marked as an assumption. A figure written by &ldquo;Apply this
          change&rdquo; is marked as a <strong className="font-semibold text-foreground">suggested change</strong> until you type it
          yourself.
        </SectionHeading>
        {saved ? (
          <p ref={savedRef} tabIndex={-1} role="status" className="rounded-lg border border-accent/30 bg-accent-soft p-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-ring">
            {saved}
          </p>
        ) : null}
        <PlanFigures project={project} />
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Opening date:</span>
          <span className="font-medium">{formatDate(project.opening_date)}</span>
          <DataStatusChip status={project.opening_date_status} />
        </p>
        {options ? <PlanEditor key={version} project={project} business={business} limits={options.limits} onSaved={onSaved} /> : null}
        <PlanActions projectId={project.id} download={downloadPayload} />
      </section>

      <section aria-labelledby="sites-title" className="flex flex-col gap-4">
        <SectionHeading id="sites-title" eyebrow="Step 1 · SiteIQ" title="How your sites compare">
          Each site gets a score from 0 to 100: its percentile rank within this city on the factors that matter for this
          business type, weighted. Data coverage says how much of that weight had data. Pick a site to simulate it.
        </SectionHeading>
        {hasMap ? (
          <div className="flex flex-col gap-2">
            <SiteMap className="h-56 w-full sm:h-72" tracts={tracts.data} pins={pins} />
            <p className="text-xs text-muted-foreground">Your sites (numbered) among the city&apos;s census tract centers. No street map is drawn.</p>
          </div>
        ) : (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <MapPin className="size-4" aria-hidden="true" />
            No tract map for this city (limited mode); the sites are listed below.
          </p>
        )}
        <SitesList project={project} />
        {scores.status === "loading" ? <SiteCardsSkeleton count={project.sites.length} /> : null}
        {scores.problem ? <ProblemNotice problem={scores.problem} onRetry={scores.reload} retrying={scores.refreshing} /> : null}
        {scores.data ? (
          <div className={scores.refreshing ? "opacity-60 transition-opacity" : undefined}>
            <SiteCards
              response={scores.data}
              selected={siteIndex ?? -1}
              onSelect={setSelected}
              simulationAvailable={metro?.simulation_available ?? true}
            />
          </div>
        ) : null}
        {scores.data && scoredSites.length === 0 ? (
          <p className="text-sm text-muted-foreground">None of the sites could be scored, so nothing can be simulated yet.</p>
        ) : null}
        {selectedSite && isScored(selectedSite) && scores.data ? <SiteDetail site={selectedSite} scoringMethod={scores.data.scoring_method} /> : null}
      </section>

      <section aria-labelledby="feasibility-title" className="flex flex-col gap-4">
        <SectionHeading id="feasibility-title" eyebrow="Step 2 · Simulation" title={siteIndex === null ? "Feasibility" : `Feasibility at site ${siteIndex + 1}`}>
          A thousand possible futures, each drawing every uncertain input from its stated range. The ranges below are not a
          forecast of what will happen; they show what follows from the assumptions.
        </SectionHeading>
        {feasibility.status === "idle" && scores.status !== "error" ? <Skeleton className="h-40 w-full rounded-xl" /> : null}
        {feasibility.status === "loading" ? (
          <div className="flex flex-col gap-3" role="status" aria-busy="true" aria-label="Running the simulation">
            <p className="text-sm text-muted-foreground">Running 1,000 simulated futures... this can take a few seconds.</p>
            <div className="grid gap-4 md:grid-cols-3">
              <Skeleton className="h-48 rounded-xl" />
              <Skeleton className="h-48 rounded-xl" />
              <Skeleton className="h-48 rounded-xl" />
            </div>
          </div>
        ) : null}
        {feasibility.problem ? <ProblemNotice problem={feasibility.problem} onRetry={feasibility.reload} retrying={feasibility.refreshing} /> : null}
        {feasibility.data ? (
          <div className={feasibility.refreshing ? "opacity-60 transition-opacity" : undefined}>
            <FeasibilityPanel
              response={feasibility.data}
              target={path.data?.target ?? options?.default_target ?? null}
              rateTyped={project.money.annual_rate_status === "USER_STATED"}
            />
          </div>
        ) : null}
      </section>

      <section aria-labelledby="path-title" className="flex flex-col gap-4">
        <SectionHeading id="path-title" eyebrow="Step 3 · Path to approval" title="The smallest plan changes that reach your planning targets">
          The search moves only your own plan choices (cash, loan, build-out, reserve, term, site) inside set bounds. It never
          changes facts or assumptions. The targets are planning targets you can edit, not a lender&apos;s rules, and reaching
          them does not mean a lender will approve the loan.
        </SectionHeading>
        {applied ? (
          <p ref={appliedRef} tabIndex={-1} role="status" className="rounded-lg border border-accent/30 bg-accent-soft p-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-ring">
            {applied}
          </p>
        ) : null}
        {applyProblem ? <ProblemNotice problem={applyProblem} /> : null}
        {path.status === "idle" && feasibility.status !== "error" ? <Skeleton className="h-32 w-full rounded-xl" /> : null}
        {path.status === "idle" && feasibility.status === "error" ? (
          <p className="text-sm text-muted-foreground">The path to approval needs the simulation above first.</p>
        ) : null}
        {path.status === "loading" ? (
          <div className="flex flex-col gap-3" role="status" aria-busy="true" aria-label="Searching for a path to approval">
            <p className="text-sm text-muted-foreground">Searching plan changes (quick mode)...</p>
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        ) : null}
        {path.problem ? <ProblemNotice problem={path.problem} onRetry={path.reload} retrying={path.refreshing} /> : null}
        {path.problem && targetEdits ? (
          <Button type="button" variant="outline" size="sm" className="self-start" onClick={() => setTargetEdits(null)}>
            <RotateCcw aria-hidden="true" />
            Back to the default targets
          </Button>
        ) : null}
        {path.data ? (
          <div className={path.refreshing ? "opacity-60 transition-opacity" : undefined}>
            <PathPanel
              result={path.data}
              onApply={apply}
              applyingRank={applyingRank}
              canApply={!path.refreshing}
              defaults={options?.default_target ?? null}
              edits={targetEdits}
              onEditTargets={setTargetEdits}
              maxExtraCash={maxExtraCash}
              onMaxExtraCash={setMaxExtraCash}
              searching={path.refreshing}
            />
          </div>
        ) : null}
      </section>

      <div>
        <Button asChild variant="ghost">
          <Link href="/studio/">
            <ArrowLeft aria-hidden="true" />
            All your plans
          </Link>
        </Button>
      </div>
    </div>
  );
}

/** The page body: id from the query string, sign-in and loading states, then the dashboard. */
export function ProjectDashboard() {
  const { status, me } = useSession();
  const id = useQueryParam("id");
  const signedIn = status !== "loading" && status !== "offline" && me.signed_in;
  const project = useApiResource(signedIn && id ? `project:${id}` : null, (signal) => studioApi.getProject(id ?? "", signal), "Loading the plan");
  const options = useApiResource(signedIn ? "studio-options" : null, (signal) => studioApi.options(signal), "Loading city names");

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14">
      {/* Until the plan loads (or when it cannot), the page still needs its one h1 (axe: page-has-heading-one). */}
      {project.data ? null : <PageIntro eyebrow="Founder Studio" title="Plan results" />}
      {status === "loading" ? <WorkspaceSkeleton /> : null}
      {status === "offline" ? <ApiOfflineNotice /> : null}
      {status === "ready" && !me.signed_in ? (
        <SignInPrompt area="Founder Studio" reason="Plans are private to the account that made them. Sign in to open this one." />
      ) : null}
      {signedIn && !id ? (
        <Alert variant="warning" role="alert">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>No plan was named in the address</AlertTitle>
          <AlertDescription className="text-foreground/85">
            <p>
              Open a plan from{" "}
              <Link href="/studio/" className="font-semibold text-accent underline underline-offset-4">
                your plans
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      {signedIn && id && project.status === "loading" ? <WorkspaceSkeleton /> : null}
      {project.problem && !project.data ? <ProblemNotice problem={project.problem} onRetry={project.reload} retrying={project.refreshing} /> : null}
      {project.data ? <Dashboard project={project.data} options={options.data} onProjectChange={project.setData} /> : null}
    </div>
  );
}
