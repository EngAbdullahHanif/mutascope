/**
 * The pLDDT colour scale (DESIGN.md, "Structure"; ADR 0039).
 *
 * AF2's per-residue confidence, 0–100, on a sequential ColorBrewer Purples ramp: light = low
 * confidence, dark = high. Purple is on neither pole of the effect scale (blue, red) nor the
 * accent (teal), so a structure coloured by pLDDT cannot be read as effects. Interpolated in
 * OKLab like the effect scale; the stops are the `--color-plddt-*` tokens (a test keeps them
 * equal).
 */
import { hexToOklab, oklabToHex } from "./diverging";

/** Stops from {@link PLDDT_MIN} to 100, evenly spaced. */
export const PLDDT_STOPS = [
  "#dadaeb",
  "#bcbddc",
  "#9e9ac8",
  "#807dba",
  "#6a51a3",
  "#54278f",
  "#3f007d",
] as const;

/** pLDDT at the lightest stop; lower values share it (AF2 rarely goes below). */
export const PLDDT_MIN = 40;

const STOPS_LAB = PLDDT_STOPS.map(hexToOklab);

/** Colour of a pLDDT value (clamped to {@link PLDDT_MIN}–100). */
export function plddtColor(plddt: number): string {
  const x = Math.min(1, Math.max(0, (plddt - PLDDT_MIN) / (100 - PLDDT_MIN)));
  const segments = STOPS_LAB.length - 1;
  const i = Math.min(segments - 1, Math.floor(x * segments));
  const f = x * segments - i;
  const a = STOPS_LAB[i]!;
  const b = STOPS_LAB[i + 1]!;
  return oklabToHex([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]);
}
