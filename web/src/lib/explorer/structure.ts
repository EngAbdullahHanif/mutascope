/**
 * From a validated structure file to what the 3D view draws (ADR 0039).
 *
 * A structure file holds the backbone (N, CA, C, O) of one AF2 model and its pLDDT; the protein
 * file says which target residues it models (`start..end`, 0-indexed). Structure residue `i` is
 * target position `start + i`. The viewer reads PDB text (3Dmol.js assigns bonds and secondary
 * structure from the backbone), so {@link toPdb} writes it, with the file's own residue numbers
 * and pLDDT in the B-factor column. Linking with the heatmap goes through target positions only:
 * {@link positionOfResi} and {@link resiOfPosition} are the one place they meet residue numbers.
 */
import type { StructureFile, StructureRef } from "@/lib/bundle/schemas.generated";
import { EFFECT_MISSING, lutIndex } from "@/lib/color/diverging";
import { plddtColor } from "@/lib/color/sequential";

import { dequantize, type View } from "./landscape";
import { sharedLut } from "./raster";

const THREE: Record<string, string> = {
  A: "ALA", C: "CYS", D: "ASP", E: "GLU", F: "PHE", G: "GLY", H: "HIS", I: "ILE", K: "LYS",
  L: "LEU", M: "MET", N: "ASN", P: "PRO", Q: "GLN", R: "ARG", S: "SER", T: "THR", V: "VAL",
  W: "TRP", Y: "TYR",
}; // prettier-ignore

export type Structure = {
  /** 0-indexed target position of structure residue 0. */
  start: number;
  /** 0-indexed, exclusive. */
  end: number;
  /** Target length: residues outside `start..end` have no 3D position. */
  targetLength: number;
  residueNumbers: number[];
  sequence: string;
  /** `(n, 4, 3)` flattened, Å. */
  coordinates: Float32Array;
  plddt: Float32Array;
  lowPlddtThreshold: number;
  /** Per structure residue: pLDDT below the threshold. */
  lowConfidence: boolean[];
};

export function decodeStructure(
  file: StructureFile,
  ref: StructureRef,
  targetLength: number,
): Structure {
  const plddt = dequantize(file.plddt);
  return {
    start: ref.start,
    end: ref.end,
    targetLength,
    residueNumbers: file.residue_numbers,
    sequence: file.sequence,
    coordinates: dequantize(file.coordinates),
    plddt,
    lowPlddtThreshold: ref.low_plddt_threshold,
    lowConfidence: Array.from(plddt, (v) => v < ref.low_plddt_threshold),
  };
}

export function isPartial(s: Structure): boolean {
  return s.start > 0 || s.end < s.targetLength;
}

/** Structure residue index of a target position, or -1 outside the structure. */
export function indexOfPosition(s: Structure, position: number): number {
  return position >= s.start && position < s.end ? position - s.start : -1;
}

/** The PDB residue number the viewer uses for a target position, or null outside it. */
export function resiOfPosition(s: Structure, position: number): number | null {
  const i = indexOfPosition(s, position);
  return i < 0 ? null : s.residueNumbers[i]!;
}

/** The target position of a PDB residue number, or null if the structure has no such residue. */
export function positionOfResi(s: Structure, resi: number): number | null {
  // Residue numbers are strictly increasing (the bundle checks it), so a binary search.
  let lo = 0;
  let hi = s.residueNumbers.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const n = s.residueNumbers[mid]!;
    if (n === resi) return s.start + mid;
    if (n < resi) lo = mid + 1;
    else hi = mid - 1;
  }
  return null;
}

/** CA coordinates of structure residue `i`. */
export function caOf(s: Structure, i: number): { x: number; y: number; z: number } {
  const o = (i * 4 + 1) * 3;
  return { x: s.coordinates[o]!, y: s.coordinates[o + 1]!, z: s.coordinates[o + 2]! };
}

const ATOMS = ["N", "CA", "C", "O"] as const;

/** Fixed-column PDB `ATOM` records, `TER` and `END`: what 3Dmol.js parses. */
export function toPdb(s: Structure, chain = "A"): string {
  const lines: string[] = [];
  let serial = 1;
  for (let i = 0; i < s.sequence.length; i++) {
    const resname = THREE[s.sequence[i]!]!;
    const resi = s.residueNumbers[i]!;
    const b = s.plddt[i]!.toFixed(2).padStart(6);
    for (let k = 0; k < 4; k++) {
      const name = ATOMS[k]!;
      const o = (i * 4 + k) * 3;
      const xyz = [0, 1, 2].map((d) => s.coordinates[o + d]!.toFixed(3).padStart(8)).join("");
      lines.push(
        `ATOM  ${String(serial).padStart(5)}  ${name.padEnd(3)} ${resname} ${chain}` +
          `${String(resi).padStart(4)}    ${xyz}  1.00${b}           ${name[0]}  `,
      );
      serial++;
    }
  }
  return `${lines.join("\n")}\nTER\nEND\n`;
}

const hex = (r: number, g: number, b: number) =>
  `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;

/** The colour the heatmap's summary bar gives a mean: the same lookup table and clipping. */
export function meanColour(mean: number, view: View): string {
  if (Number.isNaN(mean)) return EFFECT_MISSING;
  const table = sharedLut();
  const k = lutIndex(mean, view.center, view.halfWidth) * 4;
  return hex(table[k]!, table[k + 1]!, table[k + 2]!);
}

/**
 * Colour of each structure residue by the heatmap's per-residue mean (its summary bar): the same
 * centre, half-width and clipping as the view, grey where the mean has no value.
 */
export function effectColours(s: Structure, means: Float32Array, view: View): string[] {
  return Array.from({ length: s.sequence.length }, (_, i) => meanColour(means[s.start + i]!, view));
}

export function plddtColours(s: Structure): string[] {
  return Array.from(s.plddt, plddtColor);
}

export type ColourBy = "effect" | "plddt";

/**
 * One colour per target residue, for the coverage strip: the structure's colour inside
 * `start..end`, the missing grey outside it (no 3D position), never white.
 */
export function coverageColours(s: Structure, residueColours: string[]): string[] {
  return Array.from({ length: s.targetLength }, (_, p) => {
    const i = indexOfPosition(s, p);
    return i < 0 ? EFFECT_MISSING : residueColours[i]!;
  });
}

/** Contiguous runs of low-confidence residues as 1-indexed target ranges, e.g. "1–12, 90–104". */
export function lowConfidenceRanges(s: Structure): string {
  const runs: string[] = [];
  let from = -1;
  for (let i = 0; i <= s.lowConfidence.length; i++) {
    const low = i < s.lowConfidence.length && s.lowConfidence[i];
    if (low && from < 0) from = i;
    if (!low && from >= 0) {
      const a = s.start + from + 1;
      const b = s.start + i;
      runs.push(a === b ? `${a}` : `${a}–${b}`);
      from = -1;
    }
  }
  return runs.join(", ");
}
