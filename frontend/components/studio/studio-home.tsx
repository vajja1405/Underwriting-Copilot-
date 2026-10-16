"use client";

import { ArrowRight, FileCheck2, ListChecks, MapPin, Plus, Share2, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { PageIntro } from "@/components/layout/page-intro";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { SessionErrorNotice } from "@/components/session/session-error-notice";
import { SignInPrompt } from "@/components/session/sign-in-prompt";
import { MetroModeBadge } from "@/components/studio/metro-mode-badge";
import { PrototypeDataNotice } from "@/components/studio/prototype-data-notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PlannedSections, type PlannedSection } from "@/components/workspace/planned-sections";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { WorkspaceSkeleton } from "@/components/workspace/workspace-skeleton";
import { useSession } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { studioApi } from "@/lib/studio-api";
import type { ProjectList, ProjectSummary, StudioOptions } from "@/lib/studio-types";
import { useApiResource } from "@/lib/use-api";

const LATER_SECTIONS: PlannedSection[] = [
  {
    icon: FileCheck2,
    title: "Lender-ready package",
    body: "A PDF and data file with every figure's evidence status, shared only when you choose to.",
  },
  {
    icon: ListChecks,
    title: "Evidence checklist and documents",
    body: "Upload quotes and leases so stated or assumed figures can become verified ones.",
  },
  {
    icon: Share2,
    title: "Hand-off to a lender",
    body: "Send the plan to a lender's review queue, with an expiring link you can revoke.",
  },
];

interface StudioData {
  options: StudioOptions;
  plans: ProjectList;
}

async function loadStudio(signal: AbortSignal): Promise<StudioData> {
  const [options, plans] = await Promise.all([studioApi.options(signal), studioApi.listProjects(signal)]);
  return { options, plans };
}

function PlanCard({ plan, options }: { plan: ProjectSummary; options: StudioOptions }) {
  const metro = options.metros.find((item) => item.id === plan.metro_id);
  const href = `/studio/project/?id=${encodeURIComponent(plan.id)}`;
  const title = plan.name || plan.business_type_name;

  return (
    <li>
      <Card className="flex h-full flex-col">
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {metro ? <MetroModeBadge mode={metro.mode} /> : null}
            <span className="text-xs text-muted-foreground">Version {plan.version}</span>
          </div>
          <CardTitle as="h3" className="font-serif text-xl">
            <Link href={href} className="underline-offset-4 hover:underline">
              {title}
            </Link>
          </CardTitle>
          <CardDescription>
            {plan.name ? `${plan.business_type_name} · ` : null}
            {plan.metro_name}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-3">
          <p className="flex items-center gap-1.5 text-sm">
            <MapPin className="size-4 text-accent" aria-hidden="true" />
            <span className="figure-nums">
              {plan.n_sites} candidate site{plan.n_sites === 1 ? "" : "s"}
            </span>
          </p>
          {plan.idea_flagged ? (
            <p className="flex items-start gap-1.5 text-xs text-stated">
              <ShieldAlert className="mt-px size-3.5 shrink-0" aria-hidden="true" />
              The idea text was flagged and is never sent to a language model.
            </p>
          ) : null}
          {plan.expires_at ? (
            <p className="text-xs text-muted-foreground">Deleted automatically on {formatDate(plan.expires_at)} unless you delete it sooner.</p>
          ) : null}
          <div className="mt-auto flex items-center justify-between gap-3 border-t border-border pt-3">
            <span className="text-xs text-muted-foreground">Updated {formatDate(plan.updated_at)}</span>
            <Button asChild variant="link" className="h-auto p-0">
              <Link href={href} aria-label={`Open the plan ${title} in ${plan.metro_name}`}>
                Open plan
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

/** Founder Studio landing: "Your plans" (newest first) and "Start a plan". */
export function StudioHome() {
  const { status, me } = useSession();
  const signedIn = status !== "loading" && status !== "offline" && me.signed_in;
  const studio = useApiResource(signedIn ? `studio:${me.username}` : null, loadStudio, "Loading your plans");
  const data = studio.data;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-10 px-4 py-10 sm:px-6 sm:py-14">
      <PageIntro
        eyebrow="Founder Studio"
        title="Plan a location. Prepare a file a lender can check."
        description={
          <p>
            Pick a city and a business type, add up to three candidate sites and your money plan. Studio scores the sites,
            simulates revenue and cash from stated assumptions, and shows the smallest plan changes that reach common
            lender targets. It prepares and explains; it does not promise approval or pick a lender for you.
          </p>
        }
      >
        {signedIn ? (
          <div className="pt-2">
            <Button asChild size="lg">
              <Link href="/studio/new/">
                <Plus aria-hidden="true" />
                Start a plan
              </Link>
            </Button>
          </div>
        ) : null}
      </PageIntro>

      <PrototypeDataNotice />

      {status === "loading" ? <WorkspaceSkeleton /> : null}
      {status === "offline" ? <ApiOfflineNotice /> : null}
      {status === "error" && !me.signed_in ? <SessionErrorNotice /> : null}
      {status === "ready" && !me.signed_in ? (
        <SignInPrompt area="Founder Studio" reason="Your plans are private to your account. Sign in with Hugging Face to start one." />
      ) : null}

      {signedIn ? (
        <section aria-labelledby="plans-title" className="flex flex-col gap-4">
          <div className="flex items-baseline justify-between gap-3">
            <h2 id="plans-title" className="font-serif text-2xl font-semibold">
              Your plans
            </h2>
            {data && data.plans.projects.length > 0 ? (
              <span className="text-sm text-muted-foreground">{data.plans.total} plan{data.plans.total === 1 ? "" : "s"}, newest first</span>
            ) : null}
          </div>

          {studio.status === "loading" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" role="status" aria-busy="true" aria-label="Loading your plans">
              <Skeleton className="h-56 rounded-xl" />
              <Skeleton className="h-56 rounded-xl" />
            </div>
          ) : null}

          {studio.problem ? <ProblemNotice problem={studio.problem} onRetry={studio.reload} retrying={studio.refreshing} /> : null}

          {data && data.plans.projects.length === 0 ? (
            <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-rule-strong bg-card/60 p-6">
              <p className="font-medium">You have no plans yet.</p>
              <p className="text-sm text-muted-foreground">
                A plan takes about five minutes: city, business type, your idea, the money, and one to three sites. Try the
                SYNTHETIC demo city first if you want to see how it works without real data.
              </p>
              <Button asChild>
                <Link href="/studio/new/">
                  <Plus aria-hidden="true" />
                  Start a plan
                </Link>
              </Button>
            </div>
          ) : null}

          {data && data.plans.projects.length > 0 ? (
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {data.plans.projects.map((plan) => (
                <PlanCard key={plan.id} plan={plan} options={data.options} />
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <PlannedSections title="Coming in a later version" sections={LATER_SECTIONS} />
    </div>
  );
}
