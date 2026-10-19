import type { Metadata } from "next";

import { ProteinPicker } from "@/components/explorer/protein-picker";
import { PageHeader } from "@/components/layout/page-header";
import { pageMetadata } from "@/lib/seo";

export const metadata: Metadata = pageMetadata({
  title: "Explorer",
  description:
    "Pick a protein and see the effect of every single substitution in its DMS assay, as a heatmap linked to its 3D structure.",
  path: "/explore",
});

export default function ExplorerPage() {
  return (
    <>
      <PageHeader title="Explorer">
        Pick a protein and see the effect of every single substitution in its DMS assay.
      </PageHeader>
      <ProteinPicker />
    </>
  );
}
