import type { Metadata } from "next";

import { PageHeader, Placeholder } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "About" };

export default function AboutPage() {
  return (
    <>
      <PageHeader title="About">Who built MutaScope, and why.</PageHeader>
      <Placeholder>About text arrives in a later task.</Placeholder>
    </>
  );
}
