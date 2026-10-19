import { niceAxis, tickLabel, toPercent } from "./scale";

describe("niceAxis", () => {
  it("holds every value and the reference, on round ticks", () => {
    const axis = niceAxis([0.028, 0.062, -0.0018, 0.084], [0]);
    expect(axis.min).toBeLessThanOrEqual(-0.0018);
    expect(axis.max).toBeGreaterThanOrEqual(0.084);
    expect(axis.ticks).toContain(0);
    expect(axis.ticks[0]).toBe(axis.min);
    expect(axis.ticks.at(-1)).toBe(axis.max);
  });

  it("includes a reference outside the data", () => {
    const axis = niceAxis([0.6, 0.85], [0.9]);
    expect(axis.max).toBeGreaterThanOrEqual(0.9);
  });

  it("refuses an empty input", () => {
    expect(() => niceAxis([])).toThrow();
  });
});

describe("toPercent and tickLabel", () => {
  const axis = niceAxis([-0.4, 0.3], [0]);
  it("maps the ends to 0 and 100 and clamps outside", () => {
    expect(toPercent(axis, axis.min)).toBe(0);
    expect(toPercent(axis, axis.max)).toBe(100);
    expect(toPercent(axis, axis.max + 1)).toBe(100);
  });
  it("uses a true minus sign and the step's decimals", () => {
    expect(tickLabel(-0.2, axis)).toBe("−0.2");
    expect(tickLabel(0, axis)).toBe("0.0");
  });
});
