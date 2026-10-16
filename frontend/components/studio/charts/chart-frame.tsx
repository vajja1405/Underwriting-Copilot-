"use client";

import { Table2 } from "lucide-react";
import type { ReactNode } from "react";

import { SimulationLabel } from "@/components/studio/simulation-label";
import { cn } from "@/lib/utils";

/**
 * The frame every chart sits in: a <figure> with a title, the "simulated, based on stated assumptions" label, a
 * legend (line keys for lines, swatches for areas and bars), the chart, and a "Show the numbers" table view so no
 * value is only reachable through a tooltip (dataviz rule: tooltips enhance, they never gate).
 */

export interface LegendItem {
  label: string;
  /** CSS color of the mark. */
  color: string;
  kind: "line" | "area" | "bar" | "rule";
}

function LegendKey({ item }: { item: LegendItem }) {
  if (item.kind === "line" || item.kind === "rule") {
    return (
      <span
        className="inline-block h-0.5 w-4 rounded-full"
        style={{ backgroundColor: item.color, height: item.kind === "rule" ? 1 : 2 }}
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className="inline-block size-3 rounded-[3px]"
      style={{ backgroundColor: item.color, opacity: item.kind === "area" ? 0.35 : 1 }}
      aria-hidden="true"
    />
  );
}

export function ChartFrame({
  title,
  description,
  legend,
  table,
  label,
  children,
  className,
}: {
  title: string;
  description?: ReactNode;
  legend: LegendItem[];
  /** The same numbers as a table (shown on request). */
  table: ReactNode;
  /** The simulation label from the API (falls back to the standard wording). */
  label?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <figure className={cn("flex min-w-0 flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:p-5", className)}>
      <figcaption className="flex flex-col gap-1">
        <span className="font-semibold text-foreground">{title}</span>
        {description ? <span className="text-sm leading-relaxed text-muted-foreground">{description}</span> : null}
        <SimulationLabel text={label} />
      </figcaption>
      <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-foreground" aria-label="Legend">
        {legend.map((item) => (
          <li key={item.label} className="flex items-center gap-1.5">
            <LegendKey item={item} />
            {item.label}
          </li>
        ))}
      </ul>
      <div className="min-w-0">{children}</div>
      <details className="group min-w-0 rounded-md border border-border">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium text-accent hover:bg-muted [&::-webkit-details-marker]:hidden">
          <Table2 className="size-4" aria-hidden="true" />
          <span className="group-open:hidden">Show the numbers as a table</span>
          <span className="hidden group-open:inline">Hide the table</span>
        </summary>
        {/* The table scrolls inside this box, so it must be reachable and scrollable from the keyboard
            (axe scrollable-region-focusable): a focusable, named region. */}
        <div
          role="region"
          aria-label={`${title}: the numbers as a table`}
          tabIndex={0}
          className="max-h-80 overflow-auto border-t border-border outline-none focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
        >
          {table}
        </div>
      </details>
    </figure>
  );
}

/** A compact table for chart twins: sticky header, tabular numerals, right-aligned numbers. */
export function DataTable({ caption, columns, rows }: { caption: string; columns: string[]; rows: (string | number)[][] }) {
  return (
    <table className="figure-nums w-full border-collapse text-left text-xs">
      <caption className="sr-only">{caption}</caption>
      <thead className="sticky top-0 bg-paper-sunken">
        <tr>
          {columns.map((column, index) => (
            <th key={column} scope="col" className={cn("px-3 py-2 font-semibold", index > 0 && "text-right")}>
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, rowIndex) => (
          <tr key={rowIndex} className="border-t border-border">
            {row.map((cell, index) =>
              index === 0 ? (
                <th key={index} scope="row" className="px-3 py-1.5 font-medium">
                  {cell}
                </th>
              ) : (
                <td key={index} className="px-3 py-1.5 text-right">
                  {cell}
                </td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
