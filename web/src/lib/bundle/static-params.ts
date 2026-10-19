/**
 * Bundle files read from disk at build time: the files `pnpm build` just downloaded and checked
 * against `SHA256SUMS`, run through the same validator as `load.ts`.
 *
 * - The manifest lists the routes to prerender (`/explore/[assayId]`, the sitemap) and their
 *   titles; a route never exists for a protein the site cannot load. Protein pages still load
 *   their files in the browser.
 * - `benchmarks.json` (one small file) is rendered into the Landing and Benchmarks pages at build
 *   time, so their numbers are in the HTML, readable without script (ADR 0041). A file that fails
 *   the validator fails the build: it is never shown.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { validate } from "./load";
import {
  BenchmarksSchema,
  ManifestSchema,
  type Benchmarks,
  type Manifest,
} from "./schemas.generated";

function readJson(file: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), "public", "data", file), "utf8"));
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
