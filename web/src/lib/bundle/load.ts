/**
 * Load bundle files in the browser, from the site's own /data (ADR 0037).
 *
 * Each file is checked the way the API checks it at startup (ADR 0028, 0030):
 * - the schema version is one this code reads, before anything else, so a newer bundle fails
 *   with that message rather than a list of schema errors;
 * - the whole file passes the generated Zod validator (unknown fields included);
 * - a protein file's bytes have the manifest's sha256 (checked on the gzip as stored);
 * - a structure file's bytes have the sha256 its protein file lists, and it models exactly the
 *   target residues the protein file says it does (ADR 0039).
 * A file that fails is an error shown to the reader, never data shown with a warning.
 */
import type { z } from "zod";

import {
  BUNDLE_SCHEMA_VERSION,
  BenchmarksSchema,
  ManifestSchema,
  ProteinFileSchema,
  StructureFileSchema,
  type Benchmarks,
  type Manifest,
  type ManifestEntry,
  type ProteinFile,
  type StructureFile,
} from "./schemas.generated";

export const DATA_BASE = "/data";

export type Fetch = (url: string) => Promise<Response>;

export class BundleLoadError extends Error {
  readonly file: string;

  constructor(file: string, message: string) {
    super(`${file}: ${message}`);
    this.name = "BundleLoadError";
    this.file = file;
  }
}

function isGzip(bytes: Uint8Array): boolean {
  return bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
}

export async function gunzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart])
    .stream()
    .pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function fetchBytes(file: string, fetchFn: Fetch): Promise<Uint8Array> {
  let res: Response;
  try {
    res = await fetchFn(`${DATA_BASE}/${file}`);
  } catch (error) {
    throw new BundleLoadError(file, `could not be fetched (${String(error)})`);
  }
  if (!res.ok) throw new BundleLoadError(file, `HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

/** Version check, then the full validator; `data` is parsed JSON. */
export function validate<S extends z.ZodType>(file: string, data: unknown, schema: S): z.output<S> {
  const version = (data as { schema_version?: unknown } | null)?.schema_version;
  if (version !== BUNDLE_SCHEMA_VERSION) {
    throw new BundleLoadError(
      file,
      `schema version ${JSON.stringify(version)}; this site reads ${BUNDLE_SCHEMA_VERSION}`,
    );
  }
  const result = schema.safeParse(data);
  if (!result.success) {
    const issue = result.error.issues[0];
    const where = issue && issue.path.length > 0 ? ` at ${issue.path.join(".")}` : "";
    throw new BundleLoadError(file, `does not match the bundle schema${where}: ${issue?.message}`);
  }
  return result.data;
}

function parseJson(file: string, bytes: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new BundleLoadError(file, "is not valid JSON");
  }
}

export async function loadManifest(fetchFn: Fetch = fetch): Promise<Manifest> {
  const file = "manifest.json";
  return validate(file, parseJson(file, await fetchBytes(file, fetchFn)), ManifestSchema);
}

export async function loadBenchmarks(fetchFn: Fetch = fetch): Promise<Benchmarks> {
  const file = "benchmarks.json";
  return validate(file, parseJson(file, await fetchBytes(file, fetchFn)), BenchmarksSchema);
}

/**
 * One protein file, checked against its manifest entry.
 *
 * The digest is of the gzip file. If a host served it decoded (`Content-Encoding: gzip`), the
 * bytes would no longer be the ones listed, so that is an error rather than a skipped check.
 */
export async function loadProtein(
  entry: ManifestEntry,
  fetchFn: Fetch = fetch,
): Promise<ProteinFile> {
  const bytes = await fetchBytes(entry.file, fetchFn);
  if (!isGzip(bytes)) throw new BundleLoadError(entry.file, "was not served as gzip bytes");
  if ((await sha256Hex(bytes)) !== entry.sha256) {
    throw new BundleLoadError(entry.file, "sha256 differs from the manifest");
  }
  const protein = validate(
    entry.file,
    parseJson(entry.file, await gunzip(bytes)),
    ProteinFileSchema,
  );
  if (protein.assay.assay_id !== entry.assay_id) {
    throw new BundleLoadError(entry.file, `holds ${protein.assay.assay_id}, not ${entry.assay_id}`);
  }
  return protein;
}

/**
 * A protein's AF2 structure (ADR 0039), checked against the protein file that names it.
 *
 * The digest comes from the protein file, which was itself checked against the manifest. The
 * structure's residues must be the target's residues `start..end`: the bundle builder asserted
 * that, and a structure paired with the wrong protein is an error here too, never a picture.
 */
export async function loadStructure(
  protein: ProteinFile,
  fetchFn: Fetch = fetch,
): Promise<StructureFile> {
  const ref = protein.structure_file;
  if (!ref) throw new BundleLoadError(protein.assay.assay_id, "has no structure");
  const bytes = await fetchBytes(ref.file, fetchFn);
  if (!isGzip(bytes)) throw new BundleLoadError(ref.file, "was not served as gzip bytes");
  if ((await sha256Hex(bytes)) !== ref.sha256) {
    throw new BundleLoadError(ref.file, "sha256 differs from the protein file");
  }
  const structure = validate(
    ref.file,
    parseJson(ref.file, await gunzip(bytes)),
    StructureFileSchema,
  );
  if (structure.sequence !== protein.assay.target_seq.slice(ref.start, ref.end)) {
    throw new BundleLoadError(
      ref.file,
      `is not residues ${ref.start + 1}–${ref.end} of ${protein.assay.assay_id}'s target`,
    );
  }
  return structure;
}
