import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { EFFECT_MISSING, EFFECT_STOPS, hexToOklab } from "./diverging";
import { PLDDT_MIN, PLDDT_STOPS, plddtColor } from "./sequential";

const L = (hex: string) => hexToOklab(hex)[0];

describe("pLDDT scale", () => {
  it("runs from the lightest stop at PLDDT_MIN to the darkest at 100, clamped", () => {
    expect(plddtColor(PLDDT_MIN)).toBe(PLDDT_STOPS[0]);
    expect(plddtColor(0)).toBe(PLDDT_STOPS[0]);
    expect(plddtColor(100)).toBe(PLDDT_STOPS[6]);
    expect(plddtColor(PLDDT_MIN + 10)).toBe(PLDDT_STOPS[1]);
  });

  it("darkens monotonically with confidence", () => {
    for (let v = PLDDT_MIN + 1; v <= 100; v++) {
      expect(L(plddtColor(v))).toBeLessThan(L(plddtColor(v - 1)));
    }
  });

  it("shares no colour with the effect scale or the missing grey", () => {
    for (const stop of PLDDT_STOPS) {
      expect([...EFFECT_STOPS, EFFECT_MISSING]).not.toContain(stop);
    }
  });

  it("matches the --color-plddt-* tokens in globals.css", () => {
    const css = readFileSync(join(__dirname, "../../app/globals.css"), "utf8");
    const tokens = PLDDT_STOPS.map(
      (_, i) => new RegExp(`--color-plddt-${i}:\\s*(#[0-9a-f]{6});`).exec(css)?.[1],
    );
    expect(tokens).toEqual([...PLDDT_STOPS]);
  });
});
