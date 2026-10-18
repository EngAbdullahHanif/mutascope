import { describe, expect, it } from "vitest";

import type { ProteinFile } from "@/lib/bundle/schemas.generated";
import { protein } from "@/test/fixtures";

import {
  AA_ORDER,
  N_AA,
  buildView,
  cellDetail,
  decimalsFor,
  decodeProtein,
  dequantize,
  describeCell,
  extremes,
  modelOptions,
  mutationCode,
  positionMeans,
  positionStatusFrom,
  viewCsv,
} from "./landscape";

const idx = (pos: number, aa: string) => pos * N_AA + AA_ORDER.indexOf(aa);

/**
 * "MKW": three residues. Measured: M1 has all 19 substitutions, K2 only K2A and K2D, W3 none.
 * Values are chosen so every grid is distinguishable at a glance.
 */
function supervisedProtein(): ProteinFile {
  const p = protein("SUP_HUMAN_Test_2024");
  const L = 3;
  const grid = (f: (pos: number, aa: number) => number | null, decimals = 2) => ({
    decimals,
    values: Array.from({ length: L * N_AA }, (_, i) => f(Math.floor(i / N_AA), i % N_AA)),
  });
  const wt = (pos: number, aa: number) => "MKW"[pos] === AA_ORDER[aa];
  const measured = (pos: number, aa: number) =>
    wt(pos, aa)
      ? null
      : pos === 0
        ? -(aa + 1) * 10
        : pos === 1 && (aa === 0 || aa === 2)
          ? 50
          : null;
  const pred = (pos: number, aa: number) => (wt(pos, aa) ? null : (pos * 100 + aa) * 1);
  p.assay = { ...p.assay, target_seq: "MKW", length: L, n_measured_variants: 21 };
  p.measured = grid(measured);
  p.zero_shot = {
    esm2_650m: {
      ...p.zero_shot.esm2_650m!,
      scores: grid((pos, aa) => (wt(pos, aa) ? 0 : -aa * 100), 3),
    },
    saprot_650m_af2: {
      hf_id: "westlake-repl/SaProt_650M_AF2",
      revision: "r",
      region: "full",
      scores: grid((pos, aa) => (wt(pos, aa) ? 0 : aa * 50), 3),
    },
  };
  p.default = {
    model: "ridge_seq_struct",
    kind: "supervised",
    label: "Supervised (n = 21)",
    reason: null,
  };
  p.supervised = {
    model: "ridge_seq_struct",
    n_measured: 21,
    levels: [0.8, 0.9],
    prediction: grid(pred),
    lower_80: grid((pos, aa) => (wt(pos, aa) ? null : pred(pos, aa)! - 10)),
    upper_80: grid((pos, aa) => (wt(pos, aa) ? null : pred(pos, aa)! + 10)),
    lower_90: grid((pos, aa) => (wt(pos, aa) ? null : pred(pos, aa)! - 20)),
    upper_90: grid((pos, aa) => (wt(pos, aa) ? null : pred(pos, aa)! + 20)),
    position_status: ["measured", "partially_measured", "unmeasured"],
    calibration_scheme: { codes: ["random", "contiguous"], values: Array(L * N_AA).fill(0) },
    structure_imputed: [false, false, true],
  };
  return p;
}

describe("decoding", () => {
  it("dequantizes fixed-point values and keeps null as NaN, never 0", () => {
    const out = dequantize({ decimals: 3, values: [1650, -2153, null, 0] });
    expect(Array.from(out.slice(0, 2))).toEqual([Math.fround(1.65), Math.fround(-2.153)]);
    expect(Number.isNaN(out[2]!)).toBe(true);
    expect(out[3]).toBe(0);
  });

  it("writes 1-indexed mutation codes from 0-indexed positions", () => {
    expect(mutationCode("A", 0, "G")).toBe("A1G");
    expect(mutationCode("W", 122, "Y")).toBe("W123Y");
  });

  it("maps grid index pos * 20 + aa to AA_ORDER (row-major, ADR 0028)", () => {
    const l = decodeProtein(supervisedProtein());
    expect(AA_ORDER).toBe("ACDEFGHIKLMNPQRSTVWY");
    expect(Array.from(l.wt)).toEqual([
      AA_ORDER.indexOf("M"),
      AA_ORDER.indexOf("K"),
      AA_ORDER.indexOf("W"),
    ]);
    // M1A measured -10 (aa 0), M1C -20: (aa + 1) * -10 at decimals 2.
    expect(l.measured[idx(0, "A")]).toBeCloseTo(-0.1);
    expect(l.measured[idx(0, "C")]).toBeCloseTo(-0.2);
    expect(l.supervised!.prediction[idx(1, "D")]).toBeCloseTo(1.02);
  });

  it("derives position status like the bundle does", () => {
    const l = decodeProtein(supervisedProtein());
    expect(positionStatusFrom(l.measured, l.wt)).toEqual([
      "measured",
      "partially_measured",
      "unmeasured",
    ]);
    expect(l.positionStatus).toEqual(["measured", "partially_measured", "unmeasured"]);
  });
});

describe("model options and labels", () => {
  it("puts the default first and labels supervised with n", () => {
    const options = modelOptions(supervisedProtein());
    expect(options.map((o) => [o.model, o.label, o.isDefault])).toEqual([
      ["ridge_seq_struct", "Supervised (n = 21)", true],
      ["esm2_650m", "Zero-shot", false],
      ["saprot_650m_af2", "Zero-shot", false],
    ]);
  });

  it("never lists the default twice, and has no SaProt without a structure", () => {
    const options = modelOptions(protein());
    expect(options.map((o) => [o.model, o.label, o.isDefault])).toEqual([
      ["esm2_650m", "Zero-shot", true],
    ]);
  });

  it("formats n with thousands separators", () => {
    const p = supervisedProtein();
    p.supervised!.n_measured = 9576;
    expect(modelOptions(p)[0]!.label).toBe("Supervised (n = 9,576)");
  });
});

describe("views", () => {
  it("centres DMS views on the median measured score and zero-shot views on 0", () => {
    const l = decodeProtein(supervisedProtein());
    // Measured: M1 -0.1 ... -1.9 (19 values, no M1M) and K2A, K2D at 0.5: median of 21 values.
    const values = [
      ...Array.from({ length: 20 }, (_, aa) => -(aa + 1) / 10).filter(
        (_, aa) => AA_ORDER[aa] !== "M",
      ),
      0.5,
      0.5,
    ].sort((a, b) => a - b);
    expect(l.dmsCenter).toBeCloseTo(values[10]!);
    const measured = buildView(l, {
      model: "ridge_seq_struct",
      mode: "measured",
      maskUnmeasured: false,
    });
    const sup = buildView(l, {
      model: "ridge_seq_struct",
      mode: "predicted",
      maskUnmeasured: false,
    });
    const zs = buildView(l, { model: "esm2_650m", mode: "predicted", maskUnmeasured: false });
    expect(measured.center).toBe(l.dmsCenter);
    // Predicted and measured share one scale, so switching between them compares like with like.
    expect([sup.center, sup.halfWidth]).toEqual([measured.center, measured.halfWidth]);
    expect(zs.center).toBe(0);
    expect(zs.units).toBe("log_ratio");
    // Every view carries its label: the model's for predictions, "Measured" for the assay's own.
    expect([sup.label, zs.label, measured.label]).toEqual([
      "Supervised (n = 21)",
      "Zero-shot",
      "Measured",
    ]);
  });

  it("never shows the wild type as a value, even where zero-shot stores 0", () => {
    const l = decodeProtein(supervisedProtein());
    const zs = buildView(l, { model: "esm2_650m", mode: "predicted", maskUnmeasured: false });
    expect(Number.isNaN(zs.values[idx(0, "M")]!)).toBe(true);
    expect(Number.isNaN(zs.values[idx(2, "W")]!)).toBe(true);
    expect(zs.values[idx(2, "A")]).toBeCloseTo(0);
  });

  it("masks unmeasured substitutions only in the predicted view", () => {
    const l = decodeProtein(supervisedProtein());
    const masked = buildView(l, {
      model: "saprot_650m_af2",
      mode: "predicted",
      maskUnmeasured: true,
    });
    expect(masked.values[idx(1, "A")]).toBeCloseTo(0);
    expect(Number.isNaN(masked.values[idx(1, "C")]!)).toBe(true);
    expect(Number.isNaN(masked.values[idx(2, "A")]!)).toBe(true);
    const all = buildView(l, {
      model: "saprot_650m_af2",
      mode: "predicted",
      maskUnmeasured: false,
    });
    expect(all.values[idx(1, "C")]).toBeCloseTo(0.05);
  });

  it("averages each residue's shown substitutions, NaN where none", () => {
    const l = decodeProtein(supervisedProtein());
    const measured = buildView(l, {
      model: "ridge_seq_struct",
      mode: "measured",
      maskUnmeasured: false,
    });
    const means = positionMeans(measured, l.length);
    expect(means[1]).toBeCloseTo(0.5);
    expect(Number.isNaN(means[2]!)).toBe(true);
  });

  it("ranks the extremes and breaks ties by position, then amino acid", () => {
    const l = decodeProtein(supervisedProtein());
    const measured = buildView(l, {
      model: "ridge_seq_struct",
      mode: "measured",
      maskUnmeasured: false,
    });
    const { harmful, tolerated } = extremes(measured, 3);
    expect(
      harmful.map((c) => mutationCode(l.sequence[c.position]!, c.position, AA_ORDER[c.aa]!)),
    ).toEqual(["M1Y", "M1W", "M1V"]);
    expect(
      tolerated.map((c) => mutationCode(l.sequence[c.position]!, c.position, AA_ORDER[c.aa]!)),
    ).toEqual(["K2D", "K2A", "M1A"]);
  });

  it("picks decimals from the scale", () => {
    expect(decimalsFor(0.05)).toBe(4);
    expect(decimalsFor(1.5)).toBe(2);
    expect(decimalsFor(40)).toBe(1);
    expect(decimalsFor(5_000_000)).toBe(0);
  });
});

describe("cell details", () => {
  it("gives intervals, the measured value and the out-of-fold prediction for supervised", () => {
    const l = decodeProtein(supervisedProtein());
    const d = cellDetail(l, "ridge_seq_struct", 1, AA_ORDER.indexOf("A"));
    expect(d.code).toBe("K2A");
    expect(d.predicted).toBeCloseTo(1.0);
    expect(
      d.intervals?.map((iv) =>
        [iv.level, iv.lower, iv.upper].map((v) => Math.round(v * 100) / 100),
      ),
    ).toEqual([
      [80, 0.9, 1.1],
      [90, 0.8, 1.2],
    ]);
    expect(d.measured).toBeCloseTo(0.5);
    expect(d.outOfFold).toBeCloseTo(1.0);
    expect(d.positionStatus).toBe("partially_measured");
    expect(d.structureImputed).toBe(false);
    expect(describeCell(d, 2)).toBe(
      "K2A. Supervised (n = 21) Ridge (sequence + structure): 1.00; 80% interval 0.90 to 1.10; 90% interval 0.80 to 1.20; measured 0.50; out-of-fold prediction 1.00; residue 2: some substitutions measured.",
    );
  });

  it("has no intervals for zero-shot, and no out-of-fold value where not measured", () => {
    const l = decodeProtein(supervisedProtein());
    const d = cellDetail(l, "esm2_650m", 2, AA_ORDER.indexOf("C"));
    expect(d.option.label).toBe("Zero-shot");
    expect(d.intervals).toBeNull();
    expect(Number.isNaN(d.measured)).toBe(true);
    expect(d.outOfFold).toBeNull();
    expect(d.structureImputed).toBe(true);
    expect(cellDetail(l, "esm2_650m", 2, AA_ORDER.indexOf("W")).isWildType).toBe(true);
  });

  it("has no imputed-structure flag on an assay without a supervised model", () => {
    const l = decodeProtein(protein());
    expect(cellDetail(l, "esm2_650m", 0, 0).structureImputed).toBeNull();
  });
});

describe("CSV", () => {
  it("writes the shown substitutions with label, intervals and measured value", () => {
    const l = decodeProtein(supervisedProtein());
    const view = buildView(l, {
      model: "ridge_seq_struct",
      mode: "predicted",
      maskUnmeasured: true,
    });
    const lines = viewCsv(l, view, "predicted").trim().split("\n");
    expect(lines[0]).toBe(
      "mutation,position,wt,mt,value,quantity,model,label,lower_80,upper_80,lower_90,upper_90,measured_dms_score",
    );
    // Masked to measured: 19 at M1 + 2 at K2.
    expect(lines).toHaveLength(1 + 21);
    expect(lines).toContain(
      'K2A,2,K,A,1,predicted_dms_score,ridge_seq_struct,"Supervised (n = 21)",0.9,1.1,0.8,1.2,0.5',
    );
  });

  it("labels a zero-shot or measured view as such, without intervals", () => {
    const l = decodeProtein(supervisedProtein());
    const zs = viewCsv(
      l,
      buildView(l, { model: "esm2_650m", mode: "predicted", maskUnmeasured: false }),
      "predicted",
    );
    expect(zs.split("\n")[0]).toBe(
      "mutation,position,wt,mt,value,quantity,model,label,measured_dms_score",
    );
    expect(zs).toContain('W3A,3,W,A,0,log_ratio,esm2_650m,"Zero-shot",');
    expect(zs.trim().split("\n")).toHaveLength(1 + 3 * 19);
    const m = viewCsv(
      l,
      buildView(l, { model: "ridge_seq_struct", mode: "measured", maskUnmeasured: false }),
      "measured",
    );
    expect(m).toContain('K2D,2,K,D,0.5,measured_dms_score,measured,"Measured",0.5');
    expect(m.trim().split("\n")).toHaveLength(1 + 21);
  });
});
