"use client";

import { useTheme } from "next-themes";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import { lutIndex } from "@/lib/color/diverging";
import {
  AA_ORDER,
  N_AA,
  cellDetail,
  describeCell,
  type Landscape,
  type View,
} from "@/lib/explorer/landscape";
import {
  ZOOM_LEVELS,
  fitZoom,
  rasterize,
  sharedLut,
  tickPositions,
  tickStep,
} from "@/lib/explorer/raster";

import { CellTooltip } from "./cell-tooltip";

/** Geometry in CSS pixels. Rows are fixed; columns follow the zoom. */
const SUMMARY_H = 36;
const RULER_H = 18;
const SEQ_H = 16;
const GAP = 4;
const ROW_H = 14;
export const GRID_TOP = SUMMARY_H + RULER_H + SEQ_H + GAP;
const HEIGHT = GRID_TOP + N_AA * ROW_H;

export type FocusCell = { position: number; aa: number };

type Palette = {
  background: string;
  foreground: string;
  muted: string;
  border: string;
  ring: string;
  mono: string;
};

function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    background: v("--background"),
    foreground: v("--foreground"),
    muted: v("--muted-foreground"),
    border: v("--border"),
    ring: v("--ring"),
    mono: v("--font-jetbrains-mono") || "ui-monospace, monospace",
  };
}

export type HeatmapProps = {
  landscape: Landscape;
  view: View;
  means: Float32Array;
  focus: FocusCell | null;
  onFocusChange: (cell: FocusCell) => void;
  /** Called once per view, with `performance.now()` after its first frame is drawn. */
  onDrawn?: (view: View, at: number) => void;
};

/**
 * The 20 × L mutation landscape on one canvas, sized to the visible window: the scroll area
 * is a wide spacer, and each frame draws only the columns in view (ADR 0038). Above the grid:
 * the per-residue mean bar, a ruler (1-indexed) and the wild-type sequence once columns are
 * wide enough to read it. The wild-type cell of every column carries a mark.
 *
 * Keyboard: the grid takes focus; arrows move one cell, Page Up / Page Down ten residues,
 * Home / End the first and last residue, + / − zoom. The focused cell is described in a live
 * region (and in the tooltip), so the canvas never holds information a screen reader misses.
 */
export function Heatmap({ landscape, view, means, focus, onFocusChange, onDrawn }: HeatmapProps) {
  const L = landscape.length;
  const scroller = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState<number | null>(null);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [hover, setHover] = useState<FocusCell | null>(null);
  const [hasFocus, setHasFocus] = useState(false);
  const { resolvedTheme } = useTheme();
  const anchor = useRef<{ position: number; x: number } | null>(null);
  const drawnView = useRef<View | null>(null);

  const cw = zoom ?? fitZoom(L, width || 1);

  // One pixel per cell, rebuilt only when the view changes.
  const raster = useMemo(() => {
    if (typeof document === "undefined") return null;
    const img = document.createElement("canvas");
    img.width = L;
    img.height = N_AA;
    const ctx = img.getContext("2d");
    if (!ctx) return null;
    ctx.putImageData(new ImageData(rasterize(view, L) as ImageDataArray, L, N_AA), 0, 0);
    return img;
  }, [view, L]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  // After a zoom, keep the anchored residue under the same screen x.
  useLayoutEffect(() => {
    const el = scroller.current;
    const a = anchor.current;
    if (!el || !a) return;
    el.scrollLeft = Math.max(0, (a.position + 0.5) * cw - a.x);
    anchor.current = null;
    setScrollLeft(el.scrollLeft);
  }, [cw]);

  const zoomTo = useCallback(
    (next: number, x?: number) => {
      const el = scroller.current;
      if (!el || next === cw) return;
      const screenX =
        x ?? (focus ? (focus.position + 0.5) * cw - el.scrollLeft : el.clientWidth / 2);
      const position = Math.min(L - 1, Math.max(0, Math.floor((el.scrollLeft + screenX) / cw)));
      anchor.current = { position, x: screenX };
      setZoom(next);
    },
    [cw, focus, L],
  );

  const step = useCallback(
    (direction: 1 | -1, x?: number) => {
      const i = ZOOM_LEVELS.indexOf(cw as (typeof ZOOM_LEVELS)[number]);
      const next = ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, Math.max(0, i + direction))]!;
      zoomTo(next, x);
    },
    [cw, zoomTo],
  );

  // Ctrl / ⌘ + wheel zooms (a pinch on a trackpad arrives as ctrl + wheel). Non-passive, so
  // the page itself does not zoom.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      step(e.deltaY < 0 ? 1 : -1, e.clientX - el.getBoundingClientRect().left);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [step]);

  const draw = useCallback(() => {
    const c = canvas.current;
    if (!c || !raster || width === 0) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(width * dpr) || c.height !== Math.round(HEIGHT * dpr)) {
      c.width = Math.round(width * dpr);
      c.height = Math.round(HEIGHT * dpr);
    }
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const p = readPalette();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = p.background;
    ctx.fillRect(0, 0, width, HEIGHT);

    const first = Math.max(0, Math.floor(scrollLeft / cw));
    const last = Math.min(L, Math.ceil((scrollLeft + width) / cw));
    const x0 = first * cw - scrollLeft;
    const xOf = (pos: number) => x0 + (pos - first) * cw;

    // Grid: the visible slice of the raster, scaled up without smoothing.
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(
      raster,
      first,
      0,
      last - first,
      N_AA,
      x0,
      GRID_TOP,
      (last - first) * cw,
      N_AA * ROW_H,
    );

    // Wild-type mark: the page colour with an ink bar, so it reads as neither an effect nor
    // "no value" in either theme.
    const markW = Math.max(1, Math.round(cw / 3));
    for (let pos = first; pos < last; pos++) {
      const aa = landscape.wt[pos]!;
      if (aa < 0) continue;
      const x = xOf(pos);
      const y = GRID_TOP + aa * ROW_H;
      ctx.fillStyle = p.background;
      ctx.fillRect(x, y, cw, ROW_H);
      ctx.fillStyle = p.foreground;
      ctx.fillRect(x + (cw - markW) / 2, y + 3, markW, ROW_H - 6);
    }

    // Mean effect per residue: signed bars from the neutral line, coloured on the same scale.
    const mid = SUMMARY_H / 2;
    const table = sharedLut();
    const barW = cw >= 4 ? cw - 1 : cw;
    for (let pos = first; pos < last; pos++) {
      const m = means[pos]!;
      if (Number.isNaN(m)) continue;
      const t = Math.max(-1, Math.min(1, (m - view.center) / view.halfWidth));
      const k = lutIndex(m, view.center, view.halfWidth) * 4;
      ctx.fillStyle = `rgb(${table[k]} ${table[k + 1]} ${table[k + 2]})`;
      const h = Math.max(1, Math.abs(t) * (mid - 2));
      ctx.fillRect(xOf(pos), t >= 0 ? mid - h : mid, barW, h);
    }
    ctx.fillStyle = p.border;
    ctx.fillRect(0, mid, width, 1);

    // MSA region (ADR 0012): a bar under the ruler.
    const msa = landscape.msaRegion;
    if (msa && msa.end > first && msa.start < last) {
      const a = Math.max(msa.start, first);
      const b = Math.min(msa.end, last);
      ctx.fillStyle = p.muted;
      ctx.fillRect(xOf(a), SUMMARY_H + RULER_H - 3, (b - a) * cw, 3);
    }

    // Ruler: 1-indexed labels.
    ctx.font = `11px ${p.mono}`;
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    const stepPx = tickStep(cw);
    for (const pos of tickPositions(first, last, stepPx)) {
      const x = xOf(pos) + cw / 2;
      ctx.fillStyle = p.muted;
      ctx.fillRect(Math.round(x), SUMMARY_H + 12, 1, 3);
      ctx.fillText(String(pos + 1), x, SUMMARY_H + 1);
    }

    // Sequence, once letters fit.
    if (cw >= 8) {
      ctx.fillStyle = p.foreground;
      ctx.font = `${Math.min(12, cw)}px ${p.mono}`;
      for (let pos = first; pos < last; pos++) {
        ctx.fillText(landscape.sequence[pos] ?? "", xOf(pos) + cw / 2, SUMMARY_H + RULER_H + 2);
      }
    }

    // Hovered column, then the focused cell on top.
    if (hover && hover.position >= first && hover.position < last) {
      ctx.strokeStyle = p.foreground;
      ctx.lineWidth = 1;
      ctx.strokeRect(xOf(hover.position) + 0.5, GRID_TOP + 0.5, cw - 1, N_AA * ROW_H - 1);
      ctx.strokeRect(
        xOf(hover.position) - 0.5,
        GRID_TOP + hover.aa * ROW_H - 0.5,
        cw + 1,
        ROW_H + 1,
      );
    }
    if (focus && hasFocus && focus.position >= first && focus.position < last) {
      ctx.strokeStyle = p.ring;
      ctx.lineWidth = 2;
      ctx.strokeRect(xOf(focus.position) - 1, GRID_TOP + focus.aa * ROW_H - 1, cw + 2, ROW_H + 2);
    }

    if (drawnView.current !== view) {
      drawnView.current = view;
      onDrawn?.(view, performance.now());
    }
  }, [raster, width, scrollLeft, cw, L, landscape, means, view, hover, focus, hasFocus, onDrawn]);

  // Draw synchronously after layout (the first frame of a view is what we time), and again
  // whenever anything drawn changes. Colours are read from the theme tokens at draw time.
  useLayoutEffect(() => {
    draw();
  }, [draw, resolvedTheme]);

  // Keep the focused cell on screen when the keyboard moves it.
  const reveal = useCallback(
    (cell: FocusCell) => {
      const el = scroller.current;
      if (!el) return;
      const x = cell.position * cw;
      if (x < el.scrollLeft) el.scrollLeft = x;
      else if (x + cw > el.scrollLeft + el.clientWidth) el.scrollLeft = x + cw - el.clientWidth;
    },
    [cw],
  );

  useEffect(() => {
    if (focus) reveal(focus);
  }, [focus, reveal]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const cur = focus ?? { position: 0, aa: 0 };
    let next: FocusCell | null = null;
    switch (e.key) {
      case "ArrowLeft":
        next = { ...cur, position: Math.max(0, cur.position - 1) };
        break;
      case "ArrowRight":
        next = { ...cur, position: Math.min(L - 1, cur.position + 1) };
        break;
      case "ArrowUp":
        next = { ...cur, aa: Math.max(0, cur.aa - 1) };
        break;
      case "ArrowDown":
        next = { ...cur, aa: Math.min(N_AA - 1, cur.aa + 1) };
        break;
      case "PageUp":
        next = { ...cur, position: Math.max(0, cur.position - 10) };
        break;
      case "PageDown":
        next = { ...cur, position: Math.min(L - 1, cur.position + 10) };
        break;
      case "Home":
        next = { ...cur, position: 0 };
        break;
      case "End":
        next = { ...cur, position: L - 1 };
        break;
      case "+":
      case "=":
        step(1);
        break;
      case "-":
      case "_":
        step(-1);
        break;
      default:
        return;
    }
    e.preventDefault();
    if (next) onFocusChange(next);
  };

  const cellAt = (e: MouseEvent<HTMLDivElement>): FocusCell | null => {
    const el = scroller.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const x = e.clientX - rect.left + el.scrollLeft;
    const y = e.clientY - rect.top - GRID_TOP;
    if (y < 0 || y >= N_AA * ROW_H) return null;
    const position = Math.floor(x / cw);
    if (position < 0 || position >= L) return null;
    return { position, aa: Math.floor(y / ROW_H) };
  };

  const shown = hover ?? (hasFocus ? focus : null);
  const detail = shown ? cellDetail(landscape, view.option.model, shown.position, shown.aa) : null;
  const announced = focus
    ? describeCell(
        cellDetail(landscape, view.option.model, focus.position, focus.aa),
        view.decimals,
      )
    : "";

  return (
    <div className="relative" data-testid="heatmap" data-zoom={cw}>
      <div className="flex overflow-hidden rounded-md border bg-background">
        <div aria-hidden className="w-6 shrink-0 border-r font-mono text-xs text-muted-foreground">
          <div style={{ height: GRID_TOP }} />
          {Array.from(AA_ORDER, (aa) => (
            <div
              key={aa}
              className="text-center"
              style={{ height: ROW_H, lineHeight: `${ROW_H}px` }}
            >
              {aa}
            </div>
          ))}
        </div>
        <div
          ref={scroller}
          role="application"
          aria-roledescription="mutation heatmap"
          aria-label={`${view.title}, ${view.label}. ${L} residues by 20 amino acids. Arrow keys move between cells, plus and minus zoom.`}
          tabIndex={0}
          className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onScroll={(e) => setScrollLeft(e.currentTarget.scrollLeft)}
          onKeyDown={onKeyDown}
          onFocus={() => {
            setHasFocus(true);
            // Start at the first column in view, so focusing never scrolls the grid away.
            if (!focus)
              onFocusChange({ position: Math.min(L - 1, Math.ceil(scrollLeft / cw)), aa: 0 });
          }}
          onBlur={() => setHasFocus(false)}
          onPointerMove={(e) => setHover(cellAt(e))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => {
            const cell = cellAt(e);
            if (cell) onFocusChange(cell);
          }}
        >
          <div style={{ width: L * cw, height: HEIGHT }}>
            <canvas
              ref={canvas}
              className="sticky left-0 block"
              style={{ width, height: HEIGHT }}
            />
          </div>
        </div>
      </div>
      {detail && shown ? (
        <CellTooltip
          detail={detail}
          decimals={view.decimals}
          left={24 + (shown.position + 0.5) * cw - scrollLeft}
          top={GRID_TOP + shown.aa * ROW_H}
          containerWidth={width + 24}
        />
      ) : null}
      <p aria-live="polite" className="sr-only" data-testid="heatmap-announcer">
        {hasFocus ? announced : ""}
      </p>
      <ZoomControls zoom={cw} onStep={step} />
    </div>
  );
}

function ZoomControls({ zoom, onStep }: { zoom: number; onStep: (d: 1 | -1) => void }) {
  const i = ZOOM_LEVELS.indexOf(zoom as (typeof ZOOM_LEVELS)[number]);
  return (
    <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
      <button
        type="button"
        className="rounded-md border px-2 py-0.5 hover:bg-accent disabled:opacity-50"
        onClick={() => onStep(-1)}
        disabled={i <= 0}
        aria-label="Zoom out"
      >
        −
      </button>
      <button
        type="button"
        className="rounded-md border px-2 py-0.5 hover:bg-accent disabled:opacity-50"
        onClick={() => onStep(1)}
        disabled={i >= ZOOM_LEVELS.length - 1}
        aria-label="Zoom in"
      >
        +
      </button>
      <span className="tabular font-mono">{zoom} px per residue</span>
      <span className="hidden sm:inline">
        · Ctrl / ⌘ + scroll to zoom, Shift + scroll or swipe to pan
      </span>
    </div>
  );
}
