"use client";

import type { GLModel, GLShape, GLViewer } from "3dmol";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";

import { StaleDataAlert } from "@/components/stale-data";
import { loadStructure, StaleDataError } from "@/lib/bundle/load";
import type { ProteinFile } from "@/lib/bundle/schemas.generated";
import { EFFECT_MISSING } from "@/lib/color/diverging";
import { formatValue, mutationCode, type View } from "@/lib/explorer/landscape";
import {
  caOf,
  decodeStructure,
  effectColours,
  indexOfPosition,
  plddtColours,
  positionOfResi,
  resiOfPosition,
  toPdb,
  type ColourBy,
  type Structure,
} from "@/lib/explorer/structure";

import { CoverageStrip } from "./coverage-strip";

type State =
  | { state: "loading" }
  | { state: "error"; message: string; stale: boolean }
  | { state: "ready"; structure: Structure };

export type StructureViewerProps = {
  protein: ProteinFile;
  view: View;
  /** The heatmap's per-residue means (its summary bar). */
  means: Float32Array;
  colourBy: ColourBy;
  /** The residue to highlight: hovered in the heatmap, or in 3D itself. */
  highlight: number | null;
  /** The target position under the pointer in 3D, or null. */
  onHover: (position: number | null) => void;
  /** A residue was clicked in 3D. */
  onSelect: (position: number) => void;
};

/** A colour token as hex, for WebGL, which reads no CSS (`--foreground` is OKLCH). */
function tokenHex(name: string): string {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return "#000000";
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((c) => (c ?? 0).toString(16).padStart(2, "0")).join("")}`;
}

/**
 * The protein's AF2 structure in 3Dmol.js (ADR 0039), loaded after the heatmap: this module and
 * 3Dmol.js are separate chunks, and the structure file is fetched only when this mounts.
 *
 * Residues are coloured like the heatmap's summary bar (same model, centre and clipping) or by
 * pLDDT; residues below the protein file's pLDDT threshold are drawn as a thin trace in both. The
 * structure only has residues `start..end` of the target: a heatmap column outside them has no 3D
 * position, which the caption says.
 *
 * The highlighted residue is exposed as `data-highlighted-position` (0-indexed target position),
 * the residue under the pointer as `data-hovered-position`, so tests read state, not pixels.
 */
export default function StructureViewer({
  protein,
  view,
  means,
  colourBy,
  highlight,
  onHover,
  onSelect,
}: StructureViewerProps) {
  const [state, setState] = useState<State>({ state: "loading" });
  const [hovered, setHovered] = useState<number | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<GLViewer | null>(null);
  const model = useRef<GLModel | null>(null);
  const marker = useRef<GLShape | null>(null);
  const callbacks = useRef({ onHover, onSelect });
  const { resolvedTheme } = useTheme();
  const length = protein.assay.length;

  // 3Dmol.js keeps the handlers it was given; they call the latest props through this ref.
  useEffect(() => {
    callbacks.current = { onHover, onSelect };
  }, [onHover, onSelect]);

  // Load the structure file (checked) and 3Dmol.js together, then build the viewer once.
  useEffect(() => {
    let live = true;
    let created: GLViewer | null = null;
    Promise.all([loadStructure(protein), import("3dmol")])
      .then(([file, $3Dmol]) => {
        if (!live || !host.current) return;
        const structure = decodeStructure(file, protein.structure_file!, length);
        created = $3Dmol.createViewer(host.current, {
          backgroundAlpha: 0,
          hoverDuration: 40,
          antialias: true,
        });
        if (!created) throw new Error("this browser has no WebGL");
        const m = created.addModel(toPdb(structure), "pdb");
        const position = (atom: { resi?: number }) =>
          atom.resi === undefined ? null : positionOfResi(structure, atom.resi);
        created.setHoverable(
          {},
          true,
          (atom: { resi?: number }) => {
            const p = position(atom);
            setHovered(p);
            callbacks.current.onHover(p);
          },
          () => {
            setHovered(null);
            callbacks.current.onHover(null);
          },
        );
        created.setClickable({}, true, (atom: { resi?: number }) => {
          const p = position(atom);
          if (p !== null) callbacks.current.onSelect(p);
        });
        viewer.current = created;
        model.current = m;
        setState({ state: "ready", structure });
      })
      .catch((error: unknown) => {
        if (live) {
          setState({
            state: "error",
            message: error instanceof Error ? error.message : String(error),
            stale: error instanceof StaleDataError,
          });
        }
      });
    return () => {
      live = false;
      created?.clear();
      viewer.current = model.current = marker.current = null;
    };
  }, [protein, length]);

  const structure = state.state === "ready" ? state.structure : null;
  const colours = useMemo(() => {
    if (!structure) return null;
    return colourBy === "plddt" ? plddtColours(structure) : effectColours(structure, means, view);
  }, [structure, colourBy, means, view]);

  // Colour and style: a cartoon, and a thin trace where pLDDT is below the threshold.
  const firstFrame = useRef(true);
  useEffect(() => {
    const v = viewer.current;
    const m = model.current;
    if (!v || !m || !structure || !colours) return;
    const colourOf = (atom: { resi?: number }) => {
      const p = atom.resi === undefined ? null : positionOfResi(structure, atom.resi);
      const i = p === null ? -1 : indexOfPosition(structure, p);
      return i < 0 ? EFFECT_MISSING : colours[i]!;
    };
    const low = structure.residueNumbers.filter((_, i) => structure.lowConfidence[i]);
    m.setStyle({}, { cartoon: { colorfunc: colourOf, arrows: true } });
    if (low.length > 0) {
      m.setStyle(
        { resi: low },
        { cartoon: { colorfunc: colourOf, style: "trace", thickness: 0.15 } },
      );
    }
    if (firstFrame.current) {
      v.zoomTo();
      firstFrame.current = false;
    }
    v.render();
  }, [structure, colours]);

  // The highlighted residue: a sphere on its CA, in the ink colour of the theme.
  useEffect(() => {
    const v = viewer.current;
    if (!v || !structure) return;
    if (marker.current) {
      v.removeShape(marker.current);
      marker.current = null;
    }
    const i = highlight === null ? -1 : indexOfPosition(structure, highlight);
    if (i >= 0) {
      marker.current = v.addSphere({
        center: caOf(structure, i),
        radius: 1.6,
        color: tokenHex("--foreground"),
        opacity: 0.85,
      });
    }
    v.render();
  }, [highlight, structure, resolvedTheme]);

  // Follow the panel's size.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const observer = new ResizeObserver(() => viewer.current?.resize().render());
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const a = protein.assay;
  const highlighted = highlight;
  const caption =
    highlighted === null || !structure
      ? null
      : indexOfPosition(structure, highlighted) < 0
        ? `${mutationCode(a.target_seq[highlighted] ?? "?", highlighted, "")} is outside the structure (it models residues ${structure.start + 1}–${structure.end}).`
        : residueCaption(structure, highlighted, a.target_seq, means[highlighted]!, view);

  return (
    <div
      className="flex flex-col gap-2"
      data-testid="structure-viewer"
      data-state={state.state}
      data-highlighted-position={highlight ?? ""}
      data-hovered-position={hovered ?? ""}
    >
      <div className="relative h-80 overflow-hidden rounded-md border bg-background sm:h-96">
        <div
          ref={host}
          className="absolute inset-0"
          role="img"
          aria-label={`AF2 structure of ${a.molecule_name}, residues ${(protein.structure_file?.start ?? 0) + 1}–${protein.structure_file?.end ?? a.length}, coloured by ${colourBy === "plddt" ? "pLDDT" : view.title}.`}
        />
        {state.state === "loading" ? (
          <p
            className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-muted-foreground"
            role="status"
          >
            <LoaderCircle aria-hidden className="size-4 animate-spin" />
            Loading the structure…
          </p>
        ) : null}
        {state.state === "error" && state.stale ? (
          <StaleDataAlert className="absolute inset-0 justify-center p-4" />
        ) : null}
        {state.state === "error" && !state.stale ? (
          <p
            className="absolute inset-0 flex items-center justify-center gap-2 p-4 text-sm text-destructive"
            role="alert"
          >
            <CircleAlert aria-hidden className="size-4 shrink-0" />
            Could not show the structure: {state.message}
          </p>
        ) : null}
      </div>
      <p className="min-h-5 font-mono text-xs text-muted-foreground" aria-live="off">
        {caption}
      </p>
      {structure && colours ? <CoverageStrip structure={structure} colours={colours} /> : null}
    </div>
  );
}

function residueCaption(
  s: Structure,
  position: number,
  sequence: string,
  mean: number,
  view: View,
): string {
  const i = indexOfPosition(s, position);
  const code = mutationCode(sequence[position] ?? "?", position, "");
  const confidence = s.lowConfidence[i] ? " (low confidence)" : "";
  return `${code} · PDB residue ${resiOfPosition(s, position)} · pLDDT ${s.plddt[i]!.toFixed(1)}${confidence} · mean ${formatValue(mean, view.decimals)}`;
}
