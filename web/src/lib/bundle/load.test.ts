// @vitest-environment node
// (DecompressionStream and crypto.subtle as in a browser; jsdom lacks them.)
import { describe, expect, it } from "vitest";

import { benchmarks, fakeFetch, gzipJson, manifest, protein } from "@/test/fixtures";

import { BundleLoadError, loadBenchmarks, loadManifest, loadProtein } from "./load";

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
    expect(fetchFn.calls).toEqual(["/data/manifest.json", "/data/benchmarks.json"]);
  });

  it("refuses another schema version before checking anything else", async () => {
    const fetchFn = fakeFetch({ "manifest.json": enc({ schema_version: "2.0.0" }) });
    await expect(loadManifest(fetchFn)).rejects.toThrow(
      'manifest.json: schema version "2.0.0"; this site reads 1.0.0',
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
