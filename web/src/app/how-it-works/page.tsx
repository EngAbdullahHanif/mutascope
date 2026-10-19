import { ArrowDown, ArrowRight } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { modelName } from "@/lib/benchmarks/select";
import { manifestFromDisk } from "@/lib/bundle/static-params";
import { pageMetadata } from "@/lib/seo";
import { adrUrl, repoFile, SITE } from "@/lib/site";

const DESCRIPTION =
  "How MutaScope is built: ProteinGym data, zero-shot and supervised models, the evaluation protocol, the deployment rule, limitations and the upstream errors found on the way.";

export const metadata: Metadata = pageMetadata({
  title: "How it works",
  description: DESCRIPTION,
  path: "/how-it-works",
});

const STAGES: { name: string; detail: string }[] = [
  { name: "ProteinGym v1.3", detail: "pinned release, sha256-checked" },
  { name: "Validate and clean", detail: "single mutants, wild type checked" },
  { name: "Structures", detail: "AF2 models, pLDDT, Foldseek 3Di" },
  { name: "Zero-shot scores", detail: "ESM-2, SaProt, ensemble" },
  { name: "Feature store", detail: "all 19 × L substitutions" },
  { name: "Supervised CV", detail: "ridge, LightGBM, MLP; 3 splits" },
  { name: "Intervals", detail: "CV+ conformal, 80% and 90%" },
  { name: "Release check", detail: "rebuild equals published files" },
];

const OUTPUTS: { name: string; detail: string }[] = [
  { name: "Bundle on the Hugging Face Hub", detail: "pinned commit, checksums" },
  { name: "This website", detail: "static, reads the bundle" },
  { name: "Live API", detail: "ESM-2 on ONNX, zero-shot only" },
];

function A({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="text-primary underline-offset-4 hover:underline" href={href}>
      {children}
    </a>
  );
}

function Adr({ n }: { n: number }) {
  return <A href={adrUrl(n)}>ADR {String(n).padStart(4, "0")}</A>;
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex max-w-prose flex-col gap-3">
      <h2 id={id} className="text-xl font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function HowItWorksPage() {
  const proteins = manifestFromDisk().proteins;
  const count = (f: (p: (typeof proteins)[number]) => boolean) => proteins.filter(f).length;
  const nAssays = proteins.length;
  const nStructure = count((p) => p.has_structure);
  const nVariants = proteins.reduce((n, p) => n + p.n_measured_variants, 0);
  const nRidge = count((p) => p.default_model === "ridge_seq_struct");
  const nSaprot = count((p) => p.default_model === "saprot_650m_af2");
  const nEsm = count((p) => p.default_model === "esm2_650m");
  const fmt = (n: number) => n.toLocaleString("en-US");

  return (
    <>
      <PageHeader title="How it works">
        From ProteinGym assays to zero-shot and supervised landscapes: the pipeline, the decisions
        behind it, and its limits.
      </PageHeader>

      <div className="flex flex-col gap-12">
        <section aria-labelledby="pipeline" className="flex flex-col gap-4">
          <h2 id="pipeline" className="text-xl font-semibold tracking-tight">
            The pipeline
          </h2>
          <figure className="rounded-lg border bg-card p-4 shadow-sm sm:p-6" data-testid="pipeline">
            <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {STAGES.map((s, i) => (
                <li key={s.name} className="relative flex items-stretch gap-2">
                  <div className="flex-1 rounded-md border bg-background p-3">
                    <div className="font-mono text-xs text-muted-foreground">
                      {String(i + 1).padStart(2, "0")}
                    </div>
                    <div className="font-medium">{s.name}</div>
                    <div className="text-sm text-muted-foreground">{s.detail}</div>
                  </div>
                  {i < STAGES.length - 1 ? (
                    <ArrowRight
                      aria-hidden
                      className="hidden size-4 shrink-0 self-center text-muted-foreground lg:block"
                    />
                  ) : null}
                </li>
              ))}
            </ol>
            <div className="my-3 flex justify-center text-muted-foreground" aria-hidden>
              <ArrowDown className="size-4" />
            </div>
            <ul className="grid gap-2 sm:grid-cols-3">
              {OUTPUTS.map((o) => (
                <li key={o.name} className="rounded-md border border-dashed p-3">
                  <div className="font-medium">{o.name}</div>
                  <div className="text-sm text-muted-foreground">{o.detail}</div>
                </li>
              ))}
            </ul>
            <figcaption className="mt-4 text-sm text-muted-foreground">
              Every step is a <span className="font-mono">mutascope</span> command that reads files
              and writes files; Nextflow chains them (
              <span className="font-mono">make pipeline</span>
              ), and the release is published only if a rebuild matches it (<Adr n={27} />
              ).
            </figcaption>
          </figure>
        </section>

        <Section id="data" title="Data">
          <p>
            ProteinGym&apos;s deep mutational scanning (DMS) benchmark, release v1.3, downloaded at
            a pinned version and checked against sha256 checksums. MutaScope keeps single
            substitutions only: {fmt(nVariants)} measured variants in {fmt(nAssays)} assays. Every
            mutation&apos;s wild-type residue is checked against the target sequence, and a mismatch
            stops the pipeline instead of being dropped.
          </p>
          <p>
            Structures are ProteinGym&apos;s AlphaFold2 models, which match the assay sequences.{" "}
            {fmt(nStructure)} of the {fmt(nAssays)} assays have a usable one; the rest have none, so
            structure-aware models skip them (<Adr n={3} />
            ). A higher <span className="font-mono">DMS_score</span> always means fitter, following
            ProteinGym.
          </p>
        </Section>

        <Section id="models" title="Models">
          <p>
            <strong>Zero-shot</strong> models never see an assay&apos;s measurements. ESM-2 (35M,
            150M and 650M parameters) scores a substitution by masking the residue and comparing the
            log-probabilities of the mutant and the wild type (<Adr n={12} />
            ). SaProt 650M does the same with tokens that combine each amino acid with its local 3D
            shape from the structure (
            <Adr n={15} />
            ). Their ensemble averages the two after standardising each within the assay (
            <Adr n={16} />
            ).
          </p>
          <p>
            <strong>Supervised</strong> models are trained per assay on that assay&apos;s
            measurements, under ProteinGym&apos;s supervised protocol. The ladder runs from one-hot
            ridge regression through ridge on rich features (zero-shot scores, language-model
            embeddings, structure features), LightGBM and a small neural network (<Adr n={19} />,{" "}
            <Adr n={21} />
            ). Each prediction comes with 80% and 90% intervals from CV+, a cross-conformal method (
            <Adr n={24} />
            ).
          </p>
        </Section>

        <Section id="evaluation" title="Evaluation">
          <p>
            The main metric is Spearman correlation within each assay. Assays are aggregated as
            ProteinGym does (per protein, then per function type, then overall), never as a plain
            mean, with 95% intervals from a bootstrap over assays. Two models are compared only by a
            paired bootstrap of their difference on the same assays (<Adr n={11} />, <Adr n={14} />
            ).
          </p>
          <p>
            Before any modelling, the harness had to reproduce ProteinGym&apos;s published ESM-2
            650M result from its own scores, and our ESM-2 650M scores had to agree with theirs in
            every assay (<Adr n={10} />
            ). Supervised models are scored on ProteinGym&apos;s random, modulo and contiguous
            folds; every search for settings stays inside the training folds, and each supervised
            model is compared with zero-shot on exactly the same test folds.
          </p>
          <p>
            <Link className="text-primary underline-offset-4 hover:underline" href="/benchmarks">
              The results are on the Benchmarks page.
            </Link>
          </p>
        </Section>

        <Section id="deployment" title="What the website shows, and why">
          <p data-testid="deployment-rule">
            Each assay shows one default model. <strong>{fmt(nRidge)} assays</strong> show the
            supervised model, {modelName("ridge_seq_struct")}: they have a structure and at least
            300 measured variants. The other <strong>{fmt(nSaprot + nEsm)}</strong> show a zero-shot
            model: {modelName("saprot_650m_af2")} for the {fmt(nSaprot)} smaller assays with a
            structure, {modelName("esm2_650m")} for the {fmt(nEsm)} without one.
          </p>
          <p>
            Why this rule, which was fixed before the final results (<Adr n={25} />, <Adr n={26} />
            ):
          </p>
          <ul className="list-disc space-y-2 pl-5">
            <li>
              <strong>Choose on the task the website does.</strong> A full landscape is mostly
              predictions at positions with no measurement, which the modulo and contiguous splits
              test. There, ridge was as good as or better than LightGBM and the neural network,
              which win only when the position was seen in training.
            </li>
            <li>
              <strong>Supervise only where it pays.</strong> Below 300 variants no supervised model
              was shown to beat SaProt on unseen positions; from 300 up, all did (see the by-size
              chart on the Benchmarks page).
            </li>
            <li>
              <strong>Same model everywhere.</strong> Nothing is chosen per assay from its own
              results. Every prediction is labelled &ldquo;Supervised (n = …)&rdquo; or
              &ldquo;Zero-shot&rdquo;.
            </li>
          </ul>
          <p>
            The live scoring page runs ESM-2 (35M by default, 150M on request) on ONNX Runtime on a
            small CPU server, so it is zero-shot only; its fast mode uses the one-pass wild-type
            marginal (<Adr n={29} />, <Adr n={31} />, <Adr n={34} />
            ).
          </p>
        </Section>

        <Section id="limitations" title="Limitations">
          <ul className="list-disc space-y-2 pl-5">
            <li>
              Predictions are functional effects in laboratory DMS assays, each on its own scale.
              They are not pathogenicity predictions.
            </li>
            <li>
              Single substitutions only: no insertions, deletions or combinations of mutations.
            </li>
            <li>
              Supervised gains are three to six times smaller at unseen positions than at seen ones,
              and for Binding assays a gain on unseen positions is not shown at all (<Adr n={26} />
              ).
            </li>
            <li>
              Intervals have a constant width per assay: they say how wide the error usually is, not
              which variants are harder (<Adr n={24} />
              ).
            </li>
            <li>
              Two deployed targets lie mostly outside their structure, so most of their rows use
              imputed structure features; those rows are flagged (<Adr n={26} />
              ).
            </li>
            <li>
              Structures are AlphaFold2 predictions. Residues with low confidence (pLDDT below 70)
              carry no structure token for SaProt.
            </li>
          </ul>
        </Section>

        <Section id="upstream" title="Two errors found in ProteinGym's SaProt scores">
          <p>
            Our SaProt 650M scores had to match ProteinGym&apos;s published ones in every assay. In
            two assays they could not, and the cause was upstream (<Adr n={15} />
            ):
          </p>
          <ul className="list-disc space-y-2 pl-5" data-testid="upstream-errors">
            <li>
              <span className="font-mono">POLG_HCVJF_Qi_2014</span>: scored with a structure range
              shifted by one residue from an older reference file, so each residue carried its
              neighbour&apos;s structure token.
            </li>
            <li>
              <span className="font-mono">BRCA2_HUMAN_Erwood_2022_HEK293T</span>: scored in three
              structure chunks whose scores were written back in file order, which is not position
              order, so every row holds another variant&apos;s score.
            </li>
          </ul>
          <p>
            For each, a test replays the wrong inputs through our scorer and reproduces
            ProteinGym&apos;s numbers exactly, which shows the difference is theirs. Both assays are
            left out of the agreement check only, and the benchmark uses our scores for them.
          </p>
        </Section>

        <Section id="links" title="Read more">
          <ul className="list-disc space-y-1 pl-5" data-testid="links">
            <li>
              <A href={repoFile("docs/adr")}>Architecture decision records</A>: every decision, with
              its evidence
            </li>
            <li>
              <A href={SITE.modelRepo}>Model card</A> (Hugging Face) and its{" "}
              <A href={repoFile("docs/MODEL_CARD.md")}>source</A>
            </li>
            <li>
              <A href={SITE.huggingFace}>Dataset</A> (Hugging Face) and its{" "}
              <A href={repoFile("docs/DATA_CARD.md")}>data card</A>
            </li>
            <li>
              <A href={`${SITE.api}/docs`}>API documentation</A> (OpenAPI; the server may take a
              moment to wake)
            </li>
            <li>
              <A href={SITE.github}>Source code</A> on GitHub
            </li>
          </ul>
        </Section>

        <aside
          className="max-w-prose rounded-md border bg-muted/50 p-4 text-sm"
          data-testid="not-clinical"
        >
          <strong className="font-medium">Not for clinical use.</strong> MutaScope predicts
          functional effects measured in deep mutational scanning assays. It does not predict
          pathogenicity and must not inform diagnosis or treatment. Zero-shot and supervised
          predictions are always labelled as such.
        </aside>
      </div>
    </>
  );
}
