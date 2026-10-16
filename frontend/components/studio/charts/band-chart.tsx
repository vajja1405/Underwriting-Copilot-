"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";

import { formatUsd, formatUsdCompact, monthLabel } from "@/lib/format";

/**
 * A P10-P90 band with the P50 line over the simulated months: the revenue fan and the cash runway charts.
 *
 * One series, so one hue (--viz-1): the band is a ~12% wash, the median a 2px line. The crosshair snaps to the
 * month and the tooltip lists P90, P50 and P10 there, values first. A zero line is drawn when the band crosses zero
 * (cash below zero is what the runway chart is about). Loaded with next/dynamic so Recharts stays out of other pages.
 */

export interface BandPoint {
  /** 1 = the opening month. */
  month: number;
  /** "2027-01" */
  calendarMonth: string;
  p10: number;
  p50: number;
  p90: number;
}

interface Row extends BandPoint {
  band: [number, number];
}

function BandTooltip({ active, payload }: TooltipContentProps) {
  const row = payload?.[0]?.payload as Row | undefined;
  if (!active || !row) {
    return null;
  }
  return (
    <div className="figure-nums rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-semibold text-foreground">
        Month {row.month} · {monthLabel(row.calendarMonth)}
      </p>
      <dl className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5">
        <dt className="text-muted-foreground">P90 (high)</dt>
        <dd className="text-right font-semibold text-foreground">{formatUsd(row.p90)}</dd>
        <dt className="flex items-center gap-1.5 text-muted-foreground">
          <span className="inline-block h-0.5 w-3 bg-viz-1" aria-hidden="true" />
          P50 (median)
        </dt>
        <dd className="text-right font-semibold text-foreground">{formatUsd(row.p50)}</dd>
        <dt className="text-muted-foreground">P10 (low)</dt>
        <dd className="text-right font-semibold text-foreground">{formatUsd(row.p10)}</dd>
      </dl>
    </div>
  );
}

export default function BandChart({ data, height = 260, zeroLine = false }: { data: BandPoint[]; height?: number; zeroLine?: boolean }) {
  const rows: Row[] = data.map((point) => ({ ...point, band: [point.p10, point.p90] }));
  const crossesZero = rows.some((row) => row.p10 < 0) && rows.some((row) => row.p90 > 0);
  const tickEvery = rows.length > 24 ? 6 : 3;

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 18, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--viz-grid)" strokeWidth={1} vertical={false} />
          <XAxis
            dataKey="month"
            tickLine={false}
            axisLine={{ stroke: "var(--viz-axis)" }}
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            interval={0}
            ticks={rows.filter((row) => row.month === 1 || row.month % tickEvery === 0).map((row) => row.month)}
            tickFormatter={(month: number) => `M${month}`}
          />
          <YAxis
            width={56}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
            tickFormatter={(value: number) => formatUsdCompact(value)}
          />
          {zeroLine || crossesZero ? (
            <ReferenceLine y={0} stroke="var(--ink-muted)" strokeWidth={1} />
          ) : null}
          <Tooltip content={BandTooltip} cursor={{ stroke: "var(--ink-muted)", strokeWidth: 1 }} isAnimationActive={false} />
          <Area
            type="monotone"
            dataKey="band"
            stroke="none"
            fill="var(--viz-1)"
            fillOpacity={0.14}
            isAnimationActive={false}
            activeDot={false}
            name="P10 to P90"
          />
          <Line
            type="monotone"
            dataKey="p50"
            stroke="var(--viz-1)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, stroke: "var(--paper-raised)", strokeWidth: 2, fill: "var(--viz-1)" }}
            isAnimationActive={false}
            name="P50"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
