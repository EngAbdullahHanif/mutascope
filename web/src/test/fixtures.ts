/**
 * Small bundle files that match the generated schemas, for unit tests. The real bundle is
 * downloaded at build time and never committed; these stand in for it.
 */
import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";

import type {
  Benchmarks,
  Manifest,
  ProteinFile,
  VersionStamp,
} from "@/lib/bundle/schemas.generated";

export const version: VersionStamp = {
  schema_version: "1.0.0",
  mutascope_version: "0.1.0",
  code_version: "0".repeat(40),
  proteingym_release: "v1.3",
  dataset_repo: "example/dataset",
  dataset_revision: "1".repeat(40),
  model_repo: "example/model",
  model_revision: "2".repeat(40),
  supervised: { code_version: "3".repeat(40), config_sha256: "a", manifest_sha256: "b" },
};

const L = 2;
const grid = (fill: number | null) => ({ decimals: 2, values: Array(L * 20).fill(fill) });

export function protein(assayId = "TEST_HUMAN_Example_2024"): ProteinFile {
  return {
    schema_version: "1.0.0",
    assay: {
      assay_id: assayId,
      uniprot_id: "TEST_HUMAN",
      molecule_name: "Test protein",
      source_organism: "Homo sapiens",
      function_type: "Stability",
      selection_assay: "Example",
      target_seq: "MK",
      length: L,
      has_structure: false,
      pdb_file: null,
      pdb_range: null,
      n_measured_variants: 3,
      n_measured_positions: 2,
      n_assays_for_protein: 1,
    },
    default: { model: "esm2_650m", kind: "zero_shot", label: "Zero-shot", reason: "few variants" },
    msa_region: null,
    measured: grid(null),
    structure: null,
    zero_shot: {
      esm2_650m: {
        hf_id: "facebook/esm2_t33_650M_UR50D",
        revision: "r",
        region: "full",
        scores: grid(-12),
      },
    },
    supervised: null,
    version,
  };
}

export function gzipJson(value: unknown): Uint8Array {
  return new Uint8Array(gzipSync(Buffer.from(JSON.stringify(value))));
}

export function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

export function manifest(proteinBytes: Uint8Array, assayId = "TEST_HUMAN_Example_2024"): Manifest {
  return {
    schema_version: "1.0.0",
    version,
    proteins: [
      {
        assay_id: assayId,
        uniprot_id: "TEST_HUMAN",
        molecule_name: "Test protein",
        function_type: "Stability",
        length: L,
        has_structure: false,
        n_measured_variants: 3,
        default_model: "esm2_650m",
        label: "Zero-shot",
        reason: "few variants",
        file: `proteins/${assayId}.json.gz`,
        bytes: proteinBytes.length,
        sha256: sha256(proteinBytes),
      },
    ],
    total_bytes: proteinBytes.length,
  };
}

export function benchmarks(): Benchmarks {
  return {
    schema_version: "1.0.0",
    version,
    deployed_model: "ridge_seq_struct",
    zero_shot_comparator: "esm2_score",
    zero_shot_summary: [],
    zero_shot_shared: [],
    zero_shot_comparisons: [],
    supervised_summary: [],
    supervised_comparisons: [],
    function_types: [],
    caveats: [],
    calibration: [],
    final_coverage: [],
  };
}

/** A `fetch` that serves `files` by URL suffix and 404s everything else. */
export function fakeFetch(files: Record<string, Uint8Array | string>) {
  const calls: string[] = [];
  const fn = async (url: string): Promise<Response> => {
    calls.push(url);
    for (const [name, body] of Object.entries(files)) {
      if (url.endsWith(`/${name}`)) return new Response(body as BodyInit, { status: 200 });
    }
    return new Response("not found", { status: 404 });
  };
  return Object.assign(fn, { calls });
}
