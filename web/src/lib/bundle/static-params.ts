/**
 * The manifest read from disk at build time, only to list the routes to prerender
 * (`/explore/[assayId]`) and their titles. Pages still load every bundle file in the browser
 * through `load.ts`; this reads the same file `pnpm build` just downloaded and verified, and
 * runs the same validator, so a route never exists for a protein the site cannot load.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { validate } from "./load";
import { ManifestSchema, type Manifest } from "./schemas.generated";

let cached: Manifest | null = null;

export function manifestFromDisk(): Manifest {
  if (!cached) {
    const file = "manifest.json";
    const text = readFileSync(join(process.cwd(), "public", "data", file), "utf8");
    cached = validate(file, JSON.parse(text), ManifestSchema);
  }
  return cached;
}
