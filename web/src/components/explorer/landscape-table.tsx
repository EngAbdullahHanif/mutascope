"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { AA_ORDER, N_AA, formatValue, type Landscape, type View } from "@/lib/explorer/landscape";

const PAGE = 25;

/**
 * The heatmap as a table: one row per residue, one column per amino acid, a page of residues
 * at a time (a 3,423-residue protein would be 68,000 cells in the DOM). The same numbers the
 * canvas colours, for screen readers and for reading values exactly.
 */
export function LandscapeTable({ landscape, view }: { landscape: Landscape; view: View }) {
  const [page, setPage] = useState(0);
  const pages = Math.ceil(landscape.length / PAGE);
  const from = page * PAGE;
  const to = Math.min(landscape.length, from + PAGE);
  return (
    <div className="flex flex-col gap-2" data-testid="landscape-table">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Button variant="outline" size="xs" onClick={() => setPage(page - 1)} disabled={page === 0}>
          Previous
        </Button>
        <Button
          variant="outline"
          size="xs"
          onClick={() => setPage(page + 1)}
          disabled={page >= pages - 1}
        >
          Next
        </Button>
        <span className="text-muted-foreground" aria-live="polite">
          Residues {from + 1}–{to} of {landscape.length}
        </span>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <table className="tabular w-full font-mono text-xs">
          <caption className="p-2 text-left font-sans text-sm text-muted-foreground">
            {view.title}, {view.label}: residues {from + 1}–{to}. “wt” is the wild type, “—” no
            value.
          </caption>
          <thead>
            <tr className="border-b bg-muted">
              <th scope="col" className="px-2 py-1 text-left font-sans font-medium">
                Residue
              </th>
              {Array.from(AA_ORDER, (aa) => (
                <th key={aa} scope="col" className="px-1.5 py-1 text-right font-medium">
                  {aa}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: to - from }, (_, k) => {
              const pos = from + k;
              return (
                <tr key={pos} className="border-b last:border-0">
                  <th scope="row" className="px-2 py-0.5 text-left font-medium whitespace-nowrap">
                    {landscape.sequence[pos]}
                    {pos + 1}
                  </th>
                  {Array.from({ length: N_AA }, (_, aa) => (
                    <td key={aa} className="px-1.5 py-0.5 text-right whitespace-nowrap">
                      {landscape.wt[pos] === aa
                        ? "wt"
                        : formatValue(view.values[pos * N_AA + aa]!, view.decimals)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
