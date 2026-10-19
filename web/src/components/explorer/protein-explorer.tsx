"use client";

import { CircleAlert, Info, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EffectLegend } from "@/components/charts/effect-legend";
import { Button } from "@/components/ui/button";
import { loadManifest, loadProtein } from "@/lib/bundle/load";
import type { ProteinFile } from "@/lib/bundle/schemas.generated";
import {
  buildView,
  decodeProtein,
  formatCount,
  positionMeans,
  proteinSummary,
  viewDescription,
  type ModelOption,
  type ProteinSummary,
  type View,
  type ViewMode,
} from "@/lib/explorer/landscape";

import { Heatmap, type FocusCell, type HeatmapApi } from "./heatmap";
import { LandscapeTable } from "./landscape-table";
import { SidePanel } from "./side-panel";
import { StructurePanel } from "./structure-panel";

/** A residue pointed at in one view and shown in the other (ADR 0039). */
type Linked = { position: number; from: "heatmap" | "structure" };

type State =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; protein: ProteinFile; loadedAt: number };

/**
 * One protein's landscape: loads its bundle file through the checked loader, then draws it.
 *
 * `summary` is read from the same file at build time (ADR 0038 addendum), so the header, the
 * view controls (disabled until the data loads) and the default view's label are in the
 * prerendered HTML: the page's text paints before the data loads, and nothing above the heatmap
 * moves when it arrives. The placeholder holds a screen's height so the footer does not jump.
 */
export function ProteinExplorer({ summary }: { summary: ProteinSummary }) {
  const assayId = summary.assayId;
  const [state, setState] = useState<State>({ state: "loading" });

  useEffect(() => {
    let live = true;
    loadManifest()
      .then((manifest) => {
        const entry = manifest.proteins.find((p) => p.assay_id === assayId);
        if (!entry) throw new Error(`${assayId} is not in the bundle`);
        return loadProtein(entry);
      })
      .then((protein) => {
        if (live) setState({ state: "ready", protein, loadedAt: performance.now() });
      })
      .catch((error: unknown) => {
        if (live) {
          setState({
            state: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => {
      live = false;
    };
  }, [assayId]);

  if (state.state === "loading") {
    const option = summary.options[0]!;
    return (
      <div className="flex min-h-dvh flex-col gap-6">
        <ProteinHeader summary={summary} />
        <ViewOptions options={summary.options} model={option.model} mode="predicted" disabled />
        <ViewLine
          label={option.label}
          description={viewDescription(option, "predicted", summary.nMeasuredVariants)}
          reason={summary.defaultReason}
        />
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <LoaderCircle aria-hidden className="size-4 animate-spin" />
          Loading {assayId}…
        </p>
      </div>
    );
  }
  if (state.state === "error") {
    return (
      <div className="flex flex-col gap-6">
        <ProteinHeader summary={summary} />
        <p className="flex items-center gap-2 text-sm text-destructive" role="alert">
          <CircleAlert aria-hidden className="size-4" />
          Could not load this protein: {state.message}
        </p>
      </div>
    );
  }
  return <Loaded protein={state.protein} loadedAt={state.loadedAt} />;
}

function Loaded({ protein, loadedAt }: { protein: ProteinFile; loadedAt: number }) {
  const landscape = useMemo(() => decodeProtein(protein), [protein]);
  const [model, setModel] = useState(landscape.options[0]!.model);
  const [mode, setMode] = useState<ViewMode>("predicted");
  const [maskUnmeasured, setMaskUnmeasured] = useState(false);
  const [focus, setFocus] = useState<FocusCell | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [linked, setLinked] = useState<Linked | null>(null);
  const heatmapApi = useRef<HeatmapApi>(null);
  // A pointer leaving one view clears only what that view set.
  const linkFrom = useCallback(
    (from: Linked["from"]) => (position: number | null) =>
      setLinked((cur) =>
        position !== null ? { position, from } : cur?.from === from ? null : cur,
      ),
    [],
  );
  const onHeatmapHover = useMemo(() => linkFrom("heatmap"), [linkFrom]);
  const onStructureHover = useMemo(() => linkFrom("structure"), [linkFrom]);
  const onStructureSelect = useCallback((position: number) => {
    heatmapApi.current?.select(position);
  }, []);
  const view = useMemo(
    () => buildView(landscape, { model, mode, maskUnmeasured }),
    [landscape, model, mode, maskUnmeasured],
  );
  const means = useMemo(() => positionMeans(view, landscape.length), [view, landscape.length]);
  const summary = useMemo(() => proteinSummary(protein), [protein]);

  // Timing (ADR 0038): first frame after the data loaded, then each view change.
  const pending = useRef<{ since: number; what: "render" | "switch" } | null>({
    since: loadedAt,
    what: "render",
  });
  const [timing, setTiming] = useState<{ render?: number; switch?: number }>({});
  const change =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      pending.current = { since: performance.now(), what: "switch" };
      set(v);
    };
  const onDrawn = useCallback((_: View, at: number) => {
    const p = pending.current;
    if (!p) return;
    pending.current = null;
    const ms = Math.round((at - p.since) * 10) / 10;
    performance.measure(`mutascope:heatmap-${p.what}`, { start: p.since, end: at });
    setTiming((t) => ({ ...t, [p.what]: ms }));
  }, []);

  return (
    <div className="flex min-h-dvh flex-col gap-6">
      <ProteinHeader summary={summary} />
      <ViewOptions
        options={landscape.options}
        model={model}
        mode={mode}
        maskUnmeasured={maskUnmeasured}
        onModel={change(setModel)}
        onMode={change(setMode)}
        onMask={change(setMaskUnmeasured)}
      />
      <ViewLine
        label={view.label}
        description={viewDescription(view.option, mode, summary.nMeasuredVariants)}
        reason={summary.defaultReason}
      />

      {landscape.msaRegion ? (
        <p
          className="flex max-w-prose gap-2 rounded-md border bg-muted/50 p-3 text-sm"
          data-testid="msa-note"
        >
          <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <span>
            ESM-2&apos;s benchmark numbers for this assay come from residues{" "}
            {landscape.msaRegion.start + 1}–{landscape.msaRegion.end}, its MSA region (marked under
            the ruler). The landscapes here score the full target, so inside that region they differ
            from the benchmark&apos;s scores.
          </span>
        </p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_14rem]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <EffectLegend
              maxAbs={view.halfWidth}
              center={view.center}
              title={view.title}
              decimals={view.decimals}
              clamped
              showMissing
              showWildType
              ends={view.units === "dms" ? ["Less fit", "Assay median", "Fitter"] : undefined}
            />
            <p className="max-w-xs text-xs text-muted-foreground">
              {view.units === "dms"
                ? "DMS scores have no common zero, so white is this assay's median measured score, not the wild type."
                : "White is the wild type's own score (0). Bars: mean over each residue's substitutions."}
            </p>
          </div>
          <Heatmap
            landscape={landscape}
            view={view}
            means={means}
            focus={focus}
            onFocusChange={setFocus}
            onDrawn={onDrawn}
            onHoverPosition={onHeatmapHover}
            linkedPosition={linked?.from === "structure" ? linked.position : null}
            apiRef={heatmapApi}
          />
          <p
            className="text-xs text-muted-foreground"
            data-testid="render-timing"
            data-render-ms={timing.render}
            data-switch-ms={timing.switch}
          >
            {timing.render === undefined ? null : `Drawn ${timing.render} ms after the data loaded`}
            {timing.switch === undefined ? null : ` · last view change ${timing.switch} ms`}
          </p>
        </div>
        <SidePanel landscape={landscape} view={view} mode={mode} onSelect={setFocus} />
      </div>

      {/* After the heatmap's first frame, so the structure never delays it (ADR 0039). */}
      {timing.render === undefined ? null : (
        <StructurePanel
          protein={protein}
          view={view}
          means={means}
          highlight={linked?.position ?? null}
          onHover={onStructureHover}
          onSelect={onStructureSelect}
        />
      )}

      <section className="flex flex-col gap-2">
        <div>
          <Button
            variant="outline"
            size="sm"
            aria-expanded={showTable}
            aria-controls="landscape-table"
            onClick={() => setShowTable(!showTable)}
          >
            {showTable ? "Hide" : "Show"} as a table
          </Button>
        </div>
        {showTable ? (
          <div id="landscape-table">
            <LandscapeTable landscape={landscape} view={view} />
          </div>
        ) : null}
      </section>
    </div>
  );
}

/** Breadcrumb, title and facts: the same before and after the data loads. */
function ProteinHeader({ summary: s }: { summary: ProteinSummary }) {
  return (
    <header className="flex flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        <Link
          href="/explore"
          className="text-primary underline underline-offset-4 hover:decoration-2"
        >
          Explorer
        </Link>{" "}
        / <span className="font-mono">{s.assayId}</span>
      </p>
      <h1 className="text-3xl font-semibold tracking-tight">{s.moleculeName}</h1>
      <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <Fact term="UniProt" value={s.uniprotId} mono />
        <Fact term="Organism" value={s.organism} />
        <Fact term="Function type" value={s.functionType} />
        <Fact term="Length" value={`${formatCount(s.length)} residues`} />
        <Fact
          term="Measured"
          value={`${formatCount(s.nMeasuredVariants)} variants at ${formatCount(s.nMeasuredPositions)} residues`}
        />
      </dl>
    </header>
  );
}

/** Model, predicted / measured and the unmeasured mask; `disabled` until the data loads. */
function ViewOptions({
  options,
  model,
  mode,
  maskUnmeasured = false,
  onModel,
  onMode,
  onMask,
  disabled = false,
}: {
  options: ModelOption[];
  model: string;
  mode: ViewMode;
  maskUnmeasured?: boolean;
  onModel?: (model: string) => void;
  onMode?: (mode: ViewMode) => void;
  onMask?: (mask: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <section aria-label="View options" className="flex flex-wrap items-end gap-x-6 gap-y-3 text-sm">
      <label className="flex max-w-full min-w-0 flex-col gap-1">
        <span className="text-muted-foreground">Model</span>
        <select
          className="h-8 max-w-full rounded-md border bg-background px-2"
          value={model}
          disabled={disabled}
          onChange={(e) => onModel?.(e.target.value)}
          data-testid="model-select"
        >
          {options.map((o) => (
            <option key={o.model} value={o.model}>
              {o.isDefault ? "Default: " : ""}
              {o.name} — {o.label}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="flex flex-col gap-1" disabled={disabled}>
        <legend className="mb-1 text-muted-foreground">Show</legend>
        <div className="flex h-8 overflow-hidden rounded-md border">
          {(["predicted", "measured"] as const).map((m) => (
            <label
              key={m}
              className="flex cursor-pointer items-center px-3 has-checked:bg-accent has-checked:font-medium has-focus-visible:ring-2 has-focus-visible:ring-ring"
            >
              <input
                type="radio"
                name="mode"
                value={m}
                checked={mode === m}
                onChange={() => onMode?.(m)}
                className="sr-only"
              />
              {m === "predicted" ? "Predicted" : "Measured"}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex h-8 items-center gap-2">
        <input
          type="checkbox"
          checked={maskUnmeasured}
          disabled={disabled || mode === "measured"}
          onChange={(e) => onMask?.(e.target.checked)}
          className="size-4 accent-primary"
        />
        <span className={mode === "measured" ? "text-muted-foreground" : undefined}>
          Grey out unmeasured
        </span>
      </label>
    </section>
  );
}

/** The view's label (shown with every prediction), what it is, and why there is no default. */
function ViewLine({
  label,
  description,
  reason,
}: {
  label: string;
  description: string;
  reason: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="rounded-sm border px-2 py-0.5 font-medium" data-testid="model-label">
        {label}
      </span>
      <span className="text-muted-foreground">{description}</span>
      {reason ? (
        <span className="text-muted-foreground" data-testid="default-reason">
          No supervised model: {reason}.
        </span>
      ) : null}
    </div>
  );
}

function Fact({ term, value, mono = false }: { term: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className={mono ? "font-mono" : undefined}>{value}</dd>
    </div>
  );
}
