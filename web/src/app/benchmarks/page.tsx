import type { Metadata } from "next";

import { PageHeader, Placeholder } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "Benchmarks" };

export default function BenchmarksPage() {
  return (
    <>
      <PageHeader title="Benchmarks">
        How well each model ranks variants, aggregated ProteinGym-style with 95% bootstrap
        intervals, and paired comparisons between models.
      </PageHeader>
      <Placeholder>Benchmark tables and figures arrive in a later task.</Placeholder>
    </>
  );
}
