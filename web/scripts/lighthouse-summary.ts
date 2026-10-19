/**
 * Lighthouse scores as a Markdown table (`node scripts/lighthouse-summary.ts`, after `pnpm
 * lighthouse`): for each form factor and page, every run's performance and accessibility and
 * their medians, which is what `lighthouserc.json` asserts on. Reads LHCI's filesystem output
 * (`.lighthouseci/<form factor>/manifest.json`) and appends to the CI job summary when there is one.
 */
import { existsSync, readFileSync } from "node:fs";
import { appendFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type Run = { url: string; summary: { performance: number; accessibility: number } };

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
  const parts = ["mobile", "desktop"]
    .filter((f) => existsSync(join(root, f, "manifest.json")))
    .map((f) =>
      table(f, JSON.parse(readFileSync(join(root, f, "manifest.json"), "utf8")) as Run[]),
    );
  const text = [
    "### Lighthouse (budgets: performance ≥ 0.90, accessibility ≥ 0.95, median of 3)",
    "",
    "| Form factor | Page | Performance (runs) | Median | Accessibility (runs) | Median |",
    "| --- | --- | --- | ---: | --- | ---: |",
    ...parts,
    "",
  ].join("\n");
  console.log(text);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, text);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
