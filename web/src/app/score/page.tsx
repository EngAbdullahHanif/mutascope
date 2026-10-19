import type { Metadata } from "next";

import { PageHeader } from "@/components/layout/page-header";
import { LiveScorer } from "@/components/live/live-scorer";

export const metadata: Metadata = { title: "Score a sequence" };

export default function ScorePage() {
  return (
    <>
      <PageHeader title="Score a sequence">
        Paste any protein sequence and get the zero-shot ESM-2 score of every single substitution,
        computed live. Nothing is trained on your sequence, and nothing is kept.
      </PageHeader>
      <LiveScorer />
    </>
  );
}
