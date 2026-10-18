"use client";

import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { loadBenchmarks, loadManifest, loadProtein } from "@/lib/bundle/load";
import { SITE } from "@/lib/site";

type Check =
  { state: "loading" } | { state: "ok"; detail: string } | { state: "error"; detail: string };

type Checks = { manifest: Check; benchmarks: Check; protein: Check };

const LOADING: Checks = {
  manifest: { state: "loading" },
  benchmarks: { state: "loading" },
  protein: { state: "loading" },
};

const fail = (error: unknown): Check => ({
  state: "error",
  detail: error instanceof Error ? error.message : String(error),
});

/**
 * Loads the bundle the site was built with, through the same checks every page will use
 * (schema version, Zod, the manifest's sha256), and reports each file: the About page's
 * provenance box, and the end-to-end test's proof that the data path works in a real browser.
 */
export function BundleStatus() {
  const [checks, setChecks] = useState<Checks>(LOADING);

  useEffect(() => {
    let live = true;
    const set = (key: keyof Checks, check: Check) => {
      if (live) setChecks((prev) => ({ ...prev, [key]: check }));
    };
    loadManifest()
      .then(async (manifest) => {
        set("manifest", { state: "ok", detail: `${manifest.proteins.length} assays` });
        // The smallest file: enough to exercise gzip, sha256 and the protein validator.
        const entry = [...manifest.proteins].sort((a, b) => a.bytes - b.bytes)[0];
        if (!entry) throw new Error("the manifest lists no proteins");
        const protein = await loadProtein(entry);
        set("protein", {
          state: "ok",
          detail: `${protein.assay.assay_id} (${protein.assay.length} residues)`,
        });
      })
      .catch((error: unknown) => {
        set("manifest", fail(error));
        set("protein", fail(error));
      });
    loadBenchmarks()
      .then((b) => set("benchmarks", { state: "ok", detail: `${b.zero_shot_summary.length} rows` }))
      .catch((error: unknown) => set("benchmarks", fail(error)));
    return () => {
      live = false;
    };
  }, []);

  return (
    <section
      aria-labelledby="bundle-status"
      className="rounded-lg border bg-card p-6 text-sm shadow-sm"
      data-testid="bundle-status"
    >
      <h2 id="bundle-status" className="font-medium">
        Data bundle{" "}
        <span className="font-mono font-normal text-muted-foreground">
          {SITE.bundle.tag} · {SITE.bundle.revision.slice(0, 7)}
        </span>
      </h2>
      <ul className="mt-3 space-y-2">
        <Row name="manifest.json" check={checks.manifest} />
        <Row name="benchmarks.json" check={checks.benchmarks} />
        <Row name="Sample protein" check={checks.protein} />
      </ul>
    </section>
  );
}

function Row({ name, check }: { name: string; check: Check }) {
  return (
    <li className="flex items-start gap-2" data-state={check.state} data-testid={`check-${name}`}>
      {check.state === "loading" ? (
        <LoaderCircle className="mt-0.5 size-4 animate-spin text-muted-foreground" aria-hidden />
      ) : check.state === "ok" ? (
        <CircleCheck className="mt-0.5 size-4 text-primary" aria-hidden />
      ) : (
        <CircleAlert className="mt-0.5 size-4 text-destructive" aria-hidden />
      )}
      <span>
        <span className="font-medium">{name}</span>{" "}
        <span className="text-muted-foreground">
          {check.state === "loading"
            ? "checking…"
            : check.state === "ok"
              ? `valid: ${check.detail}`
              : `failed: ${check.detail}`}
        </span>
      </span>
    </li>
  );
}
