import type { ReactNode } from "react";

import { formatValue, positionStatusText, type CellDetail } from "@/lib/explorer/landscape";

const WIDTH = 288;

/** What one cell holds: everything the bundle (or a live result) says about that substitution. */
export function CellTooltip({
  detail: d,
  decimals,
  left,
  top,
  containerWidth,
}: {
  detail: CellDetail;
  decimals: number;
  /** Cell centre, relative to the heatmap frame. */
  left: number;
  top: number;
  containerWidth: number;
}) {
  const f = (v: number) => formatValue(v, decimals);
  // Flip to the cell's left near the right edge, so the tooltip never leaves the frame.
  const x = left + 12 + WIDTH > containerWidth ? Math.max(0, left - 12 - WIDTH) : left + 12;
  return (
    <div
      role="tooltip"
      data-testid="cell-tooltip"
      className="pointer-events-none absolute z-20 rounded-md border bg-popover p-3 text-xs text-popover-foreground shadow-md"
      style={{ left: x, top: Math.max(0, top - 8), width: WIDTH }}
    >
      {d.isWildType ? (
        <p className="font-mono font-medium">
          {d.wt}
          {d.position + 1} · wild type
        </p>
      ) : (
        <>
          <p className="flex items-baseline justify-between gap-2">
            <span className="font-mono text-sm font-semibold">{d.code}</span>
            <span className="font-medium">{d.option.label}</span>
          </p>
          <p className="text-muted-foreground">{d.option.name}</p>
          <dl className="tabular mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <Row term="Predicted">
              {d.live && Number.isNaN(d.predicted) ? "not scored yet" : f(d.predicted)}
            </Row>
            {d.intervals?.map((iv) => (
              <Row key={iv.level} term={`${iv.level}% interval`}>
                {f(iv.lower)} to {f(iv.upper)}
              </Row>
            ))}
            {d.live ? null : (
              <Row term="Measured">{Number.isNaN(d.measured) ? "not measured" : f(d.measured)}</Row>
            )}
            {d.outOfFold !== null ? (
              <Row term="Out-of-fold prediction">{f(d.outOfFold)}</Row>
            ) : null}
          </dl>
        </>
      )}
      {d.live ? null : (
        <p className="mt-2 border-t pt-2 text-muted-foreground">
          Residue {d.position + 1}: {positionStatusText(d.positionStatus)}
          {d.structureImputed === null
            ? null
            : d.structureImputed
              ? ". Structure features imputed (outside the structure)."
              : "."}
        </p>
      )}
    </div>
  );
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-right font-mono whitespace-nowrap">{children}</dd>
    </>
  );
}
