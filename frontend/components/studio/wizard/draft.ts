/**
 * The plan wizard's draft: what the founder has typed so far, the checks for each step, and the request body
 * sent to POST /api/studio/projects. Kept free of React so the rules are easy to read and test.
 *
 * Money fields are kept as the strings the founder typed and parsed only when checked, so an input never jumps
 * while someone is typing. The checks use the limits the server publishes in GET /api/studio/options (sites per
 * plan, idea length, loan term, rate) and the simulator's one hard rule: the founder's cash plus the loan must cover
 * the build-out budget (or, when the budget is left empty, the business type's assumed build-out from the options:
 * the figure the simulation will use), or the plan would open with negative cash. The server checks everything
 * again.
 */

import type { MetroOption, ProjectInput, SiteInput, StudioLimits } from "@/lib/studio-types";
import { formatUsd } from "@/lib/format";

/** Used until the server's limits arrive (the same values as schemas_studio.Limits). */
export const DEFAULT_LIMITS: StudioLimits = {
  max_sites: 3,
  idea_text_max_chars: 2000,
  loan_term_months_min: 24,
  loan_term_months_max: 360,
  annual_rate_pct_max: 30,
};
/** The server's longest site label (schemas_studio.SiteIn). */
export const MAX_SITE_LABEL = 80;

export type SiteSource = "map" | "tract" | "suggested" | "address";

export interface DraftSite {
  /** Stable key for React lists. */
  key: string;
  label: string;
  lat: number | null;
  lon: number | null;
  address: string;
  source: SiteSource;
  /** The census tract the point was snapped to, when known. */
  geoid: string | null;
}

export interface Draft {
  metroId: string;
  businessTypeId: string;
  idea: string;
  budgetTotal: string;
  ownerEquity: string;
  loanAmount: string;
  termMonths: string;
  ratePct: string;
  sites: DraftSite[];
}

export const EMPTY_DRAFT: Draft = {
  metroId: "",
  businessTypeId: "",
  idea: "",
  budgetTotal: "",
  ownerEquity: "",
  loanAmount: "",
  termMonths: "120",
  ratePct: "",
  sites: [],
};

export const STEPS = [
  { id: "city", title: "City" },
  { id: "business", title: "Business type" },
  { id: "idea", title: "Your idea" },
  { id: "money", title: "Money" },
  { id: "sites", title: "Sites" },
  { id: "review", title: "Review" },
] as const;

export type StepId = (typeof STEPS)[number]["id"];

/** Field name -> message, for the fields of one step that need fixing. */
export type StepErrors = Record<string, string>;

/** "450,000" or "$450000" -> 450000; empty or unreadable -> null. */
export function parseAmount(value: string): number | null {
  const cleaned = value.replace(/[$,\s]/g, "");
  if (cleaned === "") {
    return null;
  }
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

function checkMoney(draft: Draft, limits: StudioLimits, assumedBuildOut: number | null): StepErrors {
  const errors: StepErrors = {};
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
  const minTerm = limits.loan_term_months_min;
  const maxTerm = limits.loan_term_months_max;
  if (term === null || !Number.isInteger(term) || term < minTerm || term > maxTerm) {
    errors.termMonths = `Enter a whole number of months from ${minTerm} to ${maxTerm}.`;
  }
  if (draft.ratePct.trim() !== "" && (rate === null || rate < 0 || rate > limits.annual_rate_pct_max)) {
    errors.ratePct = `Enter a yearly rate from 0 to ${limits.annual_rate_pct_max}, or leave it empty.`;
  }
  if (budget !== null && equity !== null && loan !== null && !errors.budgetTotal && equity + loan < budget) {
    errors.loanAmount =
      "Your own cash plus the loan must cover the build-out budget; otherwise the plan opens with negative cash.";
  }
  if (draft.budgetTotal.trim() === "" && assumedBuildOut !== null && equity !== null && loan !== null && equity + loan < assumedBuildOut) {
    errors.loanAmount = `With the budget left empty, the simulation assumes a build-out of ${formatUsd(assumedBuildOut)}; your own cash plus the loan must cover it. Raise them, or type your own budget.`;
  }
  return errors;
}

function checkSites(draft: Draft, limits: StudioLimits): StepErrors {
  const errors: StepErrors = {};
  if (draft.sites.length === 0) {
    errors.sites = "Add at least one candidate site.";
  }
  if (draft.sites.length > limits.max_sites) {
    errors.sites = `Add at most ${limits.max_sites} sites.`;
  }
  draft.sites.forEach((site, index) => {
    if (site.label.trim() === "") {
      errors[`site-${site.key}-label`] = `Give site ${index + 1} a short name.`;
    }
    if (site.source === "address" && site.address.trim().length < 5) {
      errors[`site-${site.key}-address`] = `Enter a street address for site ${index + 1}.`;
    }
  });
  return errors;
}

/**
 * The problems that stop the founder from leaving a step (empty when the step is complete). assumedBuildOut: the
 * chosen business type's build_out_assumed from the options (null when unknown).
 */
export function checkStep(
  step: StepId,
  draft: Draft,
  limits: StudioLimits = DEFAULT_LIMITS,
  assumedBuildOut: number | null = null,
): StepErrors {
  switch (step) {
    case "city":
      return draft.metroId ? {} : { metroId: "Pick a city." };
    case "business":
      return draft.businessTypeId ? {} : { businessTypeId: "Pick a business type." };
    case "idea":
      if (draft.idea.trim().length === 0) {
        return { idea: "Describe your idea in a sentence or two." };
      }
      return draft.idea.length > limits.idea_text_max_chars ? { idea: `Keep it under ${limits.idea_text_max_chars} characters.` } : {};
    case "money":
      return checkMoney(draft, limits, assumedBuildOut);
    case "sites":
      return checkSites(draft, limits);
    case "review":
      return {};
  }
}

/** True when sites in this metro can only be picked on its map (the SYNTHETIC demo city has no addresses). */
export function isMapOnlyMetro(metro: MetroOption | undefined): boolean {
  return metro?.site_input === "map";
}

function toSiteInput(site: DraftSite): SiteInput {
  if (site.source === "address") {
    return { label: site.label.trim(), address: site.address.trim() };
  }
  return { label: site.label.trim(), lat: site.lat, lon: site.lon };
}

/** A label the server accepts (1-80 characters). */
export function siteLabel(text: string): string {
  return text.length <= MAX_SITE_LABEL ? text : `${text.slice(0, MAX_SITE_LABEL - 1)}…`;
}

/** The POST /api/studio/projects body. Call only after every step passed checkStep. */
export function toProjectInput(draft: Draft): ProjectInput {
  const rate = parseAmount(draft.ratePct);
  return {
    metro_id: draft.metroId,
    business_type_id: draft.businessTypeId,
    idea_text: draft.idea.trim(),
    budget_total: draft.budgetTotal.trim() === "" ? null : parseAmount(draft.budgetTotal),
    owner_equity: parseAmount(draft.ownerEquity) ?? 0,
    loan_amount: parseAmount(draft.loanAmount) ?? 0,
    loan_term_months: parseAmount(draft.termMonths) ?? 0,
    annual_rate_pct: draft.ratePct.trim() === "" ? null : rate,
    sites: draft.sites.map(toSiteInput),
  };
}

let siteCounter = 0;

/** A new site for the draft (numbered labels: "Site 1", "Site 2", ...). */
export function newSite(partial: Partial<DraftSite> & { source: SiteSource }, existing: DraftSite[]): DraftSite {
  siteCounter += 1;
  return {
    key: `site-${Date.now()}-${siteCounter}`,
    lat: null,
    lon: null,
    address: "",
    geoid: null,
    ...partial,
    label: siteLabel(partial.label ?? `Site ${existing.length + 1}`),
  };
}
