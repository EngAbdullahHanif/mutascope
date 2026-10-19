/**
 * Download the website bundle at its pinned commit into public/data/ (`pnpm data:fetch`).
 *
 * The site serves the bundle from its own CDN, so it works with the API asleep (ADR 0037). The
 * pin is web/bundle.json (the same commit as `export.publish.revision`). Every file is checked
 * against the bundle's SHA256SUMS of that commit, the manifest's protein digests and the protein
 * files' structure references (ADR 0039) must agree with it, and manifest, benchmarks, every
 * protein file and every structure file must pass the generated Zod validators.
 * Files are written to a staging directory and moved into place only when all of that passes,
 * so a failed or tampered download never leaves a partial bundle behind.
 *
 * `--verify-only` re-checks an existing public/data/ against its SHA256SUMS and pin; a normal
 * run does the same first and downloads only when that check fails.
 */
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

import {
  BUNDLE_SCHEMA_VERSION,
  BenchmarksSchema,
  ManifestSchema,
  ProteinFileSchema,
  StructureFileSchema,
  type ProteinFile,
} from "../src/lib/bundle/schemas.generated.ts";

export const CHECKSUMS = "SHA256SUMS";
export const MANIFEST = "manifest.json";
export const BENCHMARKS = "benchmarks.json";
/** Written next to the files: the pin they were downloaded at. */
export const STAMP = "bundle.json";

/** The JSON Schemas the validators are generated from. */
export const DEFAULT_SCHEMA_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "docs",
  "schema",
  "bundle",
);

export type BundlePin = { repo: string; path: string; tag: string; revision: string };
export type Fetch = (url: string) => Promise<Response>;

export class BundleFetchError extends Error {}

export function sha256Hex(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * `SHA256SUMS` (`<digest>  <path>` per line) as `{path: digest}`.
 *
 * A path that would land outside the bundle directory, a malformed digest or a path listed twice
 * is an error: this list decides which files are written to disk.
 */
export function parseChecksums(text: string): Map<string, string> {
  const sums = new Map<string, string>();
  for (const [i, line] of text.split("\n").entries()) {
    if (line.trim() === "") continue;
    const match = /^([0-9a-f]{64}) [ *]?(.+)$/.exec(line.trimEnd());
    if (!match?.[1] || !match[2]) {
      throw new BundleFetchError(`${CHECKSUMS} line ${i + 1} is malformed`);
    }
    const name = match[2].trim();
    const norm = normalize(name);
    if (norm.startsWith("..") || norm.startsWith(sep) || norm !== name || name.includes("\\")) {
      throw new BundleFetchError(`${CHECKSUMS} line ${i + 1}: unsafe path ${JSON.stringify(name)}`);
    }
    if (sums.has(name)) throw new BundleFetchError(`${CHECKSUMS} lists ${name} twice`);
    sums.set(name, match[1]);
  }
  for (const required of [MANIFEST, BENCHMARKS]) {
    if (!sums.has(required)) throw new BundleFetchError(`${CHECKSUMS} does not list ${required}`);
  }
  return sums;
}

export function checkPin(pin: BundlePin): void {
  if (!/^[0-9a-f]{40}$/.test(pin.revision)) {
    throw new BundleFetchError(`bundle revision ${JSON.stringify(pin.revision)} is not a commit`);
  }
}

export function fileUrl(pin: BundlePin, name: string): string {
  return `https://huggingface.co/datasets/${pin.repo}/resolve/${pin.revision}/${pin.path}/${name}`;
}

async function download(fetchFn: Fetch, url: string, attempts = 3): Promise<Uint8Array> {
  let last: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const res = await fetchFn(url);
      if (res.ok) return new Uint8Array(await res.arrayBuffer());
      last = new BundleFetchError(`${url}: HTTP ${res.status}`);
      // A 4xx will not change on retry: the file is not at that commit.
      if (res.status >= 400 && res.status < 500) break;
    } catch (error) {
      last = error;
    }
    if (attempt < attempts) await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
  }
  throw last instanceof Error ? last : new BundleFetchError(`${url}: ${String(last)}`);
}

function parseJson(name: string, bytes: Uint8Array): unknown {
  const raw = name.endsWith(".gz") ? gunzipSync(bytes) : bytes;
  return JSON.parse(new TextDecoder().decode(raw));
}

/** What a bundle file is, by its path; anything else is only checksummed. */
export function fileRole(
  name: string,
): "manifest" | "benchmarks" | "protein" | "structure" | "schema" | "other" {
  if (name === MANIFEST) return "manifest";
  if (name === BENCHMARKS) return "benchmarks";
  if (/^proteins\/[^/]+\.json\.gz$/.test(name)) return "protein";
  if (/^structures\/[^/]+\.json\.gz$/.test(name)) return "structure";
  if (/^schema\/[^/]+\.schema\.json$/.test(name)) return "schema";
  return "other";
}

/**
 * A JSON Schema shipped in the bundle must be the one the validators were generated from
 * (docs/schema/bundle/, `pnpm schemas`): otherwise the site would check the files against a
 * different contract than the one they were written to.
 */
export async function checkSchemaFile(
  name: string,
  bytes: Uint8Array,
  schemaDir: string,
): Promise<void> {
  const local = join(schemaDir, name.slice("schema/".length));
  if (!existsSync(local)) throw new BundleFetchError(`${name} has no counterpart in ${schemaDir}`);
  const shipped: unknown = JSON.parse(new TextDecoder().decode(bytes));
  const ours: unknown = JSON.parse(await readFile(local, "utf8"));
  if (JSON.stringify(shipped) !== JSON.stringify(ours)) {
    throw new BundleFetchError(`${name} differs from ${local}: regenerate the validators`);
  }
}

/** Schema version first (a clear message for a newer bundle), then the full Zod validator. */
export function validateFile(name: string, bytes: Uint8Array): unknown {
  const data = parseJson(name, bytes);
  const version = (data as { schema_version?: unknown } | null)?.schema_version;
  if (version !== BUNDLE_SCHEMA_VERSION) {
    throw new BundleFetchError(
      `${name} has schema version ${JSON.stringify(version)}; this site reads ${BUNDLE_SCHEMA_VERSION}`,
    );
  }
  const role = fileRole(name);
  const schema =
    role === "manifest"
      ? ManifestSchema
      : role === "benchmarks"
        ? BenchmarksSchema
        : role === "structure"
          ? StructureFileSchema
          : ProteinFileSchema;
  const result = schema.safeParse(data);
  if (!result.success) {
    const first = result.error.issues[0];
    throw new BundleFetchError(
      `${name} does not match the bundle schema: ${first?.path.join(".") ?? ""} ${first?.message ?? ""}`,
    );
  }
  return result.data;
}

/** Every protein file the manifest names is in SHA256SUMS with the same digest. */
export function checkManifest(manifest: unknown, sums: Map<string, string>): number {
  const { proteins } = ManifestSchema.parse(manifest);
  for (const entry of proteins) {
    const listed = sums.get(entry.file);
    if (listed === undefined) {
      throw new BundleFetchError(`${entry.file} is in the manifest but not in ${CHECKSUMS}`);
    }
    if (listed !== entry.sha256) {
      throw new BundleFetchError(`${entry.file}: manifest sha256 differs from ${CHECKSUMS}`);
    }
  }
  return proteins.length;
}

/**
 * A protein file's structure reference names a file in SHA256SUMS with the same digest, so the
 * site's own check of a structure (`loadStructure`) uses the digest the bundle was published with.
 */
export function checkStructureRef(protein: ProteinFile, sums: Map<string, string>): void {
  const ref = protein.structure_file;
  if (!ref) return;
  const listed = sums.get(ref.file);
  if (listed === undefined) {
    throw new BundleFetchError(`${protein.assay.assay_id}: ${ref.file} is not in ${CHECKSUMS}`);
  }
  if (listed !== ref.sha256) {
    throw new BundleFetchError(
      `${protein.assay.assay_id}: ${ref.file} sha256 differs from ${CHECKSUMS}`,
    );
  }
}

/**
 * Run `fn` over `items`, at most `limit` at a time. After the first failure no new item starts,
 * and this returns only when every started call has settled, then throws that first error.
 * (`Promise.all` would reject while other calls were still writing into the staging directory,
 * and the cleanup would race them.)
 */
export async function mapLimit<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  let failure: { error: unknown } | undefined;
  const worker = async () => {
    while (!failure && next < items.length) {
      try {
        await fn(items[next++] as T);
      } catch (error) {
        failure ??= { error };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  if (failure) throw failure.error;
}

/**
 * Download, verify and validate the bundle at `pin` into `outDir`; return the files written.
 *
 * Raises a {@link BundleFetchError} (and leaves `outDir` as it was) when a file is missing, a
 * digest differs, the manifest disagrees with SHA256SUMS or a file fails its schema.
 */
export async function fetchBundle(
  pin: BundlePin,
  outDir: string,
  {
    fetchFn = fetch as Fetch,
    concurrency = 8,
    log = console.log,
    schemaDir = DEFAULT_SCHEMA_DIR,
  }: {
    fetchFn?: Fetch;
    concurrency?: number;
    log?: (msg: string) => void;
    schemaDir?: string;
  } = {},
): Promise<number> {
  checkPin(pin);
  const sumsBytes = await download(fetchFn, fileUrl(pin, CHECKSUMS));
  const sums = parseChecksums(new TextDecoder().decode(sumsBytes));
  const staging = `${outDir}.staging`;
  await rm(staging, { recursive: true, force: true });
  await mkdir(staging, { recursive: true });
  try {
    let done = 0;
    let manifest: unknown;
    await mapLimit([...sums.entries()], concurrency, async ([name, digest]) => {
      const bytes = await download(fetchFn, fileUrl(pin, name));
      if (sha256Hex(bytes) !== digest) {
        throw new BundleFetchError(`${name}: sha256 differs from ${CHECKSUMS}`);
      }
      const role = fileRole(name);
      if (role === "schema") await checkSchemaFile(name, bytes, schemaDir);
      else if (role !== "other") {
        const data = validateFile(name, bytes);
        if (role === "manifest") manifest = data;
        if (role === "protein") checkStructureRef(data as ProteinFile, sums);
      }
      const dest = join(staging, name);
      await mkdir(dirname(dest), { recursive: true });
      await writeFile(dest, bytes);
      done++;
      if (done % 50 === 0) log(`  ${done}/${sums.size} files verified`);
    });
    const proteins = checkManifest(manifest, sums);
    await writeFile(join(staging, CHECKSUMS), sumsBytes);
    await writeFile(join(staging, STAMP), `${JSON.stringify(pin, null, 2)}\n`);
    await rm(outDir, { recursive: true, force: true });
    await rename(staging, outDir);
    log(
      `bundle ${pin.tag} (${pin.revision.slice(0, 7)}): ${sums.size} files, ${proteins} proteins`,
    );
    return sums.size;
  } catch (error) {
    // Keep the original error even if the cleanup fails.
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
}

/**
 * Re-check a downloaded bundle: same pin, every SHA256SUMS file present with its digest, and the
 * manifest agreeing with the list. Returns the number of files checked.
 */
export async function verifyLocal(pin: BundlePin, outDir: string): Promise<number> {
  const stampPath = join(outDir, STAMP);
  if (!existsSync(stampPath)) throw new BundleFetchError(`${outDir} has no ${STAMP}`);
  const stamp = JSON.parse(await readFile(stampPath, "utf8")) as Partial<BundlePin>;
  if (stamp.revision !== pin.revision || stamp.repo !== pin.repo || stamp.path !== pin.path) {
    throw new BundleFetchError(`${outDir} holds ${stamp.revision ?? "?"}, not ${pin.revision}`);
  }
  const sums = parseChecksums(await readFile(join(outDir, CHECKSUMS), "utf8"));
  for (const [name, digest] of sums) {
    const path = join(outDir, name);
    if (!existsSync(path)) throw new BundleFetchError(`${name} is listed but missing`);
    if (sha256Hex(await readFile(path)) !== digest) {
      throw new BundleFetchError(`${name}: sha256 differs from ${CHECKSUMS}`);
    }
  }
  const manifestBytes = await readFile(join(outDir, MANIFEST));
  checkManifest(validateFile(MANIFEST, manifestBytes), sums);
  return sums.size;
}

async function main(): Promise<void> {
  const here = dirname(fileURLToPath(import.meta.url));
  const pin = JSON.parse(await readFile(join(here, "..", "bundle.json"), "utf8")) as BundlePin;
  const outDir = join(here, "..", "public", "data");
  if (process.argv.includes("--verify-only")) {
    const n = await verifyLocal(pin, outDir);
    console.log(`public/data: ${n} files match ${CHECKSUMS} at ${pin.revision.slice(0, 7)}`);
    return;
  }
  try {
    const n = await verifyLocal(pin, outDir);
    console.log(`public/data: ${n} files already match ${pin.tag}; not downloading`);
    return;
  } catch (error) {
    if (existsSync(outDir)) console.log(`public/data is stale (${(error as Error).message})`);
  }
  console.log(`downloading bundle ${pin.tag} from ${pin.repo}`);
  await fetchBundle(pin, outDir);
  await verifyLocal(pin, outDir);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error: unknown) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
