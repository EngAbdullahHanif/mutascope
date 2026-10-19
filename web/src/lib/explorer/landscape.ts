/**
 * From a validated protein file to what the Explorer draws (ADR 0038).
 *
 * Grids in the bundle are `(L, 20)`, row-major, in {@link AA_ORDER}, fixed-point with `null` for
 * no value (ADR 0028). Here they become `Float32Array`s with `NaN` for no value, decoded once
 * per protein; a view (model × predicted/measured × mask) is then one more pass over L × 20
 * numbers. Positions are 0-indexed everywhere; {@link mutationCode} is the only place that
 * writes the 1-indexed form readers see.
 */
import { robustHalfWidth } from "@/lib/color/diverging";
import type { ProteinFile, Quantized } from "@/lib/bundle/schemas.generated";

/** The bundle's amino-acid order (`AA_ORDER` in the Python package); never reorder. */
export const AA_ORDER = "ACDEFGHIKLMNPQRSTVWY";
export const N_AA = 20;

/** Readers' names for the bundle's model ids. */
export const MODEL_NAMES: Record<string, string> = {
  ridge_seq_struct: "Ridge (sequence + structure)",
  esm2_650m: "ESM-2 650M",
  saprot_650m_af2: "SaProt 650M",
};

const ZERO_SHOT_ORDER = ["esm2_650m", "saprot_650m_af2"] as const;

export type Kind = "supervised" | "zero_shot";

export type ModelOption = {
  /** Bundle model id: `ridge_seq_struct`, `esm2_650m`, `saprot_650m_af2`. */
  model: string;
  name: string;
  kind: Kind;
  /** `"Supervised (n = 9,576)"` or `"Zero-shot"`: shown with every prediction. */
  label: string;
  isDefault: boolean;
};

export type PositionStatus = "measured" | "partially_measured" | "unmeasured";

export type SupervisedGrids = {
  model: string;
  n: number;
  prediction: Float32Array;
  lower80: Float32Array;
  upper80: Float32Array;
  lower90: Float32Array;
  upper90: Float32Array;
  structureImputed: boolean[];
};

export type Landscape = {
  assayId: string;
  sequence: string;
  length: number;
  /** Per residue: index of the wild type in {@link AA_ORDER}, or -1 if not canonical. */
  wt: Int8Array;
  /** `DMS_score`, higher = fitter; NaN where not measured. */
  measured: Float32Array;
  /** Masked-marginal log-ratios by model id; the wild type is 0. */
  zeroShot: Record<string, Float32Array>;
  supervised: SupervisedGrids | null;
  positionStatus: PositionStatus[];
  /** Median measured `DMS_score`: the neutral point of every view in DMS units. */
  dmsCenter: number;
  /** Half-width shared by the measured and supervised views, so they can be compared. */
  dmsHalfWidth: number;
  options: ModelOption[];
  msaRegion: { start: number; end: number; label: string } | null;
};

export function dequantize(q: Quantized): Float32Array {
  const out = new Float32Array(q.values.length);
  const scale = 10 ** q.decimals;
  for (let i = 0; i < q.values.length; i++) {
    const v = q.values[i];
    out[i] = v === null || v === undefined ? Number.NaN : v / scale;
  }
  return out;
}

/** `A123G` from a 0-indexed position: the one place the website writes a 1-indexed residue. */
export function mutationCode(wt: string, position: number, mt: string): string {
  return `${wt}${position + 1}${mt}`;
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function supervisedLabel(n: number): string {
  return `Supervised (n = ${formatCount(n)})`;
}

/** Default first, then ESM-2 650M and SaProt where the file has them (never twice). */
export function modelOptions(p: ProteinFile): ModelOption[] {
  const options: ModelOption[] = [];
  const add = (model: string, kind: Kind, isDefault: boolean) => {
    if (options.some((o) => o.model === model)) return;
    const label = kind === "supervised" ? supervisedLabel(p.supervised!.n_measured) : "Zero-shot";
    options.push({ model, name: MODEL_NAMES[model] ?? model, kind, label, isDefault });
  };
  add(p.default.model, p.default.kind, true);
  for (const model of ZERO_SHOT_ORDER) {
    if (model in p.zero_shot) add(model, "zero_shot", false);
  }
  return options;
}

function median(values: Float32Array): number {
  const finite = Array.from(values).filter((v) => !Number.isNaN(v));
  if (finite.length === 0) return 0;
  finite.sort((a, b) => a - b);
  const mid = finite.length >> 1;
  return finite.length % 2 ? finite[mid]! : (finite[mid - 1]! + finite[mid]!) / 2;
}

/** All 19 substitutions measured, some, or none: the bundle's `position_status` rule. */
export function positionStatusFrom(measured: Float32Array, wt: Int8Array): PositionStatus[] {
  const out: PositionStatus[] = [];
  for (let pos = 0; pos < wt.length; pos++) {
    let n = 0;
    for (let aa = 0; aa < N_AA; aa++) {
      if (aa !== wt[pos] && !Number.isNaN(measured[pos * N_AA + aa]!)) n++;
    }
    out.push(n === 0 ? "unmeasured" : n === N_AA - 1 ? "measured" : "partially_measured");
  }
  return out;
}

export function decodeProtein(p: ProteinFile): Landscape {
  const sequence = p.assay.target_seq;
  const length = p.assay.length;
  const wt = new Int8Array(length);
  for (let i = 0; i < length; i++) wt[i] = AA_ORDER.indexOf(sequence[i] ?? "");
  const measured = dequantize(p.measured);
  const zeroShot: Record<string, Float32Array> = {};
  for (const [model, z] of Object.entries(p.zero_shot)) zeroShot[model] = dequantize(z.scores);
  const s = p.supervised;
  const supervised: SupervisedGrids | null = s
    ? {
        model: s.model,
        n: s.n_measured,
        prediction: dequantize(s.prediction),
        lower80: dequantize(s.lower_80),
        upper80: dequantize(s.upper_80),
        lower90: dequantize(s.lower_90),
        upper90: dequantize(s.upper_90),
        structureImputed: s.structure_imputed,
      }
    : null;
  const dmsCenter = median(measured);
  const both = supervised ? concat(measured, supervised.prediction) : measured;
  return {
    assayId: p.assay.assay_id,
    sequence,
    length,
    wt,
    measured,
    zeroShot,
    supervised,
    positionStatus: s ? s.position_status : positionStatusFrom(measured, wt),
    dmsCenter,
    dmsHalfWidth: robustHalfWidth(both, dmsCenter),
    options: modelOptions(p),
    msaRegion: p.msa_region,
  };
}

function concat(a: Float32Array, b: Float32Array): Float32Array {
  const out = new Float32Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

export type ViewMode = "predicted" | "measured";

export type ViewOptions = {
  model: string;
  mode: ViewMode;
  /** Predicted view: grey out substitutions the assay did not measure. */
  maskUnmeasured: boolean;
};

export type View = {
  /** `(L, 20)`; NaN = drawn as missing. The wild type is always NaN: it is not a substitution. */
  values: Float32Array;
  /** Neutral point (white): 0 for zero-shot log-ratios, the assay's median DMS score otherwise. */
  center: number;
  halfWidth: number;
  units: "log_ratio" | "dms";
  /** What the numbers are, for the legend and the CSV. */
  title: string;
  option: ModelOption;
  /** `option.label` for a prediction, `"Measured"` for the assay's own values. */
  label: string;
  decimals: number;
};

export function optionFor(l: Landscape, model: string): ModelOption {
  return l.options.find((o) => o.model === model) ?? l.options[0]!;
}

export function buildView(l: Landscape, opts: ViewOptions): View {
  const option = optionFor(l, opts.model);
  let source: Float32Array;
  let units: View["units"];
  let title: string;
  if (opts.mode === "measured") {
    source = l.measured;
    units = "dms";
    title = "Measured DMS score";
  } else if (option.kind === "supervised") {
    source = l.supervised!.prediction;
    units = "dms";
    title = `${option.name}: predicted DMS score`;
  } else {
    source = l.zeroShot[option.model]!;
    units = "log_ratio";
    title = `${option.name} score (log-ratio)`;
  }
  const values = new Float32Array(source);
  const mask = opts.mode === "predicted" && opts.maskUnmeasured;
  for (let pos = 0; pos < l.length; pos++) {
    const w = l.wt[pos]!;
    if (w >= 0) values[pos * N_AA + w] = Number.NaN;
    if (mask) {
      for (let aa = 0; aa < N_AA; aa++) {
        const i = pos * N_AA + aa;
        if (Number.isNaN(l.measured[i]!)) values[i] = Number.NaN;
      }
    }
  }
  const center = units === "dms" ? l.dmsCenter : 0;
  const halfWidth = units === "dms" ? l.dmsHalfWidth : robustHalfWidth(source, 0);
  return {
    values,
    center,
    halfWidth,
    units,
    title,
    option,
    label: opts.mode === "measured" ? "Measured" : option.label,
    decimals: units === "dms" ? decimalsFor(halfWidth) : 2,
  };
}

/** Enough decimals to show about three significant digits at the scale's half-width. */
export function decimalsFor(halfWidth: number): number {
  if (!(halfWidth > 0)) return 2;
  return Math.max(0, Math.min(4, 2 - Math.floor(Math.log10(halfWidth))));
}

/** Mean of each residue's shown substitutions (NaN if none): the bar above the heatmap. */
export function positionMeans(view: View, length: number): Float32Array {
  const out = new Float32Array(length);
  for (let pos = 0; pos < length; pos++) {
    let sum = 0;
    let n = 0;
    for (let aa = 0; aa < N_AA; aa++) {
      const v = view.values[pos * N_AA + aa]!;
      if (!Number.isNaN(v)) {
        sum += v;
        n++;
      }
    }
    out[pos] = n > 0 ? sum / n : Number.NaN;
  }
  return out;
}

export type Cell = { position: number; aa: number; value: number };

/** The `k` lowest (most harmful) and highest (most tolerated) shown substitutions. */
export function extremes(view: View, k = 10): { harmful: Cell[]; tolerated: Cell[] } {
  const cells: Cell[] = [];
  for (let i = 0; i < view.values.length; i++) {
    const value = view.values[i]!;
    if (!Number.isNaN(value)) cells.push({ position: Math.floor(i / N_AA), aa: i % N_AA, value });
  }
  // Ties broken by position, then amino acid, so the lists never depend on sort stability.
  const byValue = (a: Cell, b: Cell) => a.value - b.value || a.position - b.position || a.aa - b.aa;
  cells.sort(byValue);
  return { harmful: cells.slice(0, k), tolerated: cells.slice(-k).reverse() };
}

export type CellDetail = {
  code: string;
  position: number;
  wt: string;
  mt: string;
  isWildType: boolean;
  /** The selected model's prediction (NaN at the wild type for supervised). */
  predicted: number;
  option: ModelOption;
  /** Supervised model selected: its CV+ intervals. */
  intervals: { level: number; lower: number; upper: number }[] | null;
  measured: number;
  /** Measured variant with a supervised model: the out-of-fold prediction (ADR 0025). */
  outOfFold: number | null;
  positionStatus: PositionStatus;
  /** Supervised assays only: a structure feature was imputed at this residue. */
  structureImputed: boolean | null;
};

export function cellDetail(l: Landscape, model: string, position: number, aa: number): CellDetail {
  const option = optionFor(l, model);
  const i = position * N_AA + aa;
  const s = l.supervised;
  const wt = l.sequence[position] ?? "?";
  const mt = AA_ORDER[aa]!;
  const measured = l.measured[i]!;
  const predicted =
    option.kind === "supervised" ? s!.prediction[i]! : l.zeroShot[option.model]![i]!;
  return {
    code: mutationCode(wt, position, mt),
    position,
    wt,
    mt,
    isWildType: l.wt[position] === aa,
    predicted,
    option,
    intervals:
      option.kind === "supervised" && s && !Number.isNaN(s.prediction[i]!)
        ? [
            { level: 80, lower: s.lower80[i]!, upper: s.upper80[i]! },
            { level: 90, lower: s.lower90[i]!, upper: s.upper90[i]! },
          ]
        : null,
    measured,
    outOfFold: s && !Number.isNaN(measured) ? s.prediction[i]! : null,
    positionStatus: l.positionStatus[position]!,
    structureImputed: s ? s.structureImputed[position]! : null,
  };
}

const POSITION_STATUS_TEXT: Record<PositionStatus, string> = {
  measured: "all 19 substitutions measured",
  partially_measured: "some substitutions measured",
  unmeasured: "no substitutions measured",
};

export function positionStatusText(status: PositionStatus): string {
  return POSITION_STATUS_TEXT[status];
}

/** Fixed decimals with a true minus sign (matches the legend). */
export function formatValue(value: number, decimals: number): string {
  if (Number.isNaN(value)) return "—";
  const text = Math.abs(value).toFixed(decimals);
  return value < 0 && Number(text) !== 0 ? `−${text}` : text;
}

/** One sentence for a screen reader: the same facts as the tooltip. */
export function describeCell(d: CellDetail, decimals: number): string {
  const f = (v: number) => formatValue(v, decimals);
  if (d.isWildType) return `${d.wt}${d.position + 1}: wild type.`;
  const parts = [`${d.code}. ${d.option.label} ${d.option.name}: ${f(d.predicted)}`];
  for (const iv of d.intervals ?? [])
    parts.push(`${iv.level}% interval ${f(iv.lower)} to ${f(iv.upper)}`);
  parts.push(Number.isNaN(d.measured) ? "not measured" : `measured ${f(d.measured)}`);
  if (d.outOfFold !== null) parts.push(`out-of-fold prediction ${f(d.outOfFold)}`);
  parts.push(`residue ${d.position + 1}: ${positionStatusText(d.positionStatus)}`);
  if (d.structureImputed) parts.push("structure features imputed");
  return `${parts.join("; ")}.`;
}

/** Plain numbers for CSV: full precision as decoded, empty for no value. */
function csvNumber(v: number): string {
  return Number.isNaN(v) ? "" : String(Number(v.toPrecision(7)));
}

/**
 * The current view as CSV: one row per substitution shown (masked and wild-type cells are not
 * shown, so not written). Each row carries the model and its label, so a copy taken out of
 * context still says "Zero-shot" or "Supervised (n = …)".
 */
export function viewCsv(l: Landscape, view: View, mode: ViewMode): string {
  const sup = view.option.kind === "supervised" && mode === "predicted" ? l.supervised : null;
  const header = ["mutation", "position", "wt", "mt", "value", "quantity", "model", "label"];
  if (sup) header.push("lower_80", "upper_80", "lower_90", "upper_90");
  header.push("measured_dms_score");
  const rows = [header.join(",")];
  const quantity =
    mode === "measured"
      ? "measured_dms_score"
      : view.units === "dms"
        ? "predicted_dms_score"
        : "log_ratio";
  // A measured view is no model's output: it says so instead of naming the selected model.
  const model = mode === "measured" ? "measured" : view.option.model;
  for (let pos = 0; pos < l.length; pos++) {
    for (let aa = 0; aa < N_AA; aa++) {
      const i = pos * N_AA + aa;
      const v = view.values[i]!;
      if (Number.isNaN(v)) continue;
      const wt = l.sequence[pos]!;
      const mt = AA_ORDER[aa]!;
      const row = [mutationCode(wt, pos, mt), String(pos + 1), wt, mt, csvNumber(v), quantity];
      row.push(model, `"${view.label}"`);
      if (sup) {
        row.push(
          ...[sup.lower80, sup.upper80, sup.lower90, sup.upper90].map((g) => csvNumber(g[i]!)),
        );
      }
      row.push(csvNumber(l.measured[i]!));
      rows.push(row.join(","));
    }
  }
  return `${rows.join("\n")}\n`;
}
