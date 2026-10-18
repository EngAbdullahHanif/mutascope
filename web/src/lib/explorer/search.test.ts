import { describe, expect, it } from "vitest";

import type { ManifestEntry } from "@/lib/bundle/schemas.generated";

import { filterProteins } from "./search";

const entry = (over: Partial<ManifestEntry>): ManifestEntry => ({
  assay_id: "X",
  uniprot_id: "X_HUMAN",
  molecule_name: "X",
  function_type: "Stability",
  length: 10,
  has_structure: true,
  n_measured_variants: 1,
  default_model: "ridge_seq_struct",
  label: "Supervised (n = 1)",
  reason: null,
  file: "proteins/X.json.gz",
  bytes: 1,
  sha256: "0".repeat(64),
  ...over,
});

const entries = [
  entry({
    assay_id: "P53_HUMAN_Giacomelli_2018_Null_Nutlin",
    uniprot_id: "P53_HUMAN",
    molecule_name: "Cellular tumor antigen p53",
    function_type: "OrganismalFitness",
    label: "Zero-shot",
  }),
  entry({
    assay_id: "P53_HUMAN_Kotler_2018",
    uniprot_id: "P53_HUMAN",
    molecule_name: "Cellular tumor antigen p53",
    function_type: "OrganismalFitness",
    label: "Supervised (n = 1,048)",
  }),
  entry({
    assay_id: "GFP_AEQVI_Sarkisyan_2016",
    uniprot_id: "GFP_AEQVI",
    molecule_name: "Green fluorescent protein",
    function_type: "Expression",
    label: "Supervised (n = 1,084)",
  }),
];

describe("filterProteins", () => {
  it("returns everything for an empty query", () => {
    expect(filterProteins(entries, "  ")).toHaveLength(3);
  });

  it("matches name, UniProt id, function type and label, case-insensitively", () => {
    expect(filterProteins(entries, "green").map((e) => e.uniprot_id)).toEqual(["GFP_AEQVI"]);
    expect(filterProteins(entries, "p53_human")).toHaveLength(2);
    expect(filterProteins(entries, "expression")).toHaveLength(1);
    expect(filterProteins(entries, "Zero-shot").map((e) => e.assay_id)).toEqual([
      "P53_HUMAN_Giacomelli_2018_Null_Nutlin",
    ]);
  });

  it("needs every word to match", () => {
    expect(filterProteins(entries, "p53 supervised").map((e) => e.assay_id)).toEqual([
      "P53_HUMAN_Kotler_2018",
    ]);
    expect(filterProteins(entries, "p53 gfp")).toEqual([]);
  });
});
