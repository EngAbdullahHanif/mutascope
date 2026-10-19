/**
 * A linear axis for the Benchmarks charts: a domain that holds every interval (and a reference
 * value such as 0), widened to round ticks. Pure, so the charts render on the server.
 */

export type Axis = { min: number; max: number; ticks: number[] };

function niceStep(span: number, target: number): number {
  const raw = span / target;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return nice * power;
}

/**
 * Round the domain out to ticks so every value in `values` and every reference sits inside.
 * `target` is the rough number of intervals between ticks.
 */
export function niceAxis(
  values: readonly number[],
  references: readonly number[] = [],
  target = 4,
): Axis {
  const all = [...values, ...references].filter(Number.isFinite);
  if (all.length === 0) throw new Error("niceAxis: no finite values");
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (lo === hi) {
    lo -= 0.5;
    hi += 0.5;
  }
  const step = niceStep(hi - lo, target);
  const min = Math.floor(lo / step + 1e-9) * step;
  const max = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Number(t.toFixed(10)));
  return { min, max, ticks };
}

/** Position of `x` on the axis, in percent of its width, clamped to [0, 100]. */
export function toPercent(axis: Axis, x: number): number {
  const p = ((x - axis.min) / (axis.max - axis.min)) * 100;
  return Math.min(100, Math.max(0, p));
}

/** Tick label: as few decimals as the step needs, with a true minus sign. */
export function tickLabel(t: number, axis: Axis): string {
  const step = axis.ticks.length > 1 ? axis.ticks[1]! - axis.ticks[0]! : 1;
  let digits = 0;
  while (digits < 3 && Math.abs(Number(step.toFixed(digits)) - step) > 1e-9) digits += 1;
  const s = Math.abs(t).toFixed(digits);
  return `${t < 0 && Number(s) !== 0 ? "−" : ""}${s}`;
}
