import { describe, expect, it } from "vitest";

import { EFFECT_MISSING } from "@/lib/color/diverging";
import { PLDDT_STOPS } from "@/lib/color/sequential";
import { protein, structure, structureRef } from "@/test/fixtures";

import { buildView, decodeProtein, positionMeans } from "./landscape";
import { sharedLut } from "./raster";
import {
  caOf,
  coverageColours,
  decodeStructure,
  effectColours,
  indexOfPosition,
  isPartial,
  lowConfidenceRanges,
  meanColour,
  plddtColours,
  positionOfResi,
  resiOfPosition,
  toPdb,
} from "./structure";

const ref = (start: number, end: number) => structureRef(new Uint8Array([1]), start, end);

/** Residues 3–6 (1-indexed) of a 10-residue target, numbered 101–104 in the PDB. */
const partial = () =>
  decodeStructure(
    structure("KVAR", { firstNumber: 101, plddt: [95, 65.5, 40, 88] }),
    ref(2, 6),
    10,
  );

describe("mapping", () => {
  it("maps target positions to PDB residue numbers and back, inside the structure only", () => {
    const s = partial();
    expect(isPartial(s)).toBe(true);
    expect(indexOfPosition(s, 2)).toBe(0);
    expect(resiOfPosition(s, 2)).toBe(101);
    expect(resiOfPosition(s, 5)).toBe(104);
    expect(resiOfPosition(s, 1)).toBeNull();
    expect(resiOfPosition(s, 6)).toBeNull();
    for (let p = 2; p < 6; p++) expect(positionOfResi(s, resiOfPosition(s, p)!)).toBe(p);
    expect(positionOfResi(s, 100)).toBeNull();
    expect(positionOfResi(s, 105)).toBeNull();
  });

  it("finds each residue's CA", () => {
    // The fixture puts atom k of residue i at x = (4i + k) * 1.5 Å; CA is k = 1.
    expect(caOf(partial(), 2)).toEqual({ x: 13.5, y: 0.25, z: -0.5 });
  });

  it("marks residues below the protein file's threshold as low confidence", () => {
    const s = partial();
    expect(s.lowPlddtThreshold).toBe(70);
    expect(s.lowConfidence).toEqual([false, true, true, false]);
    expect(lowConfidenceRanges(s)).toBe("4–5");
  });
});

describe("toPdb", () => {
  it("writes fixed-column ATOM records with the file's numbers and pLDDT as B-factor", () => {
    const lines = toPdb(partial()).split("\n");
    expect(lines).toHaveLength(4 * 4 + 3); // 16 atoms, TER, END, trailing newline
    const ca = lines[1]!;
    expect(ca.slice(0, 6)).toBe("ATOM  ");
    expect(ca.slice(12, 16)).toBe(" CA ");
    expect(ca.slice(17, 20)).toBe("LYS");
    expect(ca[21]).toBe("A");
    expect(Number(ca.slice(22, 26))).toBe(101);
    expect([30, 38, 46].map((c) => Number(ca.slice(c, c + 8)))).toEqual([1.5, 0.25, -0.5]);
    expect(Number(ca.slice(60, 66))).toBe(95);
    expect(ca[77]).toBe("C");
    expect(lines[4]!.slice(12, 16)).toBe(" N  ");
    expect(Number(lines[4]!.slice(22, 26))).toBe(102);
    expect(lines.slice(-3)).toEqual(["TER", "END", ""]);
  });
});

describe("colours", () => {
  const p = { ...protein(), structure_file: ref(0, 2) };
  const landscape = decodeProtein(p);
  const view = buildView(landscape, {
    model: "esm2_650m",
    mode: "predicted",
    maskUnmeasured: false,
  });
  const s = decodeStructure(structure("MK"), ref(0, 2), 2);

  it("colours each residue exactly like the summary bar's mean", () => {
    const means = positionMeans(view, landscape.length);
    const table = sharedLut();
    expect(effectColours(s, means, view)).toEqual([0, 1].map((p) => meanColour(means[p]!, view)));
    // The fixture's scores are all −12: the clipped, most harmful end of the table.
    const end = `#${[0, 1, 2].map((c) => table[c]!.toString(16).padStart(2, "0")).join("")}`;
    expect(meanColour(-12, view)).toBe(end);
  });

  it("is grey where the mean has no value (an unmeasured residue), never white", () => {
    const means = Float32Array.from([Number.NaN, 0]);
    const colours = effectColours(s, means, view);
    expect(colours[0]).toBe(EFFECT_MISSING);
    expect(colours[1]).toBe("#ffffff"); // the neutral mean is white, the missing one is not
  });

  it("colours by pLDDT on its own scale", () => {
    const high = decodeStructure(structure("MK", { plddt: [100, 40] }), ref(0, 2), 2);
    expect(plddtColours(high)).toEqual([PLDDT_STOPS[6], PLDDT_STOPS[0]]);
  });

  it("shows residues outside a partial structure as grey in the coverage strip", () => {
    const strip = coverageColours(partial(), ["#111111", "#222222", "#333333", "#444444"]);
    expect(strip).toEqual([
      EFFECT_MISSING,
      EFFECT_MISSING,
      "#111111",
      "#222222",
      "#333333",
      "#444444",
      ...Array(4).fill(EFFECT_MISSING),
    ]);
  });
});
