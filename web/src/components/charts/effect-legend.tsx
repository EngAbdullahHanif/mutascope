import { EFFECT_MISSING, EFFECT_STOPS } from "@/lib/color/diverging";
import { cn } from "@/lib/utils";

/** Signed number with a true minus sign, so −0.50 and 0.50 line up in a mono face. */
export function formatSigned(value: number, decimals: number): string {
  const text = Math.abs(value).toFixed(decimals);
  if (value < 0 && Number(text) !== 0) return `−${text}`;
  if (value > 0 && Number(text) !== 0) return `+${text}`;
  return text;
}

const GRADIENT = `linear-gradient(in oklab to right, ${EFFECT_STOPS.join(", ")})`;

export type EffectLegendProps = {
  /** Half-width of the symmetric domain: the bar runs from −maxAbs to +maxAbs. */
  maxAbs: number;
  /** What the numbers are, e.g. "ESM-2 650M score" or "DMS score". */
  title: string;
  decimals?: number;
  /** Adds a "Not measured" swatch, for views with missing cells. */
  showMissing?: boolean;
  /** The missing swatch's words; a live sequence has nothing measured, only rows not scored yet. */
  missingLabel?: string;
  /**
   * The neutral (white) value; 0 unless the quantity has no natural zero. DMS scores are
   * centred on the assay's median (ADR 0038), so the bar runs center ± maxAbs.
   */
  center?: number;
  /** Words under the ends and the middle; the effect words by default. */
  ends?: readonly [string, string, string];
  /** The domain is clipped (values beyond it share the end colours): ends read "≤" / "≥". */
  clamped?: boolean;
  /** Adds a "Wild type" swatch: the heatmap's mark for the residue itself. */
  showWildType?: boolean;
  className?: string;
};

const EFFECT_WORDS = ["Harmful", "Neutral", "Tolerated"] as const;

/**
 * Legend for the effect scale: blue = harmful, white = neutral, red = tolerated, symmetric
 * around 0. The gradient is drawn from the same stops, in the same space (OKLab), as the cells.
 */
export function EffectLegend({
  maxAbs,
  title,
  decimals = 2,
  showMissing = false,
  missingLabel = "Not measured",
  center = 0,
  ends = EFFECT_WORDS,
  clamped = false,
  showWildType = false,
  className,
}: EffectLegendProps) {
  // Around 0 the sign is the point ("+1.50" tolerated); around a median it is just a value.
  const fmt = (v: number) =>
    center === 0 ? formatSigned(v, decimals) : formatSigned(v, decimals).replace(/^\+/, "");
  const lo = `${clamped ? "≤ " : ""}${fmt(center - maxAbs)}`;
  const hi = `${clamped ? "≥ " : ""}${fmt(center + maxAbs)}`;
  const mid = center === 0 ? "0" : fmt(center);
  return (
    <figure className={cn("w-full max-w-sm text-xs", className)}>
      <figcaption className="mb-1.5 font-medium text-foreground">{title}</figcaption>
      <div
        role="img"
        aria-label={`Colour scale for ${title}: blue is ${ends[0].toLowerCase()} (${lo}), white is ${ends[1].toLowerCase()} (${mid}), red is ${ends[2].toLowerCase()} (${hi}).`}
        className="h-3 rounded-sm border"
        style={{ backgroundImage: GRADIENT }}
      />
      <div aria-hidden className="tabular mt-1 grid grid-cols-3 font-mono text-muted-foreground">
        <span>{lo}</span>
        <span className="text-center">{mid}</span>
        <span className="text-right">{hi}</span>
      </div>
      <div aria-hidden className="grid grid-cols-3 text-muted-foreground">
        <span>{ends[0]}</span>
        <span className="text-center">{ends[1]}</span>
        <span className="text-right">{ends[2]}</span>
      </div>
      {showMissing || showWildType ? (
        <div className="mt-2 flex items-center gap-4 text-muted-foreground">
          {showMissing ? (
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block size-3 rounded-sm border"
                style={{ background: EFFECT_MISSING }}
              />
              {missingLabel}
            </span>
          ) : null}
          {showWildType ? (
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-flex size-3 items-center justify-center rounded-sm border bg-background"
              >
                <span className="block h-2 w-1 bg-foreground" />
              </span>
              Wild type
            </span>
          ) : null}
        </div>
      ) : null}
    </figure>
  );
}
