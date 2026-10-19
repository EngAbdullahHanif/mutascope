import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { ChartFigure, DotPlot, MarkerKey, type DotRow } from "@/components/charts/dot-plot";
import { PageHeader } from "@/components/layout/page-header";
import {
  coverage,
  coverageLevels,
  excludesZeroAbove,
  functionGains,
  functionTypeName,
  interval,
  ladder,
  ladderSummaries,
  modelName,
  percent,
  plain,
  signed,
  sizeGains,
  SPLIT_NOTES,
  SPLITS,
  zeroShotComparisons,
  zeroShotSummaries,
  type ComparisonRow,
  type Split,
  type SummaryRow,
} from "@/lib/benchmarks/select";
import { niceAxis } from "@/lib/benchmarks/scale";
import { benchmarksFromDisk, manifestFromDisk } from "@/lib/bundle/static-params";
import { pageMetadata } from "@/lib/seo";
import { adrUrl } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Benchmarks",
  description: `How well zero-shot and supervised models rank variants in ${manifestFromDisk().proteins.length} ProteinGym DMS assays: paired differences with 95% bootstrap intervals, per split and function type, and the coverage of the prediction intervals.`,
  path: "/benchmarks",
});

/** "213 assays", or "213–217 assays" when rows differ. */
function assayRange(ns: readonly number[]): string {
  const lo = Math.min(...ns);
  const hi = Math.max(...ns);
  return lo === hi ? `${lo} assays` : `${lo}–${hi} assays (per row)`;
}

type Interval = { diff: number; ci_low: number; ci_high: number; n_assays: number };

/**
 * One paired difference as a chart row. Hollow when the interval includes 0 (or when the caller
 * says the gain is not shown, which is the same test: the interval does not lie above 0).
 */
function diffRow(
  key: string,
  r: Interval,
  label: ReactNode,
  detail: ReactNode,
  hollow = !excludesZeroAbove(r) && r.ci_high >= 0,
): DotRow {
  return {
    key,
    label,
    detail,
    marks: [
      {
        value: r.diff,
        lo: r.ci_low,
        hi: r.ci_high,
        hollow,
        title: `${signed(r.diff)} ${interval(r.ci_low, r.ci_high, true)}, ${r.n_assays} assays`,
      },
    ],
    text: (
      <>
        {signed(r.diff)}{" "}
        <span className="text-muted-foreground">{interval(r.ci_low, r.ci_high, true)}</span>
      </>
    ),
  };
}

const comparisonKey = (r: ComparisonRow) =>
  `${r.model_a}|${r.model_b}|${r.split}|${r.group}|${r.size_bin}`;

const ZERO = { value: 0, label: "no difference" };

export default function BenchmarksPage() {
  const b = benchmarksFromDisk();
  const deployed = modelName(b.deployed_model);
  const comparator = modelName(b.zero_shot_comparator);

  const zs = zeroShotComparisons(b);
  const zsAxis = niceAxis(
    zs.flatMap((r) => [r.ci_low, r.ci_high]),
    [0],
  );
  const zsSummaries = zeroShotSummaries(b);

  const rungs = ladder(b);
  const rungAxis = niceAxis(
    SPLITS.flatMap((s) => rungs[s].flatMap((r) => [r.ci_low, r.ci_high])),
    [0],
  );
  const rungLevels = ladderSummaries(b);

  const sizes = Object.fromEntries(SPLITS.map((s) => [s, sizeGains(b, s)])) as Record<
    Split,
    ComparisonRow[]
  >;
  const sizeAxis = niceAxis(
    SPLITS.flatMap((s) => sizes[s].flatMap((r) => [r.ci_low, r.ci_high])),
    [0],
  );

  const gains = functionGains(b);
  const gainAxis = niceAxis(
    SPLITS.flatMap((s) => gains[s].flatMap((r) => [r.ci_low, r.ci_high])),
    [0],
  );

  const levels = coverageLevels(b);
  const cov = levels.map((level) => ({ level, rows: coverage(b, level) }));
  const covAxis = niceAxis(
    cov.flatMap((c) => c.rows.flatMap((r) => [r.q10, r.q90])),
    levels,
  );

  return (
    <>
      <PageHeader title="Benchmarks">
        How well each model ranks the variants of ProteinGym&apos;s DMS assays. Every number on this
        page is read from the bundle&apos;s <span className="font-mono">benchmarks.json</span>.
      </PageHeader>

      <section aria-labelledby="reading" className="mb-10 max-w-prose text-muted-foreground">
        <h2 id="reading" className="sr-only">
          How to read this page
        </h2>
        <p>
          The metric is Spearman correlation between predicted and measured effect within an assay.
          Assays are aggregated the ProteinGym way (per protein, then per function type, then
          overall), with 95% intervals from 10,000 bootstrap resamples of assays. Two models are
          compared only by a <strong className="text-foreground">paired</strong> bootstrap of their
          difference on the same assays, so the charts show differences with their intervals, never
          two bars side by side (
          <a
            className="text-primary underline underline-offset-4 hover:decoration-2"
            href={adrUrl(14)}
          >
            ADR 0014
          </a>
          ). A hollow marker is a difference whose interval includes 0: the data do not show which
          model is better there.
        </p>
      </section>

      <div className="flex flex-col gap-12">
        <section aria-labelledby="zero-shot" className="flex flex-col gap-4">
          <h2 id="zero-shot" className="text-xl font-semibold tracking-tight">
            Zero-shot models
          </h2>
          <p className="max-w-prose text-muted-foreground">
            Protein language models scored without any assay data: ESM-2 at three sizes, its
            wild-type marginal (one pass, what live scoring uses), SaProt (sequence plus AF2
            structure tokens) and their ensemble. SaProt and the ensemble need a structure, so their
            comparisons run on the assays that have one.
          </p>
          <ChartFigure
            id="zero-shot"
            title="Paired differences in Spearman, zero-shot"
            assays={assayRange(zs.map((r) => r.n_assays))}
            caption={
              <>
                Each row is model A − model B on the assays both have. The int8 ONNX exports,
                compared with their fp32 source in the same file, are a deployment check and were
                rejected (
                <a
                  className="text-primary underline underline-offset-4 hover:decoration-2"
                  href={adrUrl(29)}
                >
                  ADR 0029
                </a>
                ); they are not shown.
              </>
            }
          >
            <DotPlot
              rows={zs.map((r) =>
                diffRow(
                  comparisonKey(r),
                  r,
                  <>
                    {modelName(r.model_a)} <span className="text-muted-foreground">−</span>{" "}
                    {modelName(r.model_b)}
                  </>,
                  `${r.n_assays} assays`,
                ),
              )}
              axis={zsAxis}
              reference={ZERO}
              axisLabel="Difference in Spearman (A − B), 95% paired bootstrap interval"
            />
          </ChartFigure>
          <LevelsTable
            caption="Each score set on its own assays. These intervals describe one model each; compare models only with the paired differences above."
            rows={zsSummaries}
            testId="zero-shot-levels"
          />
        </section>

        <section aria-labelledby="supervised" className="flex flex-col gap-4">
          <h2 id="supervised" className="text-xl font-semibold tracking-tight">
            Supervised models against zero-shot
          </h2>
          <p className="max-w-prose text-muted-foreground">
            Per-assay models trained on the assay&apos;s own measurements, under ProteinGym&apos;s
            five-fold cross-validation. <strong className="text-foreground">Random</strong> folds
            put variants of the same position in training and test;{" "}
            <strong className="text-foreground">modulo</strong> and{" "}
            <strong className="text-foreground">contiguous</strong> hold out whole positions, so the
            model predicts residues it never saw. Each run is compared with a zero-shot model run
            through the same folds: SaProt for runs that use structure features, ESM-2 650M
            otherwise. The deployed model is <strong className="text-foreground">{deployed}</strong>
            .
          </p>
          <ChartFigure
            id="ladder"
            title="The supervised ladder: run − zero-shot comparator, per split"
            assays={assayRange(SPLITS.flatMap((s) => rungs[s].map((r) => r.n_assays)))}
            caption={
              <>
                The MLP runs cover the assays with at least 300 variants (with a structure); the
                others cover every assay their features allow. One-hot ridge has no features that
                describe a residue, so it cannot rank a position it never saw (
                <a
                  className="text-primary underline underline-offset-4 hover:decoration-2"
                  href={adrUrl(21)}
                >
                  ADR 0021
                </a>
                ).
              </>
            }
          >
            <SplitPanels
              render={(split) => (
                <DotPlot
                  rows={rungs[split].map((r) =>
                    diffRow(
                      comparisonKey(r),
                      r,
                      modelName(r.model_a),
                      `${r.n_assays} assays · vs ${modelName(r.model_b)}`,
                    ),
                  )}
                  axis={rungAxis}
                  reference={ZERO}
                  axisLabel="Difference in pooled Spearman, 95% paired bootstrap interval"
                />
              )}
            />
          </ChartFigure>
          <details className="rounded-lg border p-4 text-sm">
            <summary className="cursor-pointer font-medium">
              Each run&apos;s own Spearman, per split (table)
            </summary>
            <div className="mt-4 flex flex-col gap-6">
              {SPLITS.map((split) => (
                <LevelsTable
                  key={split}
                  caption={`${capitalise(split)} split (${SPLIT_NOTES[split]}). Runs on different assays are not comparable from this table.`}
                  rows={rungLevels[split]}
                  testId={`levels-${split}`}
                />
              ))}
            </div>
          </details>

          <ChartFigure
            id="by-size"
            title={`${deployed} − ${comparator}, by assay size`}
            assays={assayRange(sizes.contiguous.map((r) => r.n_assays))}
            caption={
              <>
                Size bins are the assay&apos;s number of measured variants. Within a bin the value
                is a plain mean over its assays, not the ProteinGym aggregate (
                <a
                  className="text-primary underline underline-offset-4 hover:decoration-2"
                  href={adrUrl(25)}
                >
                  ADR 0025
                </a>
                ). The two smallest bins hold few assays, so their intervals are wide. This is the
                evidence behind deploying the supervised model only from 300 variants up.
              </>
            }
          >
            <SplitPanels
              render={(split) => (
                <DotPlot
                  rows={sizes[split].map((r) =>
                    diffRow(
                      comparisonKey(r),
                      r,
                      `${(r.size_bin ?? "").replace(">=", "≥ ").replace("<", "< ")} variants`,
                      `${r.n_assays} assays`,
                    ),
                  )}
                  axis={sizeAxis}
                  reference={ZERO}
                  axisLabel="Difference in pooled Spearman, 95% paired bootstrap interval"
                />
              )}
            />
          </ChartFigure>
        </section>

        <section aria-labelledby="function-types" className="flex flex-col gap-4">
          <h2 id="function-types" className="text-xl font-semibold tracking-tight">
            Gain per function type
          </h2>
          <ChartFigure
            id="function-types"
            title={`${deployed} − ${comparator}, per function type`}
            assays={`${gains.random.reduce((n, r) => n + r.n_assays, 0)} assays in ${gains.random.length} function types`}
            caption="Rows are ProteinGym's function types, each with its own assay count. A hollow marker means the paired interval does not lie above 0, so a gain is not shown there."
          >
            <SplitPanels
              render={(split) => (
                <DotPlot
                  rows={gains[split].map((r) =>
                    diffRow(
                      `${split}-${r.function_type}`,
                      r,
                      functionTypeName(r.function_type),
                      `${r.n_assays} assays${r.gain_shown ? "" : " · gain not shown"}`,
                      !r.gain_shown,
                    ),
                  )}
                  axis={gainAxis}
                  reference={ZERO}
                  axisLabel="Difference in pooled Spearman, 95% paired bootstrap interval"
                />
              )}
            />
          </ChartFigure>
          {b.caveats.map((c) => (
            <aside
              key={c.function_type}
              className="max-w-prose rounded-md border bg-muted/50 p-4 text-sm"
              data-testid={`caveat-${c.function_type}`}
            >
              <p className="font-medium">Caveat: {functionTypeName(c.function_type)}</p>
              <p className="mt-1">{c.text}</p>
              <p className="mt-2 text-muted-foreground">
                Documented in{" "}
                <a
                  className="text-primary underline underline-offset-4 hover:decoration-2"
                  href={adrUrl(26)}
                >
                  ADR 0026
                </a>{" "}
                (weak point 7); the rule that writes this caveat from the paired comparisons is in{" "}
                <a
                  className="text-primary underline underline-offset-4 hover:decoration-2"
                  href={adrUrl(28)}
                >
                  ADR 0028
                </a>{" "}
                (section 8).
              </p>
            </aside>
          ))}
        </section>

        <section aria-labelledby="calibration" className="flex flex-col gap-4">
          <h2 id="calibration" className="text-xl font-semibold tracking-tight">
            Do the prediction intervals hold?
          </h2>
          <p className="max-w-prose text-muted-foreground">
            Each supervised prediction carries 80% and 90% intervals (CV+, a conformal method).
            Coverage is the share of held-out measurements that fall inside their interval, measured
            on test folds the intervals never saw. The calibration folds were either{" "}
            <strong className="text-foreground">split-matched</strong> (split like the test: whole
            positions held out under modulo and contiguous) or{" "}
            <strong className="text-foreground">random</strong>. The design was fixed before any
            coverage was computed (
            <a
              className="text-primary underline underline-offset-4 hover:decoration-2"
              href={adrUrl(24)}
            >
              ADR 0024
            </a>
            ). The deployed intervals are split-matched.
          </p>
          <ChartFigure
            id="calibration"
            title="Test-fold coverage: split-matched against random calibration"
            assays={assayRange(cov.flatMap((c) => c.rows.map((r) => r.nAssays)))}
            caption={
              <>
                Marker: mean coverage over assays. Bar: 10th to 90th percentile of assays (a spread,
                not a confidence interval). &ldquo;Below target&rdquo; counts assays more than 10
                points under the nominal level.{" "}
                <span className="inline-flex flex-wrap gap-4 align-middle">
                  <MarkerKey>split-matched</MarkerKey>
                  <MarkerKey hollow>random</MarkerKey>
                </span>
              </>
            }
          >
            <div className="flex flex-col gap-8">
              {cov.map(({ level, rows }) => (
                <div key={level}>
                  <h4 className="mb-1 text-sm font-medium">{percent(level, 0)} intervals</h4>
                  <DotPlot
                    rows={SPLITS.map((split) => {
                      const m = rows.find((r) => r.scheme === split && r.mode === "scheme")!;
                      const r = rows.find((x) => x.scheme === split && x.mode === "random")!;
                      return {
                        key: `${level}-${split}`,
                        label: `${capitalise(split)} split`,
                        detail: `${m.nAssays} assays · below target: ${m.nBelow} split-matched, ${r.nBelow} random`,
                        marks: [
                          {
                            value: m.mean,
                            lo: m.q10,
                            hi: m.q90,
                            title: `split-matched: mean ${plain(m.mean)}, 10th–90th percentile ${plain(m.q10)}–${plain(m.q90)}`,
                          },
                          {
                            value: r.mean,
                            lo: r.q10,
                            hi: r.q90,
                            hollow: true,
                            title: `random: mean ${plain(r.mean)}, 10th–90th percentile ${plain(r.q10)}–${plain(r.q90)}`,
                          },
                        ],
                        text: (
                          <>
                            <span>matched {plain(m.mean)}</span>
                            <br />
                            <span className="text-muted-foreground">random {plain(r.mean)}</span>
                          </>
                        ),
                      };
                    })}
                    axis={covAxis}
                    reference={{ value: level, label: `nominal ${percent(level, 0)}` }}
                    axisLabel="Coverage on held-out test folds"
                  />
                </div>
              ))}
            </div>
          </ChartFigure>
          <p className="max-w-prose text-sm text-muted-foreground">
            The deployed landscapes also report coverage of measured variants by their out-of-fold
            intervals (
            {b.final_coverage.map((f) => `${plain(f.mean)} at ${percent(f.level, 0)}`).join(", ")}{" "}
            over {b.final_coverage[0]?.n_assays} assays). That holds by construction and is not
            evidence; the test-fold coverage above is (
            <a
              className="text-primary underline underline-offset-4 hover:decoration-2"
              href={adrUrl(26)}
            >
              ADR 0026
            </a>
            , weak point 11).
          </p>
        </section>

        <p className="max-w-prose text-sm text-muted-foreground">
          These are functional effects measured in laboratory DMS assays, not clinical
          pathogenicity. How the numbers were made, and their limits:{" "}
          <Link
            className="text-primary underline underline-offset-4 hover:decoration-2"
            href="/how-it-works"
          >
            How it works
          </Link>
          .
        </p>
      </div>
    </>
  );
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The three splits as small multiples on one shared axis. */
function SplitPanels({ render }: { render: (split: Split) => ReactNode }) {
  return (
    <div className="flex flex-col gap-6">
      {SPLITS.map((split) => (
        <div key={split} data-testid={`panel-${split}`}>
          <h4 className="mb-1 text-sm font-medium">
            {capitalise(split)} split{" "}
            <span className="font-normal text-muted-foreground">({SPLIT_NOTES[split]})</span>
          </h4>
          {render(split)}
        </div>
      ))}
    </div>
  );
}

/** A score set's own aggregate and its own CI, as a table: never drawn as a comparison. */
function LevelsTable({
  caption,
  rows,
  testId,
}: {
  caption: string;
  rows: SummaryRow[];
  testId: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full max-w-3xl text-left text-sm" data-testid={testId}>
        <caption className="mb-2 text-left text-sm text-muted-foreground">{caption}</caption>
        <thead className="text-xs text-muted-foreground">
          <tr className="border-b">
            <th scope="col" className="py-1.5 pr-4 font-medium">
              Model
            </th>
            <th scope="col" className="py-1.5 pr-4 text-right font-medium">
              Assays
            </th>
            <th scope="col" className="py-1.5 pr-4 text-right font-medium">
              Spearman
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              Own 95% CI
            </th>
          </tr>
        </thead>
        <tbody className="font-mono text-xs tabular-nums">
          {rows.map((r) => (
            <tr key={r.model} className="border-b last:border-b-0">
              <th scope="row" className="py-1.5 pr-4 font-sans text-sm font-normal">
                {modelName(r.model)}
              </th>
              <td className="py-1.5 pr-4 text-right">{r.n_assays}</td>
              <td className="py-1.5 pr-4 text-right">{plain(r.value)}</td>
              <td className="py-1.5 text-right text-muted-foreground">
                {interval(r.ci_low, r.ci_high, false)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
