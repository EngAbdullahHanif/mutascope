import { ArrowRight } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { LandingStructure } from "@/components/landing/landing-structure";
import { Button } from "@/components/ui/button";
import { headlines, interval, modelName, percent, signed } from "@/lib/benchmarks/select";
import { benchmarksFromDisk, manifestFromDisk } from "@/lib/bundle/static-params";
import { pageMetadata } from "@/lib/seo";
import { EXAMPLES, SITE } from "@/lib/site";

// The tab title is the name alone; the Open Graph and Twitter titles keep the longer line.
export const metadata: Metadata = {
  ...pageMetadata({
    title: `${SITE.name}: mutation landscapes for ProteinGym assays`,
    description: `Predicted functional effect of every single amino-acid substitution in ${manifestFromDisk().proteins.length} ProteinGym DMS assays, with an honest benchmark.`,
    path: "/",
    absoluteTitle: true,
  }),
  title: { absolute: SITE.name },
};

/** The example ids, checked against the manifest: a missing one fails the build. */
function examples() {
  const proteins = manifestFromDisk().proteins;
  return EXAMPLES.map((id) => {
    const entry = proteins.find((p) => p.assay_id === id);
    if (!entry) throw new Error(`example ${id} is not in the manifest`);
    if (!entry.has_structure) throw new Error(`example ${id} has no structure`);
    return entry;
  });
}

export default function HomePage() {
  const b = benchmarksFromDisk();
  const h = headlines(b);
  const picks = examples();
  const featured = picks[0]!;
  const nAssays = manifestFromDisk().proteins.length;

  return (
    <div className="flex flex-col gap-14">
      <section className="grid items-center gap-8 pt-2 md:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <div>
          <h1 className="max-w-2xl text-4xl font-semibold tracking-tight">
            See what every mutation does to a protein, and how well models predict it.
          </h1>
          <p className="mt-3 max-w-2xl text-lg text-muted-foreground" data-testid="landing-subline">
            Predicted effects of every single amino-acid substitution in {nAssays} ProteinGym
            assays, tested against real measurements.
          </p>
          <div className="mt-8 flex flex-wrap gap-3" data-testid="landing-actions">
            <Button asChild>
              <Link href="/explore">Open the Explorer</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/benchmarks">See the benchmark</Link>
            </Button>
          </div>
          <div className="mt-10" data-testid="landing-examples">
            <h2 className="text-sm font-medium text-muted-foreground">Start with an example</h2>
            <ul className="mt-2 flex flex-wrap gap-2" data-testid="examples">
              {picks.map((p) => (
                <li key={p.assay_id}>
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/explore/${p.assay_id}`}>
                      {p.molecule_name}
                      <span className="font-mono text-xs text-muted-foreground">
                        {p.uniprot_id}
                      </span>
                    </Link>
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <figure className="rounded-xl border bg-card shadow-sm">
          <LandingStructure assayId={featured.assay_id} name={featured.molecule_name} />
          <figcaption className="border-t px-4 py-2 text-xs text-muted-foreground">
            AF2 structure of {featured.molecule_name} ({featured.assay_id}).{" "}
            <Link
              className="text-primary underline underline-offset-4 hover:decoration-2"
              href={`/explore/${featured.assay_id}`}
            >
              Explore its landscape
            </Link>
          </figcaption>
        </figure>
      </section>

      <section aria-labelledby="headlines" className="mt-6 flex flex-col gap-4">
        <h2 id="headlines" className="text-xl font-semibold tracking-tight">
          What the benchmark shows
        </h2>
        <dl className="grid gap-4 md:grid-cols-3" data-testid="headlines">
          <Stat
            testId="headline-structure"
            term={`${modelName("saprot_650m_af2")} vs ${modelName("esm2_650m")}, zero-shot`}
            value={signed(h.structure.diff)}
            unit="Spearman"
          >
            Adding AF2 structure tokens to the same architecture. Paired 95% CI{" "}
            <span className="font-mono">
              {interval(h.structure.ci_low, h.structure.ci_high, true)}
            </span>
            , {h.structure.n_assays} assays with a structure.
          </Stat>
          <Stat
            testId="headline-supervised"
            term="Supervised gain on unseen positions"
            value={signed(h.supervised.contiguous.diff)}
            unit="Spearman"
          >
            {modelName(b.deployed_model)} − {modelName(b.zero_shot_comparator)}, contiguous split
            (whole blocks of positions held out); paired 95% CI{" "}
            <span className="font-mono">
              {interval(h.supervised.contiguous.ci_low, h.supervised.contiguous.ci_high, true)}
            </span>
            . Modulo split: <span className="font-mono">{signed(h.supervised.modulo.diff)}</span>.{" "}
            {h.supervised.contiguous.n_assays} assays.
          </Stat>
          <Stat
            testId="headline-coverage"
            term={`Coverage of ${percent(h.coverage.level, 0)} intervals`}
            value={percent(h.coverage.mean)}
            unit="mean"
          >
            Share of held-out measurements inside their interval, on test folds the intervals never
            saw (contiguous split, split-matched calibration). {h.coverage.nBelow} of{" "}
            {h.coverage.nAssays} assays more than 10 points below target.
          </Stat>
        </dl>
        <p className="text-sm text-muted-foreground">
          Read from the bundle&apos;s <span className="font-mono">benchmarks.json</span>.{" "}
          <Link
            className="text-primary underline underline-offset-4 hover:decoration-2"
            href="/benchmarks"
          >
            Every chart, split and caveat
          </Link>
          .
        </p>
      </section>

      <section
        aria-labelledby="try"
        className="flex flex-col gap-4 rounded-lg border bg-card p-6 shadow-sm md:flex-row md:items-center md:justify-between"
      >
        <div className="max-w-prose">
          <h2 id="try" className="text-xl font-semibold tracking-tight">
            Try your own sequence
          </h2>
          <p className="mt-1 text-muted-foreground">
            Paste any protein sequence (up to 1,022 residues) and get a zero-shot landscape from
            ESM-2, computed live. The server sleeps when idle, so the first request can take a
            moment.
          </p>
        </div>
        <Button asChild className="shrink-0">
          <Link href="/score">
            Score a sequence <ArrowRight aria-hidden />
          </Link>
        </Button>
      </section>
    </div>
  );
}

function Stat({
  term,
  value,
  unit,
  testId,
  children,
}: {
  term: string;
  value: string;
  unit: string;
  testId: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-lg border bg-card p-5 shadow-sm" data-testid={testId}>
      <dt className="text-sm font-medium">{term}</dt>
      <dd className="mt-2 flex items-baseline gap-2">
        <span className="font-mono text-3xl font-semibold tracking-tight tabular-nums">
          {value}
        </span>
        <span className="text-sm text-muted-foreground">{unit}</span>
      </dd>
      <dd className="mt-2 text-sm text-muted-foreground">{children}</dd>
    </div>
  );
}
