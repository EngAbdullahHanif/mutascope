/**
 * The effect colour scale (DESIGN.md, "Effect colours").
 *
 * Diverging and symmetric around 0: blue = harmful (negative), white = neutral, red = tolerated
 * (positive). The arms are ColorBrewer RdBu (11 classes), chosen because it stays readable
 * under the common colour-vision deficiencies; the midpoint is pure white. Values are
 * interpolated in OKLab, so equal steps in a score look like equal steps in colour, and the
 * same stops are the `--color-effect-*` tokens in globals.css (a test keeps them equal).
 */

/** Stops from -1 (most harmful) to +1 (most tolerated), evenly spaced. */
export const EFFECT_STOPS = [
  "#053061",
  "#2166ac",
  "#4393c3",
  "#92c5de",
  "#d1e5f0",
  "#ffffff",
  "#fddbc7",
  "#f4a582",
  "#d6604d",
  "#b2182b",
  "#67001f",
] as const;

/** No value (not measured, outside the structure): never a point on the scale. */
export const EFFECT_MISSING = "#bdbdbd";

type Lab = readonly [number, number, number];

function hexToRgb(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const toLinear = (c: number): number => {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};

const fromLinear = (c: number): number => {
  const s = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, s)) * 255);
};

/** sRGB hex to OKLab (Ottosson 2020). */
export function hexToOklab(hex: string): Lab {
  const [r, g, b] = hexToRgb(hex).map(toLinear) as [number, number, number];
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function oklabToHex([L, A, B]: Lab): string {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(fromLinear);
  return `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

const STOPS_LAB = EFFECT_STOPS.map(hexToOklab);

/** Colour at `t` in [-1, 1] (clamped). */
export function effectColorAt(t: number): string {
  const x = (Math.min(1, Math.max(-1, t)) + 1) / 2; // 0..1
  const segments = STOPS_LAB.length - 1;
  const i = Math.min(segments - 1, Math.floor(x * segments));
  const f = x * segments - i;
  const a = STOPS_LAB[i]!;
  const b = STOPS_LAB[i + 1]!;
  return oklabToHex([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]);
}

/**
 * Colour of an effect `value` on a scale symmetric around 0 with half-width `maxAbs`.
 * `null` or NaN is "no value" ({@link EFFECT_MISSING}), never white: white means neutral.
 */
export function effectColor(value: number | null | undefined, maxAbs: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EFFECT_MISSING;
  if (!(maxAbs > 0)) throw new RangeError(`maxAbs must be positive, got ${maxAbs}`);
  return effectColorAt(value / maxAbs);
}

/**
 * Half-width of a symmetric domain covering `values`: the largest |value|, ignoring missing
 * ones. Symmetric so that 0 is always white, whatever the data's skew.
 */
export function symmetricMax(values: Iterable<number | null | undefined>): number {
  let max = 0;
  for (const v of values) {
    if (v !== null && v !== undefined && !Number.isNaN(v)) max = Math.max(max, Math.abs(v));
  }
  return max > 0 ? max : 1;
}

/**
 * Half-width of a symmetric domain around `center` that covers the share `q` of `values`
 * (by |value − center|). Larger deviations are drawn at the end colours, and the legend marks
 * its ends as "≤" / "≥". A few extreme variants would otherwise wash every other cell out to
 * near-white (ADR 0038).
 */
export function robustHalfWidth(values: ArrayLike<number>, center: number, q = 0.99): number {
  const dev: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i]!;
    if (!Number.isNaN(v)) dev.push(Math.abs(v - center));
  }
  if (dev.length === 0) return 1;
  dev.sort((a, b) => a - b);
  const w = dev[Math.min(dev.length - 1, Math.floor(q * (dev.length - 1)))]!;
  return w > 0 ? w : dev[dev.length - 1]! || 1;
}

/** Number of colours in {@link effectLut}: fine enough that neighbours are indistinguishable. */
export const LUT_STEPS = 257;

/**
 * The scale as an RGBA lookup table of `steps` colours from −1 to +1 (index `(steps − 1) / 2`
 * is white), so a canvas can colour tens of thousands of cells without one interpolation each.
 */
export function effectLut(steps = LUT_STEPS): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(steps * 4);
  for (let i = 0; i < steps; i++) {
    const [r, g, b] = hexToRgb(effectColorAt((2 * i) / (steps - 1) - 1));
    lut.set([r, g, b, 255], i * 4);
  }
  return lut;
}

/** Index into {@link effectLut} of `value` on the domain `center ± halfWidth` (clamped). */
export function lutIndex(
  value: number,
  center: number,
  halfWidth: number,
  steps = LUT_STEPS,
): number {
  const t = Math.min(1, Math.max(-1, (value - center) / halfWidth));
  return Math.round(((t + 1) / 2) * (steps - 1));
}

/** {@link EFFECT_MISSING} as RGB bytes. */
export const EFFECT_MISSING_RGB = hexToRgb(EFFECT_MISSING);
