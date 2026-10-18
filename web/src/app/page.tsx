import Link from "next/link";

import { EffectLegend } from "@/components/charts/effect-legend";
import { Button } from "@/components/ui/button";
import { SITE } from "@/lib/site";

export default function HomePage() {
  return (
    <div className="flex flex-col gap-12">
      <section className="max-w-2xl pt-6">
        <h1 className="text-4xl font-semibold tracking-tight">
          Mutation landscapes for every ProteinGym assay
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">{SITE.tagline}</p>
        <div className="mt-6 flex gap-3">
          <Button asChild>
            <Link href="/explorer">Open the Explorer</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/how-it-works">How it works</Link>
          </Button>
        </div>
      </section>
      <section aria-labelledby="reading" className="grid gap-8 md:grid-cols-2">
        <div>
          <h2 id="reading" className="text-xl font-semibold tracking-tight">
            Reading a landscape
          </h2>
          <p className="mt-2 max-w-prose text-muted-foreground">
            Each cell is one substitution. Colour shows the predicted effect in the assay, on a
            scale symmetric around zero. Every prediction is labelled{" "}
            <span className="font-medium text-foreground">Zero-shot</span> or{" "}
            <span className="font-medium text-foreground">Supervised (n = …)</span>, with n the
            measured variants the model was trained on.
          </p>
        </div>
        <EffectLegend maxAbs={1} title="Predicted effect (example scale)" showMissing />
      </section>
    </div>
  );
}
