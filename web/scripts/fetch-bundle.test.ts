// @vitest-environment node
/**
 * The download refuses anything that does not match the pinned SHA256SUMS, and leaves
 * public/data as it was when it does. CI runs these (pnpm test) and then the real download.
 */
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

import {
  benchmarks,
  fakeFetch,
  gzipJson,
  manifest,
  protein,
  sha256,
  structure,
  structureRef,
} from "@/test/fixtures";

import {
  BundleFetchError,
  DEFAULT_SCHEMA_DIR,
  type BundlePin,
  fetchBundle,
  parseChecksums,
  verifyLocal,
} from "./fetch-bundle";

const pin: BundlePin = { repo: "o/r", path: "bundle", tag: "bundle-v0", revision: "a".repeat(40) };
const enc = (v: unknown) => new TextEncoder().encode(JSON.stringify(v));
const quiet = () => {};

/** A consistent mini bundle; `edit` changes files after SHA256SUMS is written. */
function bundleFiles(
  edit: (files: Record<string, Uint8Array>) => void = () => {},
): Record<string, Uint8Array | string> & { SHA256SUMS: string } {
  const gz = gzipJson(protein());
  const files: Record<string, Uint8Array> = {
    "manifest.json": enc(manifest(gz)),
    "benchmarks.json": enc(benchmarks()),
    "proteins/TEST_HUMAN_Example_2024.json.gz": gz,
    "schema/manifest.schema.json": readFileSync(join(DEFAULT_SCHEMA_DIR, "manifest.schema.json")),
  };
  const sums = Object.entries(files)
    .map(([name, bytes]) => `${sha256(bytes)}  ${name}`)
    .join("\n");
  edit(files);
  return { ...files, SHA256SUMS: `${sums}\n` };
}

let out: string;
beforeEach(() => {
  out = join(mkdtempSync(join(tmpdir(), "bundle-")), "data");
});

async function existingBundle() {
  await mkdir(out, { recursive: true });
  writeFileSync(join(out, "previous.txt"), "kept");
}

describe("fetchBundle", () => {
  it("downloads every listed file from the pinned commit and verifies them", async () => {
    const fetchFn = fakeFetch(bundleFiles());
    expect(await fetchBundle(pin, out, { fetchFn, log: quiet })).toBe(4);
    expect(fetchFn.calls[0]).toBe(
      `https://huggingface.co/datasets/o/r/resolve/${"a".repeat(40)}/bundle/SHA256SUMS`,
    );
    expect(existsSync(join(out, "proteins/TEST_HUMAN_Example_2024.json.gz"))).toBe(true);
    expect(await verifyLocal(pin, out)).toBe(4);
  });

  it("refuses a file whose digest differs, and keeps the previous data", async () => {
    await existingBundle();
    const files = bundleFiles((f) => {
      f["benchmarks.json"] = enc({ ...benchmarks(), deployed_model: "tampered" });
    });
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      "benchmarks.json: sha256 differs from SHA256SUMS",
    );
    expect(readFileSync(join(out, "previous.txt"), "utf8")).toBe("kept");
    expect(existsSync(`${out}.staging`)).toBe(false);
  });

  it("reports the first failure and cleans up only after in-flight downloads finish", async () => {
    // Regression: one bad file used to reject at once while other workers were still writing
    // into staging, so the cleanup raced them (ENOTEMPTY) and hid the real error.
    const files = bundleFiles((f) => {
      f["benchmarks.json"] = enc({ ...benchmarks(), deployed_model: "tampered" });
    });
    const served = fakeFetch(files);
    const slow = async (url: string) => {
      if (!url.endsWith("/benchmarks.json") && !url.endsWith("/SHA256SUMS")) {
        await new Promise((r) => setTimeout(r, 50));
      }
      return served(url);
    };
    await expect(fetchBundle(pin, out, { fetchFn: slow, log: quiet })).rejects.toThrow(
      "benchmarks.json: sha256 differs from SHA256SUMS",
    );
    await new Promise((r) => setTimeout(r, 150));
    expect(existsSync(`${out}.staging`)).toBe(false);
  });

  it("refuses a listed file that cannot be downloaded", async () => {
    const files = bundleFiles();
    delete files["proteins/TEST_HUMAN_Example_2024.json.gz"];
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      /HTTP 404/,
    );
    expect(existsSync(out)).toBe(false);
  });

  it("refuses a manifest whose protein digest disagrees with SHA256SUMS", async () => {
    const gzOther = gzipJson(protein("TEST_HUMAN_Example_2024"));
    const files = bundleFiles((f) => {
      const m = manifest(gzOther);
      m.proteins[0]!.sha256 = "b".repeat(64);
      f["manifest.json"] = enc(m);
    });
    // Re-list the edited manifest so only the manifest-vs-list check can fail.
    files.SHA256SUMS = files.SHA256SUMS.replace(
      /^[0-9a-f]{64}  manifest\.json$/m,
      `${sha256(files["manifest.json"] as Uint8Array)}  manifest.json`,
    );
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      "manifest sha256 differs from SHA256SUMS",
    );
  });

  it("refuses a shipped JSON Schema that is not the one the validators came from", async () => {
    const files = bundleFiles((f) => {
      f["schema/manifest.schema.json"] = enc({ changed: true });
    });
    files.SHA256SUMS = files.SHA256SUMS.replace(
      /^[0-9a-f]{64}  schema\/manifest\.schema\.json$/m,
      `${sha256(files["schema/manifest.schema.json"] as Uint8Array)}  schema/manifest.schema.json`,
    );
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      /differs from .*manifest\.schema\.json: regenerate the validators/,
    );
  });

  it("refuses a revision that is not a full commit hash", async () => {
    const fetchFn = fakeFetch(bundleFiles());
    await expect(
      fetchBundle({ ...pin, revision: "bundle-v1.0.0" }, out, { fetchFn, log: quiet }),
    ).rejects.toThrow("is not a commit");
    expect(fetchFn.calls).toEqual([]);
  });
});

describe("structure files (ADR 0039)", () => {
  /** A bundle whose protein points to a structure file; `edit` runs before SHA256SUMS. */
  function withStructure(
    edit: (files: Record<string, Uint8Array>) => void = () => {},
    shipped: unknown = structure(),
  ): Record<string, Uint8Array | string> & { SHA256SUMS: string } {
    const structureGz = gzipJson(shipped);
    const p = { ...protein(), structure_file: structureRef(structureGz) };
    const gz = gzipJson(p);
    const files: Record<string, Uint8Array> = {
      "manifest.json": enc(manifest(gz)),
      "benchmarks.json": enc(benchmarks()),
      "proteins/TEST_HUMAN_Example_2024.json.gz": gz,
      "structures/TEST_HUMAN.json.gz": structureGz,
    };
    edit(files);
    const sums = Object.entries(files)
      .map(([name, bytes]) => `${sha256(bytes)}  ${name}`)
      .join("\n");
    return { ...files, SHA256SUMS: `${sums}\n` };
  }

  it("downloads and validates them", async () => {
    expect(await fetchBundle(pin, out, { fetchFn: fakeFetch(withStructure()), log: quiet })).toBe(
      4,
    );
    expect(existsSync(join(out, "structures/TEST_HUMAN.json.gz"))).toBe(true);
  });

  it("refuses a structure file that does not match its schema", async () => {
    const files = withStructure(() => {}, { ...structure(), sequence: "MX" });
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      "structures/TEST_HUMAN.json.gz does not match the bundle schema: sequence",
    );
  });

  it("refuses a protein whose structure reference disagrees with SHA256SUMS", async () => {
    const files = withStructure((f) => {
      f["structures/TEST_HUMAN.json.gz"] = gzipJson(structure("MK", { plddt: [50, 60] }));
    });
    await expect(fetchBundle(pin, out, { fetchFn: fakeFetch(files), log: quiet })).rejects.toThrow(
      "TEST_HUMAN_Example_2024: structures/TEST_HUMAN.json.gz sha256 differs from SHA256SUMS",
    );
    const missing = withStructure((f) => {
      delete f["structures/TEST_HUMAN.json.gz"];
    });
    await expect(
      fetchBundle(pin, out, { fetchFn: fakeFetch(missing), log: quiet }),
    ).rejects.toThrow("structures/TEST_HUMAN.json.gz is not in SHA256SUMS");
  });
});

describe("verifyLocal", () => {
  it("catches a file changed after download, and a different pin", async () => {
    await fetchBundle(pin, out, { fetchFn: fakeFetch(bundleFiles()), log: quiet });
    await expect(verifyLocal({ ...pin, revision: "c".repeat(40) }, out)).rejects.toThrow(
      `not ${"c".repeat(40)}`,
    );
    writeFileSync(join(out, "benchmarks.json"), "{}");
    await expect(verifyLocal(pin, out)).rejects.toThrow("benchmarks.json: sha256 differs");
  });

  it("fails when nothing was downloaded", async () => {
    await expect(verifyLocal(pin, out)).rejects.toBeInstanceOf(BundleFetchError);
  });
});

describe("parseChecksums", () => {
  const d = "f".repeat(64);
  const base = `${d}  manifest.json\n${d}  benchmarks.json\n`;

  it("parses digest/path lines", () => {
    expect(parseChecksums(`${base}${d}  proteins/x.json.gz\n`).get("proteins/x.json.gz")).toBe(d);
  });

  it.each([["../escape.txt"], ["/etc/passwd"], ["proteins/../../x"], ["a\\b"]])(
    "refuses the unsafe path %s",
    (path) => {
      expect(() => parseChecksums(`${base}${d}  ${path}\n`)).toThrow(/unsafe path/);
    },
  );

  it("refuses malformed lines, duplicates and a list without the manifest", () => {
    expect(() => parseChecksums(`${base}nothex  x\n`)).toThrow(/malformed/);
    expect(() => parseChecksums(`${base}${d}  manifest.json\n`)).toThrow(/twice/);
    expect(() => parseChecksums(`${d}  benchmarks.json\n`)).toThrow(/manifest\.json/);
  });
});
