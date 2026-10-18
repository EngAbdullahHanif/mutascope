import type { Metadata } from "next";

import { BundleStatus } from "@/components/bundle-status";
import { PageHeader, Placeholder } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "About" };

export default function AboutPage() {
  return (
    <>
      <PageHeader title="About">Who built MutaScope, and why.</PageHeader>
      <div className="grid gap-6 md:grid-cols-2">
        <Placeholder>About text arrives in a later task.</Placeholder>
        <BundleStatus />
      </div>
    </>
  );
}
