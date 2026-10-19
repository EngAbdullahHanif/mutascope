/**
 * Lighthouse scores as a Markdown table (`node scripts/lighthouse-summary.ts`, after `pnpm
 * lighthouse`): for each form factor and page, every run's performance and accessibility and
 * their medians, which is what `lighthouserc.json` asserts on, then the metrics behind each
 * median run's score and the machine's CPU benchmark (Lighthouse's `benchmarkIndex`), so a
 * score can be explained from the job summary alone. Reads LHCI's filesystem output
 * (`.lighthouseci/<form factor>/manifest.json` and the reports it lists) and appends to the CI
 * job summary when there is one.
 */
import { existsSync, readFileSync } from "node:fs";
import { appendFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type Run = {
  url: string;
  isRepresentativeRun?: boolean;
  jsonPath?: string;
  summary: { performance: number; accessibility: number };
};

/** The parts of a Lighthouse report the metrics table reads. */
export type Report = {
  environment: { benchmarkIndex: number };
  audits: Record<string, { numericValue?: number }>;
};

const METRICS = [
  ["first-contentful-paint", "FCP (ms)"],
  ["largest-contentful-paint", "LCP (ms)"],
  ["total-blocking-time", "TBT (ms)"],
  ["cumulative-layout-shift", "CLS"],
  ["speed-index", "SI (ms)"],
] as const;

export function metricsRow(formFactor: string, url: string, report: Report): string {
  const values = METRICS.map(([id]) => {
    const v = report.audits[id]?.numericValue;
    if (v === undefined) return "–";
    return id === "cumulative-layout-shift" ? v.toFixed(3) : String(Math.round(v));
  });
  return (
    `| ${formFactor} | \`${new URL(url).pathname}\` | ${values.join(" | ")} | ` +
    `${Math.round(report.environment.benchmarkIndex)} |`
  );
}

export function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

export function table(formFactor: string, runs: Run[]): string {
  const pages = [...new Set(runs.map((r) => r.url))];
  const rows = pages.map((url) => {
    const mine = runs.filter((r) => r.url === url);
    const perf = mine.map((r) => r.summary.performance);
    const a11y = mine.map((r) => r.summary.accessibility);
    const fmt = (v: number) => v.toFixed(2);
    return (
      `| ${formFactor} | \`${new URL(url).pathname}\` | ${perf.map(fmt).join(", ")} | ` +
      `**${fmt(median(perf))}** | ${a11y.map(fmt).join(", ")} | **${fmt(median(a11y))}** |`
    );
  });
  return rows.join("\n");
}

async function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", ".lighthouseci");
  const factors = ["mobile", "desktop"].filter((f) => existsSync(join(root, f, "manifest.json")));
  const runs = (f: string) =>
    JSON.parse(readFileSync(join(root, f, "manifest.json"), "utf8")) as Run[];
  const parts = factors.map((f) => table(f, runs(f)));
  const metrics = factors.flatMap((f) =>
    runs(f)
      .filter((r) => r.isRepresentativeRun && r.jsonPath)
      .map((r) => metricsRow(f, r.url, JSON.parse(readFileSync(r.jsonPath!, "utf8")) as Report)),
  );
  const text = [
    "### Lighthouse (budgets: performance ≥ 0.90, accessibility ≥ 0.95, median of 3)",
    "",
    "| Form factor | Page | Performance (runs) | Median | Accessibility (runs) | Median |",
    "| --- | --- | --- | ---: | --- | ---: |",
    ...parts,
    "",
    "Metrics of each median run, and the machine's CPU benchmark (`benchmarkIndex`):",
    "",
    `| Form factor | Page | ${METRICS.map(([, label]) => label).join(" | ")} | Benchmark |`,
    `| --- | --- | ${METRICS.map(() => "---:").join(" | ")} | ---: |`,
    ...metrics,
    "",
  ].join("\n");
  console.log(text);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, text);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
