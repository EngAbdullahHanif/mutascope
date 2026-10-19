/**
 * Bundle files read from disk at build time: the files `pnpm build` just downloaded and checked
 * against `SHA256SUMS`, run through the same validator as `load.ts`.
 *
 * - The manifest lists the routes to prerender (`/explore/[assayId]`, the sitemap) and their
 *   titles; a route never exists for a protein the site cannot load. Protein pages still load
 *   their files in the browser.
 * - Each protein page's static text (header, facts, model options, default label) is read from
 *   its protein file, checked like `load.ts` does (the manifest's sha256 on the gzip, then the
 *   validator), and prerendered (ADR 0038 addendum). The grids are not: the page still loads
 *   the file in the browser to draw them.
 * - `benchmarks.json` (one small file) is rendered into the Landing and Benchmarks pages at build
 *   time, so their numbers are in the HTML, readable without script (ADR 0041). A file that fails
 *   the validator fails the build: it is never shown.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gunzipSync } from "node:zlib";

import { proteinSummary, type ProteinSummary } from "@/lib/explorer/landscape";

import { BundleLoadError, validate } from "./load";
import {
  BenchmarksSchema,
  ManifestSchema,
  ProteinFileSchema,
  type Benchmarks,
  type Manifest,
} from "./schemas.generated";

const dataPath = (file: string) => join(process.cwd(), "public", "data", file);

function readJson(file: string): unknown {
  return JSON.parse(readFileSync(dataPath(file), "utf8"));
}

let cached: Manifest | null = null;

export function manifestFromDisk(): Manifest {
  cached ??= validate("manifest.json", readJson("manifest.json"), ManifestSchema);
  return cached;
}

let cachedBenchmarks: Benchmarks | null = null;

export function benchmarksFromDisk(): Benchmarks {
  cachedBenchmarks ??= validate("benchmarks.json", readJson("benchmarks.json"), BenchmarksSchema);
  return cachedBenchmarks;
}

/** One protein page's prerendered text, from its bundle file; a file that fails a check fails the build. */
export function proteinSummaryFromDisk(assayId: string): ProteinSummary {
  const entry = manifestFromDisk().proteins.find((p) => p.assay_id === assayId);
  if (!entry) throw new BundleLoadError(assayId, "is not in the manifest");
  const bytes = readFileSync(dataPath(entry.file));
  if (createHash("sha256").update(bytes).digest("hex") !== entry.sha256) {
    throw new BundleLoadError(entry.file, "sha256 differs from the manifest");
  }
  const protein = validate(
    entry.file,
    JSON.parse(gunzipSync(bytes).toString("utf8")),
    ProteinFileSchema,
  );
  return proteinSummary(protein);
}
