import type { Metadata } from "next";

import { BundleStatus } from "@/components/bundle-status";
import { PageHeader, Placeholder } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "Explorer" };

export default function ExplorerPage() {
  return (
    <>
      <PageHeader title="Explorer">
        Pick a protein and see the predicted effect of every substitution, linked to its structure.
      </PageHeader>
      <div className="grid gap-6 md:grid-cols-2">
        <Placeholder>The protein picker, heatmap and 3D view arrive in the next tasks.</Placeholder>
        <BundleStatus />
      </div>
    </>
  );
}
