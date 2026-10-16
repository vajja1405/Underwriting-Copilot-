/**
 * Number formatting for figures shown in the app. Every value comes from the API; these helpers only change how it
 * is written (rounding for display, units, signs). A missing value (null / undefined / NaN) is always shown as
 * "Not available", never as 0.
 */

export const NOT_AVAILABLE = "Not available";

/** True for a finite number (not null, undefined, NaN or Infinity). */
export function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

const USD_WHOLE = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const USD_CENTS = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const USD_COMPACT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** "$450,000" (whole dollars). Use `cents: true` for prices such as "$4.25". */
export function formatUsd(value: number | null | undefined, options: { cents?: boolean } = {}): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return options.cents ? USD_CENTS.format(value) : USD_WHOLE.format(value);
}

/** "$450K", "$1.2M": for chart axes and tight tiles. */
export function formatUsdCompact(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return USD_COMPACT.format(value);
}

/** A plain number with thousands separators and at most `digits` decimals: "15,409", "330.2". */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

/**
 * An estimate rounded to what it can carry: whole units from 10 up ("204", "3,965"), one decimal below 10 ("4.2").
 * For model estimates such as demand per day, where "204.2 turns/day" would claim precision the model does not have.
 */
export function formatEstimate(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return formatNumber(value, Math.abs(value) >= 10 ? 0 : 1);
}

/**
 * A share (0-1) as a percent: 0.674 -> "67.4%". `digits` decimals (default 1). Probabilities from the simulator are
 * shares of simulated paths, so one decimal shows the value as computed (1,000 paths -> steps of 0.1%).
 */
export function formatShare(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return `${(value * 100).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
}

/** A value that is already a percent: 9.25 -> "9.25%". */
export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return `${value.toLocaleString("en-US", { maximumFractionDigits: digits })}%`;
}

/** A ratio such as a DSCR: 2.27 -> "2.27x". */
export function formatRatio(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return `${value.toFixed(2)}x`;
}

/** A 0-100 score: 84.6 -> "84.6". */
export function formatScore(value: number | null | undefined): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  return value.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/** A value in its own unit, with money units written as dollars: (5410, "USD/month") -> "$5,410 / month". */
export function formatWithUnit(value: number | null | undefined, unit: string): string {
  if (!isNumber(value)) {
    return NOT_AVAILABLE;
  }
  const trimmed = unit.trim();
  if (trimmed === "USD") {
    return formatUsd(value);
  }
  if (trimmed.startsWith("USD/")) {
    return `${formatUsd(value, { cents: Math.abs(value) < 100 })} / ${trimmed.slice(4)}`;
  }
  if (trimmed.startsWith("USD per ")) {
    return `${formatUsd(value, { cents: true })} per ${trimmed.slice(8)}`;
  }
  if (trimmed.startsWith("share")) {
    return formatShare(value);
  }
  if (trimmed === "count") {
    return formatNumber(value);
  }
  const digits = Math.abs(value) >= 100 ? 0 : 2;
  return `${formatNumber(value, digits)} ${trimmed}`;
}

/** "2026-10-13T14:24:29Z" -> "Oct 13, 2026". Unreadable input is returned as it was. */
export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return NOT_AVAILABLE;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

/** "2026-10-13T14:24:29Z" -> "Oct 13, 2026, 14:24 UTC". */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return NOT_AVAILABLE;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const day = formatDate(value);
  const time = date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
  return `${day}, ${time} UTC`;
}

/** "renter_households" -> "Renter households". */
export function humanize(identifier: string): string {
  const spaced = identifier.replaceAll("_", " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2027-01" -> "Jan 2027" (a simulated calendar month). */
export function monthLabel(calendarMonth: string): string {
  const [year, month] = calendarMonth.split("-");
  const name = MONTHS[Number(month) - 1];
  return name && year ? `${name} ${year}` : calendarMonth;
}

/**
 * A low-high range with the unit written once: (2164, 12984, "USD/month") -> "$2,164 to $12,984 / month",
 * (0.1, 0.45, "share ...") -> "10.0% to 45.0%", (2, 7, "cycles per week") -> "2 to 7 cycles per week".
 */
export function formatRange(low: number | null | undefined, high: number | null | undefined, unit: string): string {
  if (!isNumber(low) || !isNumber(high)) {
    return NOT_AVAILABLE;
  }
  const trimmed = unit.trim();
  if (trimmed === "USD") {
    return `${formatUsd(low)} to ${formatUsd(high)}`;
  }
  if (trimmed.startsWith("USD/")) {
    const cents = Math.abs(high) < 100;
    return `${formatUsd(low, { cents })} to ${formatUsd(high, { cents })} / ${trimmed.slice(4)}`;
  }
  if (trimmed.startsWith("USD per ")) {
    return `${formatUsd(low, { cents: true })} to ${formatUsd(high, { cents: true })} per ${trimmed.slice(8)}`;
  }
  if (trimmed.startsWith("share")) {
    return `${formatShare(low)} to ${formatShare(high)}`;
  }
  const digits = Math.max(Math.abs(low), Math.abs(high)) >= 100 ? 0 : 2;
  const suffix = trimmed === "count" ? "" : ` ${trimmed}`;
  return `${formatNumber(low, digits)} to ${formatNumber(high, digits)}${suffix}`;
}

/** "cogs cost as a share of revenue" -> "Cogs cost as a share of revenue" (server descriptions start lowercase). */
export function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A scoring reason split into its factor (as words) and the server's explanation. */
export interface FactorReason {
  factor: string | null;
  text: string;
}

/**
 * SiteIQ writes reasons as "renter_households: 15,409 renter households within 1 mile ...". This splits off the
 * factor code so the page can show "Renter households" as a label. Text without a code prefix is returned whole.
 */
export function splitFactorReason(reason: string): FactorReason {
  const match = /^([a-z][a-z0-9_]*):\s+([\s\S]+)$/.exec(reason);
  const code = match?.[1];
  const text = match?.[2];
  if (!code || !text) {
    return { factor: null, text: reason };
  }
  return { factor: humanize(code), text };
}

/** ["a"] -> "a"; ["a", "b"] -> "a and b"; ["a", "b", "c"] -> "a, b and c". */
export function joinWords(items: string[]): string {
  if (items.length <= 1) {
    return items.join("");
  }
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
