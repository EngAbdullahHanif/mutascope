"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AA_ORDER,
  extremes,
  formatValue,
  mutationCode,
  viewCsv,
  type Cell,
  type Landscape,
  type View,
  type ViewMode,
} from "@/lib/explorer/landscape";

import type { FocusCell } from "./heatmap";

/** The ten most harmful and most tolerated substitutions in view, and the view as CSV. */
export function SidePanel({
  landscape,
  view,
  mode,
  onSelect,
}: {
  landscape: Landscape;
  view: View;
  mode: ViewMode;
  onSelect: (cell: FocusCell) => void;
}) {
  const { harmful, tolerated } = extremes(view, 10);
  const download = () => {
    const blob = new Blob([viewCsv(landscape, view, mode)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = csvName(landscape.assayId, view, mode);
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <aside aria-label="Most extreme substitutions" className="flex flex-col gap-6 text-sm">
      <TopList
        title="Most harmful"
        cells={harmful}
        landscape={landscape}
        view={view}
        onSelect={onSelect}
        testId="top-harmful"
      />
      <TopList
        title="Most tolerated"
        cells={tolerated}
        landscape={landscape}
        view={view}
        onSelect={onSelect}
        testId="top-tolerated"
      />
      <div>
        <Button variant="outline" size="sm" onClick={download} data-testid="download-csv">
          <Download aria-hidden />
          Download CSV
        </Button>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {view.option.kind === "supervised" && mode === "predicted"
            ? "The substitutions shown, with their label and intervals."
            : "The substitutions shown, with their label."}
        </p>
      </div>
    </aside>
  );
}

export function csvName(assayId: string, view: View, mode: ViewMode): string {
  return `${assayId}_${mode === "measured" ? "measured" : view.option.model}.csv`;
}

function TopList({
  title,
  cells,
  landscape,
  view,
  onSelect,
  testId,
}: {
  title: string;
  cells: Cell[];
  landscape: Landscape;
  view: View;
  onSelect: (cell: FocusCell) => void;
  testId: string;
}) {
  return (
    <section>
      <h3 className="mb-1.5 font-medium">{title}</h3>
      {cells.length === 0 ? (
        <p className="text-muted-foreground">No substitutions shown.</p>
      ) : (
        <ol className="tabular font-mono text-xs" data-testid={testId}>
          {cells.map((c) => (
            <li key={`${c.position}-${c.aa}`}>
              <button
                type="button"
                className="flex w-full justify-between rounded-sm px-1.5 py-0.5 text-left hover:bg-accent"
                onClick={() => onSelect({ position: c.position, aa: c.aa })}
              >
                <span>
                  {mutationCode(landscape.sequence[c.position] ?? "?", c.position, AA_ORDER[c.aa]!)}
                </span>
                <span>{formatValue(c.value, view.decimals)}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
