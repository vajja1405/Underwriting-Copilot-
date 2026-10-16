import type { EvidenceStatus } from "@/lib/evidence";

/**
 * SYNTHETIC example loan file shown on the landing page to explain evidence chips.
 *
 * Every value here is made up for illustration. It describes no real business, person, place or loan, and it is
 * not the output of any model in this repository. The page labels it SYNTHETIC wherever it is shown.
 * The one derived value (12 months of deposits) is computed below from the other synthetic inputs, so the
 * "conflicting" example is internally consistent.
 */

export interface SampleFigure {
  id: string;
  label: string;
  value: string;
  status: EvidenceStatus;
  /** Short line under the value. */
  note: string;
  /** What the evidence drawer shows for this figure. */
  source: string;
}

export const SAMPLE_FILE_TITLE = "Neighborhood café, first location";
export const SAMPLE_LOAN_REQUEST = "Loan request: $150,000 equipment and build-out";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const AVERAGE_MONTHLY_DEPOSITS = 41_850;
const STATED_ANNUAL_REVENUE = 520_000;
const TWELVE_MONTH_DEPOSITS = AVERAGE_MONTHLY_DEPOSITS * 12;

export const SAMPLE_FIGURES: readonly SampleFigure[] = [
  {
    id: "monthly-deposits",
    label: "Average monthly deposits",
    value: usd.format(AVERAGE_MONTHLY_DEPOSITS),
    status: "VERIFIED",
    note: "From 12 bank statements",
    source: "Bank statements, January to December, page 2 of each (SYNTHETIC documents).",
  },
  {
    id: "owner-equity",
    label: "Owner cash put in",
    value: usd.format(60_000),
    status: "USER_STATED",
    note: "Typed in by the founder",
    source: "Entered in the application form. No bank statement or transfer record has been uploaded for it yet.",
  },
  {
    id: "food-cost",
    label: "Food and supply cost",
    value: "31% of sales",
    status: "ASSUMPTION",
    note: "Planning range 28% to 34%, editable",
    source: "A planning assumption chosen by the founder inside a stated range. It is not a measured value.",
  },
  {
    id: "first-year-revenue",
    label: "First-year revenue, middle case",
    value: usd.format(486_000),
    status: "MODEL_ESTIMATE",
    note: "Example of a simulated value (typed for this page)",
    source:
      "Typed for this page to show how a simulated value is labeled. In a real file this figure would come from a simulation over the stated assumptions (simulated, based on stated assumptions): a possible range, not a prediction.",
  },
  {
    id: "annual-revenue",
    label: "Annual revenue",
    value: `${usd.format(STATED_ANNUAL_REVENUE)} vs ${usd.format(TWELVE_MONTH_DEPOSITS)}`,
    status: "CONFLICTING",
    note: "Application and deposits disagree",
    source: `The application states ${usd.format(STATED_ANNUAL_REVENUE)}; 12 months of deposits add up to ${usd.format(TWELVE_MONTH_DEPOSITS)}. A reviewer decides which one to rely on, or asks for more evidence.`,
  },
  {
    id: "foot-traffic",
    label: "Weekday foot traffic at the site",
    value: "Not known",
    status: "UNKNOWN",
    note: "No public count for this block",
    source: "No data source covers this block. The value stays unknown instead of being guessed.",
  },
];
