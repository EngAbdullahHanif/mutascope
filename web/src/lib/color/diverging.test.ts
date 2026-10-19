import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  EFFECT_MISSING,
  EFFECT_STOPS,
  effectColor,
  effectColorAt,
  EFFECT_MISSING_RGB,
  LUT_STEPS,
  effectLut,
  hexToOklab,
  lutIndex,
  robustHalfWidth,
  symmetricMax,
} from "./diverging";

const L = (hex: string) => hexToOklab(hex)[0];

describe("effect scale", () => {
  it("is white at 0 and the RdBu poles at the ends (blue harmful, red tolerated)", () => {
    expect(effectColor(0, 2)).toBe("#ffffff");
    expect(effectColor(-2, 2)).toBe("#053061");
    expect(effectColor(2, 2)).toBe("#67001f");
    expect(effectColorAt(-0.2)).toBe("#d1e5f0");
    expect(effectColorAt(0.2)).toBe("#fddbc7");
  });

  it("clamps outside the domain", () => {
    expect(effectColor(-50, 2)).toBe("#053061");
    expect(effectColor(50, 2)).toBe("#67001f");
  });

  it("shows no value as grey, never as neutral white", () => {
    expect(effectColor(null, 1)).toBe(EFFECT_MISSING);
    expect(effectColor(Number.NaN, 1)).toBe(EFFECT_MISSING);
    expect(EFFECT_MISSING).not.toBe(effectColor(0, 1));
  });

  it("darkens monotonically away from 0 on both arms, at about the same rate", () => {
    const ts = Array.from({ length: 51 }, (_, i) => i / 50);
    for (let i = 1; i < ts.length; i++) {
      expect(L(effectColorAt(ts[i]!))).toBeLessThan(L(effectColorAt(ts[i - 1]!)));
      expect(L(effectColorAt(-ts[i]!))).toBeLessThan(L(effectColorAt(-ts[i - 1]!)));
    }
    // Symmetric in lightness: equal |score| reads as equal strength on either side.
    for (const t of ts) {
      expect(Math.abs(L(effectColorAt(t)) - L(effectColorAt(-t)))).toBeLessThan(0.08);
    }
  });

  it("refuses a non-positive domain", () => {
    expect(() => effectColor(1, 0)).toThrow(RangeError);
  });

  it("symmetricMax ignores missing values and never returns 0", () => {
    expect(symmetricMax([-3, 1, null, Number.NaN])).toBe(3);
    expect(symmetricMax([0, null])).toBe(1);
  });

  it("matches the --color-effect-* tokens in globals.css", () => {
    const css = readFileSync(join(__dirname, "../../app/globals.css"), "utf8");
    const names = [
      "neg-5",
      "neg-4",
      "neg-3",
      "neg-2",
      "neg-1",
      "0",
      "pos-1",
      "pos-2",
      "pos-3",
      "pos-4",
      "pos-5",
    ];
    const tokens = names.map((n) => {
      const match = new RegExp(`--color-effect-${n}:\\s*(#[0-9a-f]{6});`).exec(css);
      return match?.[1];
    });
    expect(tokens).toEqual([...EFFECT_STOPS]);
    expect(/--color-effect-missing:\s*(#[0-9a-f]{6});/.exec(css)?.[1]).toBe(EFFECT_MISSING);
  });

  it("robustHalfWidth covers the share q of deviations around the centre", () => {
    const values = Float32Array.from([...Array.from({ length: 99 }, (_, i) => i / 98), 100]);
    // 100 values, one outlier at 100: q = 0.99 reaches the 99th smallest deviation (1), not 100.
    expect(robustHalfWidth(values, 0)).toBeCloseTo(1);
    expect(robustHalfWidth(values, 0, 1)).toBe(100);
    expect(robustHalfWidth(Float32Array.from([5, 5, Number.NaN]), 5)).toBe(1);
    expect(robustHalfWidth(Float32Array.from([2, 4]), 3)).toBe(1);
  });
});

describe("lookup table", () => {
  const lut = effectLut();
  const hexAt = (i: number) =>
    `#${[lut[i * 4]!, lut[i * 4 + 1]!, lut[i * 4 + 2]!].map((c) => c.toString(16).padStart(2, "0")).join("")}`;

  it("has white in the middle and the poles at the ends", () => {
    expect(hexAt((LUT_STEPS - 1) / 2)).toBe("#ffffff");
    expect(hexAt(0)).toBe(EFFECT_STOPS[0]);
    expect(hexAt(LUT_STEPS - 1)).toBe(EFFECT_STOPS[10]);
  });

  it("indexes a value around its centre, clamped", () => {
    expect(lutIndex(3, 3, 2)).toBe((LUT_STEPS - 1) / 2);
    expect(lutIndex(1, 3, 2)).toBe(0);
    expect(lutIndex(-50, 3, 2)).toBe(0);
    expect(lutIndex(50, 3, 2)).toBe(LUT_STEPS - 1);
  });

  it("matches effectColor at every step (the canvas and the legend agree)", () => {
    for (let i = 0; i < LUT_STEPS; i++) {
      const t = (2 * i) / (LUT_STEPS - 1) - 1;
      expect(hexAt(i)).toBe(effectColor(t, 1));
      expect(lutIndex(t, 0, 1)).toBe(i);
    }
  });

  it("keeps missing grey apart from every scale colour", () => {
    const [r, g, b] = EFFECT_MISSING_RGB;
    for (let i = 0; i < LUT_STEPS; i++) {
      expect([lut[i * 4], lut[i * 4 + 1], lut[i * 4 + 2]]).not.toEqual([r, g, b]);
    }
  });
});
