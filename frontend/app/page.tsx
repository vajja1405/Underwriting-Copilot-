import {
  ArrowRight,
  Ban,
  FlaskConical,
  Gauge,
  Landmark,
  MapPinned,
  Scale,
  Store,
  UserCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { EvidenceLegend } from "@/components/evidence/evidence-legend";
import { SampleFileCard } from "@/components/landing/sample-file-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PRODUCT_NAME } from "@/lib/config";
import type { EvidenceStatus } from "@/lib/evidence";

/**
 * Landing page: what the product does for each side, how it works, the evidence-chip idea, and honest limits.
 * Everything here is static text; the only example numbers are the SYNTHETIC sample file (lib/sample-file.ts).
 */

interface Side {
  icon: LucideIcon;
  audience: string;
  name: string;
  href: string;
  summary: string;
  points: string[];
  /** What this side of the web app does today (v0.1), said plainly. */
  today: string;
}

const SIDES: Side[] = [
  {
    icon: Store,
    audience: "For founders",
    name: "Founder Studio",
    href: "/studio",
    summary: "Plan a new business location and build a file a lender can check.",
    points: [
      "Pick a city and a business type; describe the idea, budget and loan need.",
      "Compare candidate sites, each with a data-coverage score.",
      "See revenue and cash ranges, simulated from stated assumptions, each one labeled.",
      "Get a path to approval and a package you choose to share.",
    ],
    today: "In the web app today: the plan wizard, site scores, the simulation and the path to approval. The shared package comes later.",
  },
  {
    icon: Landmark,
    audience: "For lenders",
    name: "Lender Desk",
    href: "/lender",
    summary: "Review a loan file with the evidence beside every figure.",
    points: [
      "Figures extracted from documents, with page citations.",
      "Checks, and contradictions between documents.",
      "Stress tests and the next piece of evidence worth asking for.",
      "A draft memo. Approve, refer, request information or decline stays your call.",
    ],
    today:
      "In the web app today: SYNTHETIC demo files with findings, calculations, screening and a template memo; your decision goes to a demo sandbox. Documents with page citations come later.",
  },
];

interface Step {
  title: string;
  body: string;
  /** Evidence statuses this step produces (none for the human decision). */
  statuses: EvidenceStatus[];
}

const STEPS: Step[] = [
  {
    title: "Describe the plan",
    body: "The founder enters the city, business type, budget and loan need. Typed numbers are marked as stated.",
    statuses: ["USER_STATED", "ASSUMPTION"],
  },
  {
    title: "Check it against evidence",
    body: "Documents and public data confirm or contradict each figure. Gaps stay unknown instead of being guessed.",
    statuses: ["VERIFIED", "CONFLICTING", "UNKNOWN"],
  },
  {
    title: "Simulate the range",
    body: "Revenue and cash are simulated from stated assumptions and shown as ranges, labeled as estimates.",
    statuses: ["MODEL_ESTIMATE"],
  },
  {
    title: "A person decides",
    body: "A lender reviews the file. The software prepares and explains; it never approves or declines.",
    statuses: [],
  },
];

interface Limit {
  icon: LucideIcon;
  title: string;
  body: string;
}

const LIMITS: Limit[] = [
  {
    icon: Wrench,
    title: "A prototype",
    body: "A demonstration project. The planning tools and a lender demo on SYNTHETIC files work today; documents, sharing and the lender package are still being built.",
  },
  {
    icon: FlaskConical,
    title: "Synthetic demo data",
    body: "Example files are computer-generated and labeled SYNTHETIC. They describe no real person or business.",
  },
  {
    icon: MapPinned,
    title: "Limited city coverage",
    body: "Where public data is thin, a city runs in limited mode, and missing factors show as unknown or as an assumption.",
  },
  {
    icon: Gauge,
    title: "Simulated, not predicted",
    body: "Probabilities and ranges are simulated, based on stated assumptions. There is no claim of real-world accuracy.",
  },
  {
    icon: Scale,
    title: "Not advice, no decisions",
    body: `${PRODUCT_NAME} never approves, declines, promises approval or recommends a lender. Lenders make their own decisions.`,
  },
  {
    icon: Ban,
    title: "No payments",
    body: "There is nothing to buy. The prototype has no paid plans and asks for no card.",
  },
];

function SectionHeading({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <div className="flex max-w-2xl flex-col gap-3">
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-accent">{eyebrow}</p>
      <h2 id={id} className="font-serif text-2xl font-semibold leading-tight tracking-tight text-foreground sm:text-3xl">
        {title}
      </h2>
      {children ? <div className="text-base leading-relaxed text-muted-foreground">{children}</div> : null}
    </div>
  );
}

export default function LandingPage() {
  return (
    <>
      {/* Hero */}
      <section aria-labelledby="hero-title" className="relative overflow-hidden border-b border-border">
        <div className="relative mx-auto grid w-full max-w-6xl gap-10 px-4 py-12 sm:px-6 sm:py-16 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:gap-14 lg:py-20">
          <div className="flex flex-col gap-6">
            <Badge variant="outline" className="self-start rounded-md py-1 text-muted-foreground">
              Prototype · demo data is SYNTHETIC
            </Badge>
            <h1
              id="hero-title"
              className="font-serif text-[2.35rem] font-semibold leading-[1.08] tracking-tight text-foreground sm:text-5xl lg:text-[3.4rem]"
            >
              A loan file where every number shows its evidence.
            </h1>
            <p className="max-w-xl text-lg leading-relaxed text-muted-foreground">
              {PRODUCT_NAME} is one workspace for both sides of a small-business loan. Founders prepare a plan a lender can
              check. Lenders review it with each figure marked as verified, stated, assumed, estimated, conflicting or
              unknown. The software prepares and explains. People decide.
            </p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Button asChild size="lg">
                <Link href="/studio">
                  Open Founder Studio
                  <ArrowRight aria-hidden="true" />
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link href="/lender">Open Lender Desk</Link>
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">Free prototype. Sign in with a Hugging Face account.</p>
          </div>
          <SampleFileCard />
        </div>
      </section>

      {/* Two sides */}
      <section aria-labelledby="sides-title" className="mx-auto w-full max-w-6xl px-4 py-14 sm:px-6 sm:py-20">
        <SectionHeading id="sides-title" eyebrow="Two sides, one file" title="The founder prepares it. The lender reviews it.">
          <p>Both sides look at the same figures and the same evidence statuses, so nothing changes meaning between them.</p>
        </SectionHeading>
        <div className="mt-10 grid gap-5 md:grid-cols-2">
          {SIDES.map((side) => (
            <Card key={side.name} className="flex flex-col">
              <CardHeader>
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <side.icon className="size-4 text-accent" aria-hidden="true" />
                  {side.audience}
                </div>
                <CardTitle className="font-serif text-2xl">{side.name}</CardTitle>
                <CardDescription className="text-base">{side.summary}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-5">
                <ul className="flex flex-col gap-2.5">
                  {side.points.map((point) => (
                    <li key={point} className="flex gap-2.5 text-sm leading-relaxed text-foreground">
                      <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                      {point}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-xs leading-relaxed text-muted-foreground sm:max-w-sm">{side.today}</p>
                  <Button asChild variant="link" className="h-auto self-start p-0">
                    <Link href={side.href}>
                      Open {side.name}
                      <ArrowRight aria-hidden="true" />
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section aria-labelledby="how-title" className="border-y border-border bg-paper-sunken">
        <div className="mx-auto w-full max-w-6xl px-4 py-14 sm:px-6 sm:py-20">
          <SectionHeading id="how-title" eyebrow="How it works" title="Four steps, and the last one is a person." />
          <ol className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, index) => (
              <li key={step.title} className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5">
                <div className="flex items-baseline gap-3 sm:flex-col sm:gap-3">
                  <span className="figure-nums font-serif text-3xl font-semibold leading-none text-accent" aria-hidden="true">
                    {index + 1}
                  </span>
                  <h3 className="text-base font-semibold text-foreground">
                    <span className="sr-only">Step {index + 1}: </span>
                    {step.title}
                  </h3>
                </div>
                <p className="text-sm leading-relaxed text-muted-foreground">{step.body}</p>
                <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                  {step.statuses.length > 0 ? (
                    step.statuses.map((status) => <EvidenceChip key={status} status={status} />)
                  ) : (
                    <Badge variant="soft" className="rounded-[0.3rem] py-[0.3rem] text-[0.6875rem] font-semibold uppercase leading-none tracking-[0.07em]">
                      <UserCheck aria-hidden="true" />
                      Human decision
                    </Badge>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* The evidence chip */}
      <section aria-labelledby="chips-title" className="mx-auto w-full max-w-6xl px-4 py-14 sm:px-6 sm:py-20">
        <div className="grid gap-10 lg:grid-cols-[0.8fr_1.2fr] lg:gap-14">
          <SectionHeading id="chips-title" eyebrow="The evidence chip" title="Six statuses. One meaning everywhere.">
            <p>
              Every figure carries one of six statuses. Each chip has its own label and icon, so it reads the same in black
              and white and for people who do not see color.
            </p>
            <p className="mt-3">
              A number with no evidence is never filled in quietly. It stays <strong className="font-semibold text-foreground">unknown</strong>,
              and the file says so.
            </p>
          </SectionHeading>
          <EvidenceLegend />
        </div>
      </section>

      {/* Honest limits */}
      <section aria-labelledby="limits-title" className="border-t border-border bg-paper-sunken">
        <div className="mx-auto w-full max-w-6xl px-4 py-14 sm:px-6 sm:py-20">
          <SectionHeading id="limits-title" eyebrow="Honest limits" title={`What ${PRODUCT_NAME} is not.`} />
          <ul className="mt-10 grid gap-x-8 gap-y-7 sm:grid-cols-2 lg:grid-cols-3">
            {LIMITS.map((limit) => (
              <li key={limit.title} className="flex gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-card">
                  <limit.icon className="size-4 text-accent" aria-hidden="true" />
                </span>
                <div className="flex flex-col gap-1">
                  <h3 className="text-base font-semibold text-foreground">{limit.title}</h3>
                  <p className="text-sm leading-relaxed text-muted-foreground">{limit.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
