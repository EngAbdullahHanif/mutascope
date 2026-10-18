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
  className?: string;
};

/**
 * Legend for the effect scale: blue = harmful, white = neutral, red = tolerated, symmetric
 * around 0. The gradient is drawn from the same stops, in the same space (OKLab), as the cells.
 */
export function EffectLegend({
  maxAbs,
  title,
  decimals = 2,
  showMissing = false,
  className,
}: EffectLegendProps) {
  const lo = formatSigned(-maxAbs, decimals);
  const hi = formatSigned(maxAbs, decimals);
  return (
    <figure className={cn("w-full max-w-sm text-xs", className)}>
      <figcaption className="mb-1.5 font-medium text-foreground">{title}</figcaption>
      <div
        role="img"
        aria-label={`Colour scale for ${title}: blue is harmful (${lo}), white is neutral (0), red is tolerated (${hi}).`}
        className="h-3 rounded-sm border"
        style={{ backgroundImage: GRADIENT }}
      />
      <div aria-hidden className="tabular mt-1 grid grid-cols-3 font-mono text-muted-foreground">
        <span>{lo}</span>
        <span className="text-center">0</span>
        <span className="text-right">{hi}</span>
      </div>
      <div aria-hidden className="grid grid-cols-3 text-muted-foreground">
        <span>Harmful</span>
        <span className="text-center">Neutral</span>
        <span className="text-right">Tolerated</span>
      </div>
      {showMissing ? (
        <div className="mt-2 flex items-center gap-1.5 text-muted-foreground">
          <span
            aria-hidden
            className="inline-block size-3 rounded-sm border"
            style={{ background: EFFECT_MISSING }}
          />
          Not measured
        </div>
      ) : null}
    </figure>
  );
}
