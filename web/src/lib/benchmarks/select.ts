/**
 * The numbers the Landing and Benchmarks pages show, picked out of `benchmarks.json` (ADR 0041).
 *
 * Pages never type a result: every number is a row of the validated file, found by its keys. A
 * lookup that finds no row, or more than one, throws, so a page whose number is missing fails the
 * build instead of showing something else. Nothing here computes a statistic; the export did
 * (ADR 0028), and a paired difference is only ever read from a comparison row (ADR 0014).
 */
import type { Benchmarks } from "@/lib/bundle/schemas.generated";

export type ComparisonRow = Benchmarks["zero_shot_comparisons"][number];
export type SummaryRow = Benchmarks["zero_shot_summary"][number];
export type FunctionTypeRow = Benchmarks["function_types"][number];
export type CalibrationRow = Benchmarks["calibration"][number];

export const SPLITS = ["random", "modulo", "contiguous"] as const;
export type Split = (typeof SPLITS)[number];

/** What each split tests, in the words the pages use. */
export const SPLIT_NOTES: Record<Split, string> = {
  random: "positions seen in training",
  modulo: "unseen positions (every fifth)",
  contiguous: "unseen positions (blocks)",
};

export class MissingBenchmarkError extends Error {
  constructor(what: string, found: number) {
    super(`benchmarks.json: expected exactly one row for ${what}, found ${found}`);
    this.name = "MissingBenchmarkError";
  }
}

/** Readers' names for every model id in `benchmarks.json`; an unknown id is an error. */
const NAMES: Record<string, string> = {
  esm2_35m: "ESM-2 35M",
  esm2_150m: "ESM-2 150M",
  esm2_650m: "ESM-2 650M",
  esm2_35m_wt: "ESM-2 35M, wild-type marginal",
  esm2_150m_wt: "ESM-2 150M, wild-type marginal",
  esm2_650m_wt: "ESM-2 650M, wild-type marginal",
  esm2_35m_int8_wt: "ESM-2 35M int8 ONNX, wild-type marginal",
  esm2_150m_int8_wt: "ESM-2 150M int8 ONNX, wild-type marginal",
  saprot_650m_af2: "SaProt 650M",
  ensemble_esm2_650m_saprot_650m_af2: "Ensemble (ESM-2 650M + SaProt 650M)",
  ridge_site_onehot: "One-hot ridge",
  ridge_seq: "Ridge, sequence",
  ridge_seq_struct: "Ridge, sequence + structure",
  lgbm_pca_seq: "LightGBM-PCA, sequence",
  lgbm_pca_seq_struct: "LightGBM-PCA, sequence + structure",
  mlp_mse_seq_struct: "MLP (MSE), sequence + structure",
  mlp_rank_seq_struct: "MLP (RankNet), sequence + structure",
  zero_shot_esm2_score: "Zero-shot ESM-2 650M",
  zero_shot_saprot_score: "Zero-shot SaProt 650M",
};

export function modelName(id: string): string {
  const name = NAMES[id];
  if (name === undefined) throw new Error(`benchmarks.json: no reader's name for model ${id}`);
  return name;
}

/** ProteinGym's function types, as readers see them. */
export function functionTypeName(group: string): string {
  return group === "OrganismalFitness" ? "Organismal fitness" : group;
}

function one<T>(rows: readonly T[], what: string): T {
  if (rows.length !== 1) throw new MissingBenchmarkError(what, rows.length);
  return rows[0]!;
}

type ComparisonKey = {
  a: string;
  b: string;
  split?: Split | null;
  metric?: string;
  group?: string | null;
};

/** One paired comparison row (`level` overall, or function when `group` is given). */
export function comparison(rows: readonly ComparisonRow[], key: ComparisonKey): ComparisonRow {
  const { a, b, split = null, metric = "spearman", group = null } = key;
  const level = group === null ? "overall" : "function";
  return one(
    rows.filter(
      (r) =>
        r.model_a === a &&
        r.model_b === b &&
        r.split === split &&
        r.metric === metric &&
        r.level === level &&
        r.group === group &&
        r.size_bin === null,
    ),
    `${a} − ${b} (${split ?? "no split"}, ${metric}, ${group ?? "overall"})`,
  );
}

/** One summary row: a score set's own aggregate on its own assays. */
export function summary(
  rows: readonly SummaryRow[],
  model: string,
  split: Split | null = null,
  metric = "spearman",
): SummaryRow {
  return one(
    rows.filter(
      (r) =>
        r.model === model &&
        r.split === split &&
        r.metric === metric &&
        r.level === "overall" &&
        r.group === null,
    ),
    `${model} summary (${split ?? "no split"}, ${metric})`,
  );
}

export type CalibrationKey = {
  scheme: Split;
  mode: "random" | "scheme";
  level: number;
  sizeBin?: string;
};

/** One statistic over assays of test-fold coverage (ADR 0024). */
export function calibrationStat(
  rows: readonly CalibrationRow[],
  key: CalibrationKey,
  statistic: string,
): number {
  const { scheme, mode, level, sizeBin = "all" } = key;
  return one(
    rows.filter(
      (r) =>
        r.scheme === scheme &&
        r.mode === mode &&
        r.level === level &&
        r.size_bin === sizeBin &&
        r.statistic === statistic,
    ),
    `calibration ${scheme}/${mode}/${level}/${sizeBin}/${statistic}`,
  ).value;
}

export type Headlines = {
  /** SaProt 650M − ESM-2 650M, zero-shot, on their shared assays. */
  structure: ComparisonRow;
  /** Deployed model − zero-shot SaProt on unseen positions, both position-holdout splits. */
  supervised: { contiguous: ComparisonRow; modulo: ComparisonRow };
  /** Test-fold coverage of the deployed 90% intervals, split-matched calibration, contiguous. */
  coverage: { level: number; mean: number; nAssays: number; nBelow: number };
};

/** The Landing page's three numbers. Throws if any is not in the file. */
export function headlines(b: Benchmarks): Headlines {
  const deployed = b.deployed_model;
  const comparator = b.zero_shot_comparator;
  const key: CalibrationKey = { scheme: "contiguous", mode: "scheme", level: 0.9 };
  return {
    structure: comparison(b.zero_shot_comparisons, { a: "saprot_650m_af2", b: "esm2_650m" }),
    supervised: {
      contiguous: comparison(b.supervised_comparisons, {
        a: deployed,
        b: comparator,
        split: "contiguous",
      }),
      modulo: comparison(b.supervised_comparisons, { a: deployed, b: comparator, split: "modulo" }),
    },
    coverage: {
      level: key.level,
      mean: calibrationStat(b.calibration, key, "mean"),
      nAssays: calibrationStat(b.calibration, key, "n_assays"),
      nBelow: calibrationStat(b.calibration, key, "n_below"),
    },
  };
}

/**
 * Zero-shot paired comparisons (overall Spearman), in the file's order. The int8 ONNX rows
 * compare a rejected export with its fp32 source (ADR 0029): a deployment check, not a model
 * result, so they are left out and the page says so.
 */
export function zeroShotComparisons(b: Benchmarks): ComparisonRow[] {
  const rows = b.zero_shot_comparisons.filter(
    (r) =>
      r.metric === "spearman" &&
      r.level === "overall" &&
      r.split === null &&
      !r.model_a.includes("int8"),
  );
  if (rows.length === 0) throw new MissingBenchmarkError("zero-shot comparisons", 0);
  return rows;
}

/** Each zero-shot score set on its own assays (overall Spearman), in the file's order. */
export function zeroShotSummaries(b: Benchmarks): SummaryRow[] {
  const rows = b.zero_shot_summary.filter(
    (r) => r.metric === "spearman" && r.level === "overall" && r.group === null,
  );
  if (rows.length === 0) throw new MissingBenchmarkError("zero-shot summaries", 0);
  return rows;
}

/** The supervised ladder: each run − its zero-shot comparator, one list per split. */
export function ladder(b: Benchmarks): Record<Split, ComparisonRow[]> {
  const out = {} as Record<Split, ComparisonRow[]>;
  for (const split of SPLITS) {
    const rows = b.supervised_comparisons.filter(
      (r) =>
        r.split === split &&
        r.metric === "spearman" &&
        r.level === "overall" &&
        r.group === null &&
        r.size_bin === null,
    );
    if (rows.length === 0) throw new MissingBenchmarkError(`supervised ladder (${split})`, 0);
    out[split] = rows;
  }
  return out;
}

/** The supervised runs' own aggregates per split (for the table beside the ladder). */
export function ladderSummaries(b: Benchmarks): Record<Split, SummaryRow[]> {
  const out = {} as Record<Split, SummaryRow[]>;
  for (const split of SPLITS) {
    out[split] = b.supervised_summary.filter(
      (r) => r.split === split && r.level === "overall" && r.group === null,
    );
    if (out[split].length === 0) {
      throw new MissingBenchmarkError(`supervised summaries (${split})`, 0);
    }
  }
  return out;
}

/** Per-function-type gains of the deployed model, one list per split. */
export function functionGains(b: Benchmarks): Record<Split, FunctionTypeRow[]> {
  const out = {} as Record<Split, FunctionTypeRow[]>;
  for (const split of SPLITS) {
    out[split] = b.function_types.filter((r) => r.split === split);
    if (out[split].length === 0) throw new MissingBenchmarkError(`function types (${split})`, 0);
  }
  return out;
}

/** The size bins of the by-size comparison, smallest first, as the file names them. */
export const SIZE_BINS = [
  "<200",
  "200-299",
  "300-999",
  "1000-1999",
  "2000-4999",
  ">=5000",
] as const;

/** The deployed model − its comparator per assay-size bin, for one split. */
export function sizeGains(b: Benchmarks, split: Split): ComparisonRow[] {
  return SIZE_BINS.map((bin) =>
    one(
      b.supervised_comparisons.filter(
        (r) =>
          r.model_a === b.deployed_model &&
          r.model_b === b.zero_shot_comparator &&
          r.split === split &&
          r.level === "size" &&
          r.size_bin === bin,
      ),
      `size bin ${bin} (${split})`,
    ),
  );
}

export type CoverageRow = {
  scheme: Split;
  mode: "random" | "scheme";
  mean: number;
  q10: number;
  q90: number;
  nAssays: number;
  nBelow: number;
};

/** Test-fold coverage per split and calibration mode at one level (ADR 0024). */
export function coverage(b: Benchmarks, level: number): CoverageRow[] {
  return SPLITS.flatMap((scheme) =>
    (["scheme", "random"] as const).map((mode) => {
      const key = { scheme, mode, level };
      return {
        scheme,
        mode,
        mean: calibrationStat(b.calibration, key, "mean"),
        q10: calibrationStat(b.calibration, key, "q10"),
        q90: calibrationStat(b.calibration, key, "q90"),
        nAssays: calibrationStat(b.calibration, key, "n_assays"),
        nBelow: calibrationStat(b.calibration, key, "n_below"),
      };
    }),
  );
}

/** The interval levels in the file, ascending. */
export function coverageLevels(b: Benchmarks): number[] {
  const levels = [...new Set(b.calibration.map((r) => r.level))].sort((x, y) => x - y);
  if (levels.length === 0) throw new MissingBenchmarkError("calibration levels", 0);
  return levels;
}

/** `+0.046` with a true minus sign. */
export function signed(x: number, digits = 3): string {
  const s = Math.abs(x).toFixed(digits);
  return `${x < 0 && Number(s) !== 0 ? "−" : "+"}${s}`;
}

/** `0.412` with a true minus sign. */
export function plain(x: number, digits = 3): string {
  const s = Math.abs(x).toFixed(digits);
  return `${x < 0 && Number(s) !== 0 ? "−" : ""}${s}`;
}

/** `[+0.028, +0.062]` for a difference, `[0.389, 0.441]` for a level. */
export function interval(lo: number, hi: number, isDiff: boolean, digits = 3): string {
  const f = isDiff ? signed : plain;
  return `[${f(lo, digits)}, ${f(hi, digits)}]`;
}

/** `90.8%`. */
export function percent(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** The paired CI lies above 0. */
export function excludesZeroAbove(r: { ci_low: number }): boolean {
  return r.ci_low > 0;
}
