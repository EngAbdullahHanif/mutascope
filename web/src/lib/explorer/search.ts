import type { ManifestEntry } from "@/lib/bundle/schemas.generated";

/** What the protein picker searches: name, assay id, UniProt id, function type, model label. */
function haystack(e: ManifestEntry): string {
  return [e.molecule_name, e.assay_id, e.uniprot_id, e.function_type, e.label]
    .join(" ")
    .toLowerCase();
}

/**
 * Proteins matching every word of `query` (case-insensitive, any field), in the manifest's
 * order. "p53 zero-shot" finds the P53 assays whose default model is zero-shot.
 */
export function filterProteins(entries: readonly ManifestEntry[], query: string): ManifestEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...entries];
  return entries.filter((e) => {
    const text = haystack(e);
    return words.every((w) => text.includes(w));
  });
}
