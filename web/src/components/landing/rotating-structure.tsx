"use client";

import type { GLViewer } from "3dmol";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { loadManifest, loadProtein, loadStructure } from "@/lib/bundle/load";
import { decodeStructure, toPdb } from "@/lib/explorer/structure";

/** A colour token as hex, for WebGL, which reads no CSS (the tokens are OKLCH). */
function tokenHex(name: string): string {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return "#808080";
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((c) => (c ?? 0).toString(16).padStart(2, "0")).join("")}`;
}

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** The reader's reduced-motion setting, live; "reduced" until known, so nothing moves early. */
function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED).matches,
    () => true,
  );
}

/**
 * The Landing page's example structure: one protein's AF2 backbone as a plain cartoon in the
 * muted ink (an illustration, so no data colour and no legend), turning slowly.
 *
 * Loaded after first paint: the page renders this module only after hydration (`next/dynamic`,
 * `ssr: false`), and it waits for the browser to be idle before fetching the protein file, the
 * structure file and 3Dmol.js, all through `load.ts` like the Explorer. It stops turning, and
 * never starts, when the reader asks for reduced motion, and follows changes to that setting.
 * The rotation state is exposed as `data-spinning` for tests.
 */
export default function RotatingStructure({ assayId, name }: { assayId: string; name: string }) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<GLViewer | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const reduced = usePrefersReducedMotion();
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    let live = true;
    let created: GLViewer | null = null;
    const start = async () => {
      const manifest = await loadManifest();
      const entry = manifest.proteins.find((p) => p.assay_id === assayId);
      if (!entry) throw new Error(`${assayId} is not in the manifest`);
      const protein = await loadProtein(entry);
      const [file, $3Dmol] = await Promise.all([loadStructure(protein), import("3dmol")]);
      if (!live || !host.current) return;
      const structure = decodeStructure(file, protein.structure_file!, protein.assay.length);
      created = $3Dmol.createViewer(host.current, { backgroundAlpha: 0, antialias: true });
      if (!created) throw new Error("no WebGL");
      created.addModel(toPdb(structure), "pdb");
      created.zoomTo();
      // zoomTo fits the bounding sphere; a barrel seen end-on fills the panel better a bit closer.
      created.zoom(1.4);
      created.render();
      viewer.current = created;
      setState("ready");
    };
    const fail = () => {
      if (live) setState("error");
    };
    const run = () => void start().catch(fail);
    // Safari has no requestIdleCallback: a short timeout after hydration does the same job.
    const hasIdle = typeof window.requestIdleCallback === "function";
    const handle = hasIdle
      ? window.requestIdleCallback(run, { timeout: 2000 })
      : window.setTimeout(run, 200);
    return () => {
      live = false;
      if (hasIdle) window.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
      created?.spin(false);
      created?.clear();
      viewer.current = null;
    };
  }, [assayId]);

  // The cartoon's ink follows the theme.
  useEffect(() => {
    const v = viewer.current;
    if (!v || state !== "ready") return;
    v.setStyle({}, { cartoon: { color: tokenHex("--muted-foreground"), arrows: true } });
    v.render();
  }, [state, resolvedTheme]);

  const spinning = state === "ready" && !reduced;
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    // A slow turn about the vertical axis; only while the canvas is on screen.
    v.spin(spinning ? "y" : false, 0.25, true);
  }, [spinning]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const observer = new ResizeObserver(() => viewer.current?.resize().render());
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Decorative: the protein is named in the caption, and nothing here is data.
  return (
    <div
      className="relative h-64 w-full sm:h-80"
      data-testid="landing-structure"
      data-state={state}
      data-spinning={spinning ? "true" : "false"}
    >
      <div
        ref={host}
        className="absolute inset-0"
        role="img"
        aria-label={`AF2 structure of ${name}`}
      />
      {state === "error" ? (
        <p className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
          The structure could not be shown.
        </p>
      ) : null}
    </div>
  );
}
