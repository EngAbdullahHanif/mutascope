import { describe, expect, it } from "vitest";

import { EFFECT_MISSING_RGB, effectColor } from "@/lib/color/diverging";
import { protein } from "@/test/fixtures";

import { AA_ORDER, N_AA, buildView, decodeProtein } from "./landscape";
import { fitZoom, rasterize, tickPositions, tickStep, ZOOM_LEVELS } from "./raster";

const hex = (bytes: Uint8ClampedArray, o: number) =>
  `#${[bytes[o]!, bytes[o + 1]!, bytes[o + 2]!].map((c) => c.toString(16).padStart(2, "0")).join("")}`;

describe("rasterize", () => {
  it("puts residue on x and amino acid on y, with the scale's colours", () => {
    const p = protein();
    // MK: zero-shot M1A = -1, K2Y = +1, everything else 0 except the wild types.
    p.zero_shot.esm2_650m!.scores.values = Array.from({ length: 40 }, (_, i) =>
      i === 0 ? -100 : i === 20 + 19 ? 100 : 0,
    );
    const l = decodeProtein(p);
    const view = buildView(l, { model: "esm2_650m", mode: "predicted", maskUnmeasured: false });
    view.halfWidth = 1;
    const px = rasterize(view, l.length);
    expect(px).toHaveLength(2 * N_AA * 4);
    const at = (pos: number, aa: number) => (aa * l.length + pos) * 4;
    expect(hex(px, at(0, 0))).toBe(effectColor(-1, 1));
    expect(hex(px, at(1, 19))).toBe(effectColor(1, 1));
    expect(hex(px, at(1, 0))).toBe("#ffffff");
    // Wild types (M1M, K2K) are NaN in the view: the missing grey, which the canvas marks.
    const m = AA_ORDER.indexOf("M");
    expect([px[at(0, m)], px[at(0, m) + 1], px[at(0, m) + 2]]).toEqual([...EFFECT_MISSING_RGB]);
    expect(px[at(0, m) + 3]).toBe(255);
  });
});

describe("zoom and ruler", () => {
  it("fits small proteins at the widest zoom that fits, large ones at the narrowest", () => {
    expect(fitZoom(37, 1000)).toBe(24);
    expect(fitZoom(100, 1000)).toBe(10);
    expect(fitZoom(3423, 1000)).toBe(ZOOM_LEVELS[0]);
  });

  it("spaces ruler labels by at least 44 px", () => {
    expect(tickStep(24)).toBe(2);
    expect(tickStep(3)).toBe(20);
    expect(tickStep(44)).toBe(1);
  });

  it("labels residue 1 and 1-indexed multiples of the step", () => {
    expect(tickPositions(0, 25, 10)).toEqual([0, 9, 19]);
    expect(tickPositions(12, 41, 10)).toEqual([19, 29, 39]);
    expect(tickPositions(0, 3, 1)).toEqual([0, 1, 2]);
  });
});
