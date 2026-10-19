"use client";

import { useLayoutEffect, useRef } from "react";

import { EFFECT_MISSING } from "@/lib/color/diverging";
import { coverageColours, isPartial, type Structure } from "@/lib/explorer/structure";

/**
 * The whole target as one strip, coloured like the 3D view; residues the structure does not
 * model are the missing grey, never white (ADR 0039). Shown for the partial structures only:
 * for the others it would repeat the summary bar.
 */
export function CoverageStrip({ structure, colours }: { structure: Structure; colours: string[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const strip = coverageColours(structure, colours);

  useLayoutEffect(() => {
    const c = canvas.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    c.width = strip.length;
    c.height = 1;
    strip.forEach((colour, p) => {
      ctx.fillStyle = colour;
      ctx.fillRect(p, 0, 1, 1);
    });
  }, [strip]);

  if (!isPartial(structure)) return null;
  const n = structure.targetLength.toLocaleString("en-US");
  return (
    <figure className="flex flex-col gap-1 text-xs text-muted-foreground" data-testid="coverage">
      <canvas
        ref={canvas}
        aria-hidden
        className="h-3 w-full rounded-sm border [image-rendering:pixelated]"
      />
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>
          The structure models residues {structure.start + 1}–{structure.end} of {n}.
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block size-3 rounded-sm border"
            style={{ background: EFFECT_MISSING }}
          />
          Not in the structure (no 3D position)
        </span>
      </figcaption>
    </figure>
  );
}
