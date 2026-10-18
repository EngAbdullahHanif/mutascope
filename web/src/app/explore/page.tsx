import type { Metadata } from "next";

import { ProteinPicker } from "@/components/explorer/protein-picker";
import { PageHeader } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "Explorer" };

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
