"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Rectangle,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
  type TooltipContentProps,
} from "recharts";

import { formatUsd, formatUsdCompact, formatWithUnit, sentenceCase } from "@/lib/format";
import type { TornadoBar } from "@/lib/studio-types";

/**
 * The tornado chart: which inputs move year-2 net income most. For each input the simulator set it to its P10 and
 * then its P90 with every other draw held fixed; each bar runs from the base case (the zero line) to the result.
 *
 * Two marks per row, overlaid on the same band (a negative barGap): orange (--viz-2) = input at its P10 (low end),
 * blue (--viz-1) = input at its P90 (high end). Both colors passed the dataviz palette validator in both themes,
 * and the legend names them, so color is not the only key. Rows are ranked by swing, largest first (the server's
 * order). Loaded with next/dynamic.
 */

interface Row {
  label: string;
  bar: TornadoBar;
  low: [number, number];
  high: [number, number];
}

const BAR_SIZE = 14;
const END_RADIUS = 4;

/** A round step for the axis: 1, 2 or 5 times a power of ten, at least `raw`. */
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 5, 10]) {
    if (factor * power >= raw) {
      return factor * power;
    }
  }
  return 10 * power;
}

/**
 * A bar that is square at the zero line and rounded at its data end (dataviz mark spec): bars growing left are
 * rounded on the left, bars growing right on the right.
 */
function barShape(key: "low" | "high") {
  function Shape(props: BarShapeProps) {
    const row = props.payload as Row | undefined;
    const range = row?.[key] ?? [0, 0];
    const growsLeft = range[0] < 0;
    const radius: [number, number, number, number] = growsLeft ? [END_RADIUS, 0, 0, END_RADIUS] : [0, END_RADIUS, END_RADIUS, 0];
    return <Rectangle {...props} radius={radius} />;
  }
  return Shape;
}

const LOW_SHAPE = barShape("low");
const HIGH_SHAPE = barShape("high");

/** A short y-axis label, cut at a word; the full description is in the tooltip and the table. */
function shortLabel(text: string, max: number): string {
  if (text.length <= max) {
    return text;
  }
  const cut = text.slice(0, max - 1);
  const lastSpace = cut.lastIndexOf(" ");
  const words = lastSpace > max / 2 ? cut.slice(0, lastSpace) : cut;
  return `${words.trimEnd()}…`;
}

/** One y-axis label on a single line (Recharts' default tick wraps long text onto two lines). */
function AxisLabel({ x, y, payload }: { x?: number | string; y?: number | string; payload?: { value?: unknown } }) {
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fill="var(--ink)" fontSize={11}>
      {String(payload?.value ?? "")}
    </text>
  );
}

function signedUsd(value: number): string {
  const formatted = formatUsdCompact(Math.abs(value));
  if (value > 0) {
    return `+${formatted}`;
  }
  if (value < 0) {
    return `-${formatted}`;
  }
  return formatted;
}

function TornadoTooltip({ active, payload }: TooltipContentProps) {
  const row = payload?.[0]?.payload as Row | undefined;
  if (!active || !row) {
    return null;
  }
  const { bar } = row;
  return (
    <div className="figure-nums max-w-72 rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-semibold text-foreground">{bar.description}</p>
      <p className="flex items-center gap-1.5 text-muted-foreground">
        <span className="inline-block size-2.5 rounded-[2px] bg-viz-2" aria-hidden="true" />
        At P10 ({formatWithUnit(bar.input_p10, bar.unit)}):{" "}
        <strong className="text-foreground">{formatUsd(bar.metric_at_p10)}</strong>
      </p>
      <p className="flex items-center gap-1.5 text-muted-foreground">
        <span className="inline-block size-2.5 rounded-[2px] bg-viz-1" aria-hidden="true" />
        At P90 ({formatWithUnit(bar.input_p90, bar.unit)}):{" "}
        <strong className="text-foreground">{formatUsd(bar.metric_at_p90)}</strong>
      </p>
      <p className="mt-1 text-muted-foreground">Swing: {formatUsd(bar.swing)}</p>
    </div>
  );
}

export default function TornadoChart({ bars, baseMetric, compact = false }: { bars: TornadoBar[]; baseMetric: number; compact?: boolean }) {
  const rows: Row[] = bars.map((bar) => {
    const low = bar.metric_at_p10 - baseMetric;
    const high = bar.metric_at_p90 - baseMetric;
    return {
      label: shortLabel(sentenceCase(bar.description), compact ? 20 : 34),
      bar,
      low: [Math.min(0, low), Math.max(0, low)],
      high: [Math.min(0, high), Math.max(0, high)],
    };
  });
  const largest = Math.max(1, ...rows.flatMap((row) => [Math.abs(row.low[0]), row.low[1], Math.abs(row.high[0]), row.high[1]]));
  // Symmetric axis around the base case, with round ticks: -2s, -s, 0, s, 2s.
  const step = niceStep(largest / 2);
  const extent = 2 * step;
  // On a phone the plot is narrow, so the in-between ticks are left out (their labels would touch).
  const ticks = compact ? [-2 * step, 0, 2 * step] : [-2 * step, -step, 0, step, 2 * step];
  const height = rows.length * (BAR_SIZE + 14) + 40;

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }} barGap={-BAR_SIZE} barCategoryGap={14}>
          <CartesianGrid stroke="var(--viz-grid)" strokeWidth={1} horizontal={false} />
          <XAxis
            type="number"
            domain={[-extent, extent]}
            tickLine={false}
            axisLine={{ stroke: "var(--viz-axis)" }}
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickFormatter={(value: number) => signedUsd(value)}
            ticks={ticks}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={compact ? 128 : 230}
            tickLine={false}
            axisLine={false}
            tick={AxisLabel}
            tickMargin={8}
            interval={0}
          />
          <ReferenceLine x={0} stroke="var(--ink-muted)" strokeWidth={1} />
          <Tooltip content={TornadoTooltip} cursor={{ fill: "var(--paper-sunken)" }} isAnimationActive={false} />
          <Bar dataKey="low" barSize={BAR_SIZE} fill="var(--viz-2)" shape={LOW_SHAPE} isAnimationActive={false} name="Input at its P10" />
          <Bar dataKey="high" barSize={BAR_SIZE} fill="var(--viz-1)" shape={HIGH_SHAPE} isAnimationActive={false} name="Input at its P90" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
