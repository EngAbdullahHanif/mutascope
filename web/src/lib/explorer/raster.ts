/**
 * Pixels for the heatmap canvas (ADR 0038).
 *
 * A view is rasterised once into an image one pixel per cell (x = residue, y = amino acid).
 * The canvas then scales only the visible slice of it, with smoothing off, on every scroll or
 * zoom frame, so redrawing costs the same for 37 residues as for 3,423.
 */
import { EFFECT_MISSING_RGB, LUT_STEPS, effectLut, lutIndex } from "@/lib/color/diverging";

import { N_AA, type View } from "./landscape";

let lut: Uint8ClampedArray | null = null;

/** The effect scale's lookup table, built on first use. */
export function sharedLut(): Uint8ClampedArray {
  lut ??= effectLut(LUT_STEPS);
  return lut;
}

/**
 * RGBA bytes of a `length × 20` image: pixel `(pos, aa)` is the view's colour at that cell,
 * or the missing grey for NaN (wild type, masked, not measured).
 */
export function rasterize(view: View, length: number): Uint8ClampedArray {
  const table = sharedLut();
  const out = new Uint8ClampedArray(length * N_AA * 4);
  const [mr, mg, mb] = EFFECT_MISSING_RGB;
  for (let pos = 0; pos < length; pos++) {
    for (let aa = 0; aa < N_AA; aa++) {
      const v = view.values[pos * N_AA + aa]!;
      const o = (aa * length + pos) * 4;
      if (Number.isNaN(v)) {
        out[o] = mr;
        out[o + 1] = mg;
        out[o + 2] = mb;
      } else {
        const k = lutIndex(v, view.center, view.halfWidth) * 4;
        out[o] = table[k]!;
        out[o + 1] = table[k + 1]!;
        out[o + 2] = table[k + 2]!;
      }
      out[o + 3] = 255;
    }
  }
  return out;
}

/** Column widths in CSS pixels, smallest first. */
export const ZOOM_LEVELS = [3, 4, 6, 8, 10, 14, 18, 24] as const;

/** The widest zoom that fits the whole protein in `width`, or the narrowest one. */
export function fitZoom(length: number, width: number): number {
  let best: number = ZOOM_LEVELS[0];
  for (const z of ZOOM_LEVELS) if (length * z <= width) best = z;
  return best;
}

const TICK_STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];

/** Residues between ruler labels, so labels sit at least `minPx` apart. */
export function tickStep(cellWidth: number, minPx = 44): number {
  return TICK_STEPS.find((s) => s * cellWidth >= minPx) ?? 1000;
}

/** 0-indexed positions in `[from, to)` that get a ruler label (1-indexed multiples of step). */
export function tickPositions(from: number, to: number, step: number): number[] {
  const out: number[] = [];
  const first = Math.max(step, Math.ceil((from + 1) / step) * step);
  if (from === 0 && step > 1) out.push(0);
  for (let p = first; p <= to; p += step) out.push(p - 1);
  return out;
}
