// @vitest-environment node
// (DecompressionStream and crypto.subtle as in a browser; jsdom lacks them.)
import { describe, expect, it } from "vitest";

import {
  benchmarks,
  fakeFetch,
  gzipJson,
  manifest,
  protein,
  structure,
  structureRef,
  staleCacheFetch,
} from "@/test/fixtures";

import pin from "../../../bundle.json";
import {
  BundleLoadError,
  StaleDataError,
  loadBenchmarks,
  loadManifest,
  loadProtein,
  loadStructure,
} from "./load";

const enc = (v: unknown) => new TextEncoder().encode(JSON.stringify(v));

function bundle(proteinValue: unknown = protein()) {
  const gz = gzipJson(proteinValue);
  const m = manifest(gz);
  return { gz, m, entry: m.proteins[0]! };
}

describe("loadManifest / loadBenchmarks", () => {
  it("returns validated files", async () => {
    const { m } = bundle();
    const fetchFn = fakeFetch({ "manifest.json": enc(m), "benchmarks.json": enc(benchmarks()) });
    expect((await loadManifest(fetchFn)).proteins).toHaveLength(1);
    expect((await loadBenchmarks(fetchFn)).deployed_model).toBe("ridge_seq_struct");
    // The pinned revision names the bundle, so a new bundle is a new URL.
    expect(fetchFn.calls).toEqual([
      `/data/manifest.json?v=${pin.revision}`,
      `/data/benchmarks.json?v=${pin.revision}`,
    ]);
  });

  it("refuses another schema version before checking anything else", async () => {
    const fetchFn = fakeFetch({ "manifest.json": enc({ schema_version: "2.0.0" }) });
    await expect(loadManifest(fetchFn)).rejects.toThrow(
      'manifest.json: schema version "2.0.0"; this site reads 1.1.0',
    );
  });

  it("refuses unknown fields (the bundle's models forbid them)", async () => {
    const fetchFn = fakeFetch({ "benchmarks.json": enc({ ...benchmarks(), extra: 1 }) });
    await expect(loadBenchmarks(fetchFn)).rejects.toThrow(/does not match the bundle schema/);
  });

  it("reports HTTP errors and broken JSON as load errors", async () => {
    await expect(loadManifest(fakeFetch({}))).rejects.toThrow("manifest.json: HTTP 404");
    await expect(loadManifest(fakeFetch({ "manifest.json": "{" }))).rejects.toBeInstanceOf(
      BundleLoadError,
    );
  });
});

describe("loadProtein", () => {
  it("checks the digest, unzips and validates", async () => {
    const { gz, entry } = bundle();
    const p = await loadProtein(entry, fakeFetch({ [entry.file]: gz }));
    expect(p.assay.assay_id).toBe(entry.assay_id);
    expect(p.zero_shot.esm2_650m?.scores.values).toHaveLength(40);
  });

  it("refuses bytes whose sha256 differs from the manifest", async () => {
    const { gz, entry } = bundle();
    const tampered = gz.slice();
    const i = tampered.length - 5;
    tampered[i] = tampered[i]! ^ 0xff;
    await expect(loadProtein(entry, fakeFetch({ [entry.file]: tampered }))).rejects.toThrow(
      "sha256 differs from the manifest",
    );
  });

  it("requests the file under its own sha256, once when it matches", async () => {
    const { gz, entry } = bundle();
    const fetchFn = fakeFetch({ [entry.file]: gz });
    await loadProtein(entry, fetchFn);
    expect(fetchFn.calls).toEqual([`/data/${entry.file}?v=${entry.sha256}`]);
    expect(fetchFn.inits).toEqual([undefined]);
  });

  it("retries once past the browser cache when a cached copy has another digest", async () => {
    const { gz, entry } = bundle();
    const stale = gzipJson({ ...protein(), schema_version: "1.0.0" });
    const fetchFn = staleCacheFetch(entry.file, stale, gz);
    const p = await loadProtein(entry, fetchFn);
    expect(p.assay.assay_id).toBe(entry.assay_id);
    expect(fetchFn.inits).toEqual([undefined, { cache: "reload" }]);
  });

  it("is a StaleDataError when the fresh copy differs too, after exactly one retry", async () => {
    const { entry } = bundle();
    const other = gzipJson({ ...protein(), schema_version: "1.0.0" });
    const fetchFn = staleCacheFetch(entry.file, other, other);
    const error = await loadProtein(entry, fetchFn).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(StaleDataError);
    expect(error).toBeInstanceOf(BundleLoadError);
    expect((error as Error).message).toContain("sha256 differs from the manifest");
    expect(fetchFn.inits).toEqual([undefined, { cache: "reload" }]);
  });

  it("refuses a file served decoded, rather than skipping the digest check", async () => {
    const { entry } = bundle();
    await expect(loadProtein(entry, fakeFetch({ [entry.file]: enc(protein()) }))).rejects.toThrow(
      "was not served as gzip bytes",
    );
  });

  it("refuses a valid file for another assay", async () => {
    const { gz, entry } = bundle(protein("OTHER_HUMAN_Example_2024"));
    await expect(loadProtein(entry, fakeFetch({ [entry.file]: gz }))).rejects.toThrow(
      "holds OTHER_HUMAN_Example_2024",
    );
  });

  it("refuses a grid value of the wrong type", async () => {
    const bad = protein();
    (bad.measured.values as unknown[])[0] = 0.5; // fixed-point grids hold integers
    const { gz, entry } = bundle(bad);
    await expect(loadProtein(entry, fakeFetch({ [entry.file]: gz }))).rejects.toThrow(
      /at measured\.values\.0/,
    );
  });
});

describe("loadStructure (ADR 0039)", () => {
  const file = "structures/TEST_HUMAN.json.gz";
  function withStructure(shipped: unknown = structure(), start = 0, end = 2) {
    const gz = gzipJson(shipped);
    return { gz, p: { ...protein(), structure_file: structureRef(gz, start, end) } };
  }

  it("checks the protein file's digest, unzips and validates", async () => {
    const { gz, p } = withStructure();
    const fetchFn = fakeFetch({ [file]: gz });
    const s = await loadStructure(p, fetchFn);
    expect(s.sequence).toBe("MK");
    expect(s.coordinates.values).toHaveLength(2 * 4 * 3);
    expect(fetchFn.calls).toEqual([`/data/${file}?v=${p.structure_file.sha256}`]);
  });

  it("refuses bytes that are not the ones the protein file lists", async () => {
    const { p } = withStructure();
    const other = gzipJson(structure("MK", { plddt: [10, 20] }));
    await expect(loadStructure(p, fakeFetch({ [file]: other }))).rejects.toThrow(
      `${file}: sha256 differs from the protein file`,
    );
  });

  it("retries a stale structure past the cache, then reports it as stale", async () => {
    const { gz, p } = withStructure();
    const stale = gzipJson(structure("MK", { plddt: [10, 20] }));
    const recovered = staleCacheFetch(file, stale, gz);
    expect((await loadStructure(p, recovered)).sequence).toBe("MK");
    expect(recovered.inits).toEqual([undefined, { cache: "reload" }]);
    await expect(loadStructure(p, staleCacheFetch(file, stale, stale))).rejects.toBeInstanceOf(
      StaleDataError,
    );
  });

  it("refuses a structure of other residues than the protein file says", async () => {
    // Target "MK"; a structure of "KM" passes its schema but is not this protein's.
    const { gz, p } = withStructure(structure("KM"));
    await expect(loadStructure(p, fakeFetch({ [file]: gz }))).rejects.toThrow(
      "is not residues 1–2 of TEST_HUMAN_Example_2024's target",
    );
  });

  it("refuses a file that fails its schema, and a protein without a structure", async () => {
    const { gz, p } = withStructure({ ...structure(), extra: 1 });
    await expect(loadStructure(p, fakeFetch({ [file]: gz }))).rejects.toThrow(
      /does not match the bundle schema/,
    );
    await expect(loadStructure(protein(), fakeFetch({}))).rejects.toThrow("has no structure");
  });
});
