/**
 * A live result as an Explorer {@link Landscape}, so the scorer draws it with the same canvas
 * heatmap, side panel and table as the bundle's proteins (ADR 0038, 0040).
 *
 * A live landscape has one zero-shot model and no measurements: `measured` is all NaN and
 * `source` is `"live"`, which tells the tooltip, the screen-reader text, the table and the CSV
 * not to talk about measurements. Rows a precise job has not reached yet are NaN, drawn as
 * missing (grey), never as neutral (white).
 */
import {
  AA_ORDER,
  N_AA,
  positionStatusFrom,
  type Landscape,
  type ModelOption,
} from "@/lib/explorer/landscape";

import type { Schemas } from "./client";

export const LIVE_LABEL = "Zero-shot";
export type LiveMethod = "wt_marginal" | "masked_marginal";

/** `esm2_35m_onnx_fp32` → `ESM-2 35M`: readers' name for a served model config. */
export function liveModelName(model: string): string {
  const m = /^esm2_(\d+)m(?:_|$)/.exec(model);
  return m ? `ESM-2 ${m[1]}M` : model;
}

export const METHOD_TEXT: Record<LiveMethod, string> = {
  wt_marginal: "fast mode: wild-type marginal, one forward pass",
  masked_marginal: "precise mode: masked marginal, one forward pass per residue",
};

/** `(L, 20)` grid from the API (`null` = no value) as a Float32Array with NaN. */
export function gridFrom(values: (number | null)[], length: number): Float32Array {
  if (values.length !== length * N_AA) {
    throw new Error(`expected ${length * N_AA} scores (${length} × 20), got ${values.length}`);
  }
  const out = new Float32Array(values.length);
  for (let i = 0; i < values.length; i++) out[i] = values[i] ?? Number.NaN;
  return out;
}

/** A grid with no residue scored yet: every value missing. */
export function emptyGrid(length: number): Float32Array {
  return new Float32Array(length * N_AA).fill(Number.NaN);
}

/** Copy one progress event's rows into the grid (a new array, so React sees the change). */
export function applyRows(grid: Float32Array, rows: Schemas["LandscapeRow"][]): Float32Array {
  const out = new Float32Array(grid);
  const length = grid.length / N_AA;
  for (const row of rows) {
    if (row.position < 0 || row.position >= length || row.scores.length !== N_AA) {
      throw new Error(`progress row out of range: position ${row.position}`);
    }
    out.set(row.scores, row.position * N_AA);
  }
  return out;
}

/** Residues with all 20 values: what the heatmap shows so far. */
export function scoredResidues(grid: Float32Array): number {
  let n = 0;
  for (let pos = 0; pos < grid.length / N_AA; pos++) {
    if (!Number.isNaN(grid[pos * N_AA]!)) n++;
  }
  return n;
}

export function liveLandscape(sequence: string, model: string, scores: Float32Array): Landscape {
  const length = sequence.length;
  const wt = new Int8Array(length);
  for (let i = 0; i < length; i++) wt[i] = AA_ORDER.indexOf(sequence[i]!);
  const measured = new Float32Array(length * N_AA).fill(Number.NaN);
  const option: ModelOption = {
    model,
    name: liveModelName(model),
    kind: "zero_shot",
    label: LIVE_LABEL,
    isDefault: true,
  };
  return {
    source: "live",
    assayId: "live",
    sequence,
    length,
    wt,
    measured,
    zeroShot: { [model]: scores },
    supervised: null,
    positionStatus: positionStatusFrom(measured, wt),
    dmsCenter: 0,
    dmsHalfWidth: 1,
    options: [option],
    msaRegion: null,
  };
}
