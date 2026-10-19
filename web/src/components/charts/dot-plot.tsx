import type { ReactNode } from "react";

import { tickLabel, toPercent, type Axis } from "@/lib/benchmarks/scale";
import { cn } from "@/lib/utils";

export type Mark = {
  value: number;
  /** Interval ends (a CI, or a spread the caption names). */
  lo?: number;
  hi?: number;
  /** Hollow marker: the second series, or an interval that includes the reference. */
  hollow?: boolean;
  /** Tooltip text: the mark's numbers in words. */
  title: string;
};

export type DotRow = {
  key: string;
  label: ReactNode;
  /** Second line under the label: assay count, comparator. */
  detail?: ReactNode;
  marks: Mark[];
  /** The numbers as text, right of the track: the chart's direct labels. */
  text: ReactNode;
};

export type DotPlotProps = {
  rows: DotRow[];
  axis: Axis;
  /** A vertical reference line: 0 for differences, the nominal level for coverage. */
  reference: { value: number; label: string };
  /** What the x axis measures. */
  axisLabel: string;
  className?: string;
};

/**
 * A dot-and-interval chart in HTML and CSS (rendered on the server, no canvas, no script): one
 * row per estimate, marks in ink, a reference line, and every number also written as text beside
 * its row (DESIGN.md, Charts). Several marks in one row are stacked vertically so their intervals
 * never overlap.
 */
export function DotPlot({ rows, axis, reference, axisLabel, className }: DotPlotProps) {
  const refAt = toPercent(axis, reference.value);
  return (
    <div className={cn("flex flex-col text-sm", className)}>
      <ul className="flex flex-col">
        {rows.map((row) => (
          <li
            key={row.key}
            className="grid grid-cols-1 gap-x-4 border-b py-2 last:border-b-0 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,11rem)] sm:items-center"
          >
            <div className="min-w-0">
              <div className="font-medium">{row.label}</div>
              {row.detail ? (
                <div className="text-xs text-muted-foreground">{row.detail}</div>
              ) : null}
            </div>
            <Track axis={axis} refAt={refAt} marks={row.marks} />
            <div className="font-mono text-xs tabular-nums sm:text-right">{row.text}</div>
          </li>
        ))}
      </ul>
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,11rem)]">
        <div aria-hidden className="hidden sm:block" />
        <div aria-hidden className="relative h-5 font-mono text-xs text-muted-foreground">
          <div className="absolute inset-x-4 inset-y-0">
            {axis.ticks.map((t) => (
              <span
                key={t}
                className="absolute top-1 -translate-x-1/2"
                style={{ left: `${toPercent(axis, t)}%` }}
              >
                {tickLabel(t, axis)}
              </span>
            ))}
          </div>
        </div>
        <div aria-hidden className="hidden sm:block" />
        <div className="hidden sm:block" />
        <p className="mt-1 text-xs text-muted-foreground">
          {axisLabel}. Dashed line: {reference.label}.
        </p>
      </div>
    </div>
  );
}

function Track({ axis, refAt, marks }: { axis: Axis; refAt: number; marks: Mark[] }) {
  const lanes = marks.length;
  return (
    // The inner box is inset like the tick row, so end ticks and their labels stay inside.
    <div aria-hidden className={cn("relative my-1", lanes > 1 ? "h-10" : "h-6")}>
      <div className="absolute inset-x-4 inset-y-0">
        {axis.ticks.map((t) => (
          <span
            key={t}
            className="absolute inset-y-0 w-px bg-border"
            style={{ left: `${toPercent(axis, t)}%` }}
          />
        ))}
        <span
          className="absolute inset-y-0 border-l border-dashed border-muted-foreground"
          style={{ left: `${refAt}%` }}
        />
        {marks.map((m, i) => {
          const top = `${((i + 0.5) / lanes) * 100}%`;
          const at = toPercent(axis, m.value);
          return (
            <span key={i} title={m.title}>
              {m.lo !== undefined && m.hi !== undefined ? (
                <span
                  className="absolute h-0.5 -translate-y-1/2 rounded-sm bg-foreground"
                  style={{
                    top,
                    left: `${toPercent(axis, m.lo)}%`,
                    width: `${toPercent(axis, m.hi) - toPercent(axis, m.lo)}%`,
                  }}
                />
              ) : null}
              <span
                className={cn(
                  "absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-foreground",
                  m.hollow ? "bg-background" : "bg-foreground",
                )}
                style={{ top, left: `${at}%` }}
              />
            </span>
          );
        })}
      </div>
    </div>
  );
}

/** Legend swatches for the two marker styles. */
export function MarkerKey({ hollow, children }: { hollow?: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn(
          "inline-block size-2.5 rounded-full border-2 border-foreground",
          hollow ? "bg-background" : "bg-foreground",
        )}
      />
      {children}
    </span>
  );
}

/** A chart's frame: title, assay count and how to read it, the chart, then notes. */
export function ChartFigure({
  id,
  title,
  assays,
  children,
  caption,
}: {
  id: string;
  title: string;
  /** "213 assays": every chart states it (DESIGN.md, Charts). */
  assays: string;
  children: ReactNode;
  caption: ReactNode;
}) {
  return (
    <figure
      aria-labelledby={`${id}-title`}
      className="rounded-lg border bg-card p-4 shadow-sm sm:p-6"
      data-testid={`chart-${id}`}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id={`${id}-title`} className="font-semibold tracking-tight">
          {title}
        </h3>
        <span className="font-mono text-xs text-muted-foreground" data-testid="assay-count">
          {assays}
        </span>
      </div>
      {children}
      <figcaption className="mt-4 max-w-prose text-sm text-muted-foreground">{caption}</figcaption>
    </figure>
  );
}
