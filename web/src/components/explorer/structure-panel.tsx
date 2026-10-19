"use client";

import { Info, LoaderCircle } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";

import { EffectLegend } from "@/components/charts/effect-legend";
import type { ProteinFile } from "@/lib/bundle/schemas.generated";
import { PLDDT_MIN, PLDDT_STOPS } from "@/lib/color/sequential";
import type { View } from "@/lib/explorer/landscape";
import type { ColourBy } from "@/lib/explorer/structure";

// The viewer and 3Dmol.js (~540 kB) are their own chunks, fetched only once this renders, which
// the page does after the heatmap's first frame (ADR 0039).
const StructureViewer = dynamic(() => import("./structure-viewer"), {
  ssr: false,
  loading: () => (
    <p
      className="flex h-80 items-center justify-center gap-2 rounded-md border text-sm text-muted-foreground sm:h-96"
      role="status"
    >
      <LoaderCircle aria-hidden className="size-4 animate-spin" />
      Loading the 3D viewer…
    </p>
  ),
});

export type StructurePanelProps = {
  protein: ProteinFile;
  view: View;
  means: Float32Array;
  highlight: number | null;
  onHover: (position: number | null) => void;
  onSelect: (position: number) => void;
};

/** The structure section of a protein page: the 3D view and its legend, or why there is none. */
export function StructurePanel(props: StructurePanelProps) {
  const { protein, view } = props;
  const [colourBy, setColourBy] = useState<ColourBy>("effect");
  const ref = protein.structure_file;

  return (
    <section aria-labelledby="structure-heading" className="flex flex-col gap-3">
      <h2 id="structure-heading" className="text-xl font-semibold tracking-tight">
        Structure
      </h2>
      {ref === null ? (
        <p
          className="flex max-w-prose gap-2 rounded-md border bg-muted/50 p-3 text-sm"
          data-testid="no-structure"
        >
          <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <span>
            No 3D structure for this assay. ProteinGym&apos;s AF2 model of this protein does not
            match the assay&apos;s target sequence, so it is not shown (and SaProt, which needs the
            structure, has no landscape here). The heatmap and the table hold every value.
          </span>
        </p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <StructureViewer {...props} colourBy={colourBy} />
          <div className="flex flex-col gap-4 text-sm">
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-muted-foreground">Colour residues by</legend>
              <div className="flex h-8 w-fit overflow-hidden rounded-md border">
                {(
                  [
                    ["effect", "Mean effect"],
                    ["plddt", "pLDDT"],
                  ] as const
                ).map(([value, label]) => (
                  <label
                    key={value}
                    className="flex cursor-pointer items-center px-3 has-checked:bg-accent has-checked:font-medium has-focus-visible:ring-2 has-focus-visible:ring-ring"
                  >
                    <input
                      type="radio"
                      name="structure-colour"
                      value={value}
                      checked={colourBy === value}
                      onChange={() => setColourBy(value)}
                      className="sr-only"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            {colourBy === "effect" ? (
              <>
                <span
                  className="w-fit rounded-sm border px-2 py-0.5 font-medium"
                  data-testid="structure-label"
                >
                  {view.label}
                </span>
                <EffectLegend
                  maxAbs={view.halfWidth}
                  center={view.center}
                  title={`${view.title}: mean per residue`}
                  decimals={view.decimals}
                  clamped
                  showMissing
                  ends={view.units === "dms" ? ["Less fit", "Assay median", "Fitter"] : undefined}
                />
              </>
            ) : (
              <PlddtLegend threshold={ref.low_plddt_threshold} />
            )}
            <p className="text-xs text-muted-foreground">
              ProteinGym&apos;s AF2 model, backbone only. Thin trace: pLDDT below{" "}
              {ref.low_plddt_threshold} (low confidence). Hover a residue to find its column in the
              heatmap; click it to select that column.
            </p>
            <p
              className="flex gap-2 text-xs text-muted-foreground"
              data-testid="structure-a11y-note"
            >
              <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              The 3D view cannot be used with a keyboard or a screen reader. The heatmap (arrow
              keys) and the table show the same values and are the accessible route.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

const PLDDT_GRADIENT = `linear-gradient(in oklab to right, ${PLDDT_STOPS.join(", ")})`;

/** pLDDT 40–100 on the Purples ramp, with the low-confidence threshold marked. */
function PlddtLegend({ threshold }: { threshold: number }) {
  const at = ((threshold - PLDDT_MIN) / (100 - PLDDT_MIN)) * 100;
  return (
    <figure className="w-full max-w-sm text-xs">
      <figcaption className="mb-1.5 font-medium text-foreground">pLDDT (AF2 confidence)</figcaption>
      <div className="relative">
        <div
          role="img"
          aria-label={`Colour scale for pLDDT: light is low confidence (${PLDDT_MIN} or less), dark is high (100); below ${threshold} is drawn as a thin trace.`}
          className="h-3 rounded-sm border"
          style={{ backgroundImage: PLDDT_GRADIENT }}
        />
        <span
          aria-hidden
          className="absolute -top-0.5 h-4 w-0.5 bg-foreground"
          style={{ left: `${at}%` }}
        />
      </div>
      <div aria-hidden className="tabular relative mt-1 h-4 font-mono text-muted-foreground">
        <span className="absolute left-0">≤ {PLDDT_MIN}</span>
        <span className="absolute -translate-x-1/2" style={{ left: `${at}%` }}>
          {threshold}
        </span>
        <span className="absolute right-0">100</span>
      </div>
      <div aria-hidden className="flex justify-between text-muted-foreground">
        <span>Low</span>
        <span>High</span>
      </div>
    </figure>
  );
}
