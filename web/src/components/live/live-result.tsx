"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { EffectLegend } from "@/components/charts/effect-legend";
import { Heatmap, type FocusCell } from "@/components/explorer/heatmap";
import { LandscapeTable } from "@/components/explorer/landscape-table";
import { SidePanel } from "@/components/explorer/side-panel";
import { Button } from "@/components/ui/button";
import { buildView, formatCount, positionMeans } from "@/lib/explorer/landscape";
import { LIVE_LABEL, METHOD_TEXT, liveLandscape, type LiveMethod } from "@/lib/live/landscape";
import { EXAMPLES } from "@/lib/live/sequence";

export type LiveResultData = {
  sequence: string;
  /** Served model config (`esm2_35m_onnx_fp32`). */
  model: string;
  method: LiveMethod;
  /** `(L, 20)`, NaN where a precise job has not scored the residue yet. */
  grid: Float32Array;
  /** Residues scored and to score (equal in fast mode). */
  done: number;
  total: number;
  /** The whole landscape has arrived. */
  complete: boolean;
};

/**
 * A live landscape, drawn with the Explorer's heatmap. Zero-shot only (no 3D, no supervised
 * model: neither exists for an arbitrary sequence), labelled with the model that scored it.
 */
export function LiveResult({ data }: { data: LiveResultData }) {
  const landscape = useMemo(
    () => liveLandscape(data.sequence, data.model, data.grid),
    [data.sequence, data.model, data.grid],
  );
  const view = useMemo(
    () => buildView(landscape, { model: data.model, mode: "predicted", maskUnmeasured: false }),
    [landscape, data.model],
  );
  const means = useMemo(() => positionMeans(view, landscape.length), [view, landscape.length]);
  const [focus, setFocus] = useState<FocusCell | null>(null);
  const [showTable, setShowTable] = useState(false);
  const label = `${LIVE_LABEL}, ${view.option.name}`;
  const example = EXAMPLES.find((e) => e.sequence === data.sequence);

  return (
    <section
      aria-label="Result"
      className="flex flex-col gap-4"
      data-testid="live-result"
      data-method={data.method}
      data-done={data.done}
      data-total={data.total}
      data-complete={data.complete}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded-sm border px-2 py-0.5 font-medium" data-testid="live-label">
          {label}
        </span>
        <span className="text-muted-foreground">
          {formatCount(landscape.length)} residues; {METHOD_TEXT[data.method]}. Never trained on a
          measurement; a functional-effect estimate, not a clinical prediction.
        </span>
      </div>
      {example ? (
        <p className="text-sm text-muted-foreground">
          This is the target of a ProteinGym assay:{" "}
          <Link href={`/explore/${example.assayId}`} className="text-primary hover:underline">
            compare with its measurements in the Explorer
          </Link>
          .
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_14rem]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <EffectLegend
              maxAbs={view.halfWidth}
              center={view.center}
              title={view.title}
              decimals={view.decimals}
              clamped
              showMissing={!data.complete}
              missingLabel="Not scored yet"
              showWildType
            />
            <p className="max-w-xs text-xs text-muted-foreground">
              White is the wild type&apos;s own score (0).
              {data.complete
                ? " Bars: mean over each residue's substitutions."
                : " Grey columns are not scored yet; the scale settles when every residue is in."}
            </p>
          </div>
          <Heatmap
            landscape={landscape}
            view={view}
            means={means}
            focus={focus}
            onFocusChange={setFocus}
          />
        </div>
        {data.complete ? (
          <SidePanel landscape={landscape} view={view} mode="predicted" onSelect={setFocus} />
        ) : null}
      </div>

      {data.complete ? (
        <div className="flex flex-col gap-2">
          <div>
            <Button
              variant="outline"
              size="sm"
              aria-expanded={showTable}
              aria-controls="live-table"
              onClick={() => setShowTable(!showTable)}
            >
              {showTable ? "Hide" : "Show"} as a table
            </Button>
          </div>
          {showTable ? (
            <div id="live-table">
              <LandscapeTable landscape={landscape} view={view} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
