import type { Metadata } from "next";

import { PageHeader, Placeholder } from "@/components/layout/page-header";

export const metadata: Metadata = { title: "How it works" };

export default function HowItWorksPage() {
  return (
    <>
      <PageHeader title="How it works">
        From ProteinGym assays to zero-shot and supervised landscapes: the pipeline, the models and
        their limits.
      </PageHeader>
      <Placeholder>The pipeline, model card and limitations arrive in a later task.</Placeholder>
    </>
  );
}
