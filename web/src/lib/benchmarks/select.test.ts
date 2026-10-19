import type { Benchmarks } from "@/lib/bundle/schemas.generated";

import {
  comparison,
  coverage,
  functionGains,
  headlines,
  interval,
  ladder,
  MissingBenchmarkError,
  modelName,
  plain,
  signed,
  sizeGains,
  SIZE_BINS,
  SPLITS,
  zeroShotComparisons,
} from "./select";

type Cmp = Benchmarks["zero_shot_comparisons"][number];

function cmp(over: Partial<Cmp>): Cmp {
  return {
    model_a: "a",
    model_b: "b",
    split: null,
    metric: "spearman",
    level: "overall",
    group: null,
    size_bin: null,
    value_a: 0.5,
    value_b: 0.4,
    diff: 0.1,
    ci_low: 0.05,
    ci_high: 0.15,
    share_a_better: 1,
    n_assays: 213,
    ...over,
  };
}

function calib(scheme: string, mode: string, level: number, statistic: string, value: number) {
  return { scheme, mode, level, size_bin: "all", statistic, value };
}

/** A minimal file holding exactly the rows the selectors look up. */
function fixture(): Benchmarks {
  const supervised = SPLITS.flatMap((split) => [
    cmp({ model_a: "ridge_seq_struct", model_b: "zero_shot_saprot_score", split, diff: 0.09 }),
    ...SIZE_BINS.map((bin) =>
      cmp({
        model_a: "ridge_seq_struct",
        model_b: "zero_shot_saprot_score",
        split,
        level: "size",
        size_bin: bin,
      }),
    ),
  ]);
  const calibration = SPLITS.flatMap((scheme) =>
    ["random", "scheme"].flatMap((mode) =>
      [0.8, 0.9].flatMap((level) =>
        ["mean", "q10", "q90", "n_assays", "n_below"].map((stat) =>
          calib(scheme, mode, level, stat, stat === "n_assays" ? 213 : level),
        ),
      ),
    ),
  );
  return {
    deployed_model: "ridge_seq_struct",
    zero_shot_comparator: "zero_shot_saprot_score",
    zero_shot_comparisons: [
      cmp({ model_a: "saprot_650m_af2", model_b: "esm2_650m", diff: 0.046 }),
      cmp({ model_a: "esm2_35m_int8_wt", model_b: "esm2_35m_wt", n_assays: 217 }),
      cmp({ model_a: "saprot_650m_af2", model_b: "esm2_650m", metric: "auc" }),
    ],
    supervised_comparisons: supervised,
    function_types: SPLITS.map((split) => ({
      function_type: "Binding",
      split,
      n_assays: 13,
      supervised: 0.5,
      zero_shot: 0.4,
      diff: 0.01,
      ci_low: -0.1,
      ci_high: 0.12,
      gain_shown: false,
    })),
    calibration,
  } as unknown as Benchmarks;
}

describe("comparison", () => {
  it("finds exactly one row by its keys", () => {
    const r = comparison(fixture().zero_shot_comparisons, { a: "saprot_650m_af2", b: "esm2_650m" });
    expect(r.diff).toBe(0.046);
  });

  it("throws when the row is missing, never falling back", () => {
    expect(() =>
      comparison(fixture().zero_shot_comparisons, { a: "saprot_650m_af2", b: "esm2_35m" }),
    ).toThrow(MissingBenchmarkError);
  });

  it("throws when two rows match", () => {
    const rows = [cmp({}), cmp({})];
    expect(() => comparison(rows, { a: "a", b: "b" })).toThrow(/found 2/);
  });
});

describe("headlines", () => {
  it("reads the three Landing numbers from the file", () => {
    const h = headlines(fixture());
    expect(h.structure.diff).toBe(0.046);
    expect(h.supervised.contiguous.split).toBe("contiguous");
    expect(h.supervised.modulo.split).toBe("modulo");
    expect(h.coverage).toEqual({ level: 0.9, mean: 0.9, nAssays: 213, nBelow: 0.9 });
  });

  it("fails if a headline is not in the file", () => {
    const b = fixture();
    b.calibration = b.calibration.filter((r) => !(r.mode === "scheme" && r.statistic === "mean"));
    expect(() => headlines(b)).toThrow(MissingBenchmarkError);
  });
});

describe("chart selections", () => {
  it("leaves the int8 deployment checks and other metrics out of the zero-shot chart", () => {
    const rows = zeroShotComparisons(fixture());
    expect(rows.map((r) => r.model_a)).toEqual(["saprot_650m_af2"]);
  });

  it("gives every split its ladder, size bins in order, and function types", () => {
    const b = fixture();
    const l = ladder(b);
    for (const split of SPLITS) {
      expect(l[split]).toHaveLength(1);
      expect(sizeGains(b, split).map((r) => r.size_bin)).toEqual([...SIZE_BINS]);
      expect(functionGains(b)[split][0]!.gain_shown).toBe(false);
    }
  });

  it("pairs split-matched and random coverage for every split", () => {
    const rows = coverage(fixture(), 0.9);
    expect(rows).toHaveLength(6);
    expect(rows.every((r) => r.nAssays === 213)).toBe(true);
  });
});

describe("formatting", () => {
  it("signs differences with a true minus", () => {
    expect(signed(0.0456)).toBe("+0.046");
    expect(signed(-0.244)).toBe("−0.244");
    expect(signed(-0.0001)).toBe("+0.000");
    expect(plain(-0.014)).toBe("−0.014");
    expect(interval(-0.106, 0.121, true)).toBe("[−0.106, +0.121]");
  });

  it("names every model and refuses an unknown one", () => {
    expect(modelName("saprot_650m_af2")).toBe("SaProt 650M");
    expect(() => modelName("esm3")).toThrow();
  });
});
