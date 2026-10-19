import type { Metadata } from "next";

import { ProteinExplorer } from "@/components/explorer/protein-explorer";
import { manifestFromDisk, proteinSummaryFromDisk } from "@/lib/bundle/static-params";
import { pageMetadata } from "@/lib/seo";

// Every protein in the pinned bundle is prerendered; any other id is a 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return manifestFromDisk().proteins.map((p) => ({ assayId: p.assay_id }));
}

export async function generateMetadata(props: PageProps<"/explore/[assayId]">): Promise<Metadata> {
  const { assayId } = await props.params;
  const entry = manifestFromDisk().proteins.find((p) => p.assay_id === assayId);
  return pageMetadata({
    title: entry ? `${entry.molecule_name} (${assayId})` : assayId,
    description: entry
      ? `Effect of every single substitution in ${entry.molecule_name}: ${entry.label}.`
      : `Effect of every single substitution in ${assayId}.`,
    path: `/explore/${assayId}`,
  });
}

export default async function ProteinPage(props: PageProps<"/explore/[assayId]">) {
  const { assayId } = await props.params;
  // Static text only (header, options, default label); the grids load in the browser.
  return <ProteinExplorer summary={proteinSummaryFromDisk(assayId)} />;
}
