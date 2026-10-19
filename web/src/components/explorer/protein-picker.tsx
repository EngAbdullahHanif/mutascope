"use client";

import { CircleAlert, LoaderCircle, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { loadManifest } from "@/lib/bundle/load";
import type { ManifestEntry } from "@/lib/bundle/schemas.generated";
import { formatCount } from "@/lib/explorer/landscape";
import { filterProteins } from "@/lib/explorer/search";

type State =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; proteins: ManifestEntry[] };

/** Every protein in the bundle, searchable by name, UniProt id, function type and model label. */
export function ProteinPicker() {
  const [state, setState] = useState<State>({ state: "loading" });
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    loadManifest()
      .then((m) => {
        const proteins = [...m.proteins].sort((a, b) => a.assay_id.localeCompare(b.assay_id));
        if (live) setState({ state: "ready", proteins });
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
  }, []);

  const shown = useMemo(
    () => (state.state === "ready" ? filterProteins(state.proteins, query) : []),
    [state, query],
  );

  if (state.state === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <LoaderCircle aria-hidden className="size-4 animate-spin" />
        Loading the protein list…
      </p>
    );
  }
  if (state.state === "error") {
    return (
      <p className="flex items-center gap-2 text-sm text-destructive" role="alert">
        <CircleAlert aria-hidden className="size-4" />
        Could not load the protein list: {state.message}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative w-full max-w-md">
          <span className="sr-only">Search proteins</span>
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Name, UniProt, function type or “zero-shot”"
            className="h-9 w-full rounded-md border bg-background pr-3 pl-8 text-sm placeholder:text-muted-foreground"
            data-testid="protein-search"
          />
        </label>
        <p className="text-sm text-muted-foreground" aria-live="polite" data-testid="protein-count">
          {shown.length} of {state.proteins.length} assays
        </p>
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted text-left">
              <th scope="col" className="px-3 py-2 font-medium">
                Protein
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                UniProt
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Function type
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Length
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Default model
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <tr key={p.assay_id} className="border-b last:border-0 hover:bg-accent">
                <td className="px-3 py-1.5">
                  <Link
                    href={`/explore/${p.assay_id}`}
                    className="font-medium text-primary hover:underline"
                  >
                    {p.molecule_name}
                  </Link>
                  <div className="font-mono text-xs text-muted-foreground">{p.assay_id}</div>
                </td>
                <td className="px-3 py-1.5 font-mono text-xs">{p.uniprot_id}</td>
                <td className="px-3 py-1.5">{p.function_type}</td>
                <td className="tabular px-3 py-1.5 text-right font-mono text-xs">
                  {formatCount(p.length)}
                </td>
                <td className="px-3 py-1.5 whitespace-nowrap">{p.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No assay matches “{query}”.</p>
        ) : null}
      </div>
    </div>
  );
}
