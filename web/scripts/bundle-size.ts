/**
 * JavaScript size report and budget (`pnpm size`, after `pnpm build`).
 *
 * A page's initial JavaScript is every script its prerendered HTML loads: `<script src>` and
 * `<link rel="preload" as="script">` under `/_next/static/`. Turbopack's build prints no
 * per-route sizes, so this reads them from `.next/server/app/*.html`. Sizes are gzip (level 6,
 * what CDNs serve at least), and the budget per page is in `bundle-budget.json`, set from the
 * measured baseline plus headroom. Any page over budget fails.
 *
 * It also fails if 3Dmol.js is in the Landing page's initial JavaScript: the rotating structure
 * loads it lazily (ADR 0041), and a static import would put ~570 kB in front of the first paint.
 * 3Dmol is recognised by class names its minified build keeps; if no chunk at all contains
 * them, the check could no longer see 3Dmol and fails instead of passing silently.
 */
import { readFileSync, readdirSync } from "node:fs";
import { appendFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const NEXT = join(WEB, ".next");

/** Strings only 3Dmol's code contains (its viewer and model classes). */
export const THREEDMOL_MARKERS = ["GLViewer", "GLModel"] as const;

export type Budget = {
  /** Pages measured, by route; each maps to its prerendered HTML and a gzip budget in kB. */
  pages: Record<string, { html: string; maxGzipKb: number }>;
  /** Routes whose initial JavaScript must not contain 3Dmol. */
  no3Dmol: string[];
};

export type PageSize = { route: string; scripts: string[]; rawBytes: number; gzipBytes: number };

/** Script paths an HTML page loads up front, in order, without duplicates. */
export function initialScripts(html: string): string[] {
  const found = new Set<string>();
  for (const m of html.matchAll(/<script\b[^>]*\bsrc="(\/_next\/static\/[^"]+\.js)"/g)) {
    found.add(m[1]!);
  }
  for (const m of html.matchAll(/<link\b[^>]*>/g)) {
    const tag = m[0];
    const href = /\bhref="(\/_next\/static\/[^"]+\.js)"/.exec(tag)?.[1];
    if (href && /\brel="preload"/.test(tag) && /\bas="script"/.test(tag)) found.add(href);
  }
  return [...found];
}

export function hasMarker(source: string): boolean {
  return THREEDMOL_MARKERS.some((m) => source.includes(m));
}

/** Pages over budget, as messages; empty when all are within it. */
export function overBudget(sizes: PageSize[], budget: Budget): string[] {
  return sizes.flatMap((s) => {
    const max = budget.pages[s.route]!.maxGzipKb;
    const kb = s.gzipBytes / 1000;
    return kb > max ? [`${s.route}: ${kb.toFixed(1)} kB gzip > budget ${max} kB`] : [];
  });
}

export function report(sizes: PageSize[], budget: Budget): string {
  const rows = sizes.map(
    (s) =>
      `| \`${s.route}\` | ${s.scripts.length} | ${(s.rawBytes / 1000).toFixed(1)} | ` +
      `${(s.gzipBytes / 1000).toFixed(1)} | ${budget.pages[s.route]!.maxGzipKb} |`,
  );
  return [
    "### Initial JavaScript per page",
    "",
    "| Page | Scripts | Raw (kB) | Gzip (kB) | Budget, gzip (kB) |",
    "| --- | ---: | ---: | ---: | ---: |",
    ...rows,
    "",
  ].join("\n");
}

function measure(route: string, htmlFile: string): PageSize {
  const html = readFileSync(join(NEXT, "server", "app", htmlFile), "utf8");
  const scripts = initialScripts(html);
  if (scripts.length === 0) throw new Error(`${route}: no scripts found in ${htmlFile}`);
  let rawBytes = 0;
  let gzipBytes = 0;
  for (const src of scripts) {
    const body = readFileSync(join(NEXT, src.replace(/^\/_next\//, "")));
    rawBytes += body.length;
    gzipBytes += gzipSync(body, { level: 6 }).length;
  }
  return { route, scripts, rawBytes, gzipBytes };
}

async function main() {
  const budget = JSON.parse(readFileSync(join(WEB, "bundle-budget.json"), "utf8")) as Budget;
  const sizes = Object.entries(budget.pages).map(([route, p]) => measure(route, p.html));
  const text = report(sizes, budget);
  console.log(text);
  await writeFile(join(NEXT, "bundle-size.md"), text);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, text);

  const failures = overBudget(sizes, budget);
  const chunksDir = join(NEXT, "static", "chunks");
  const any3Dmol = readdirSync(chunksDir).some(
    (f) => f.endsWith(".js") && hasMarker(readFileSync(join(chunksDir, f), "utf8")),
  );
  if (!any3Dmol) failures.push("no chunk contains 3Dmol's markers: the 3Dmol check is blind");
  for (const route of budget.no3Dmol) {
    const page = sizes.find((s) => s.route === route);
    if (!page) throw new Error(`${route} is in no3Dmol but not measured`);
    for (const src of page.scripts) {
      if (hasMarker(readFileSync(join(NEXT, src.replace(/^\/_next\//, "")), "utf8"))) {
        failures.push(`${route}: 3Dmol is in its initial JavaScript (${src})`);
      }
    }
  }
  if (failures.length > 0) {
    for (const f of failures) console.error(`FAIL ${f}`);
    process.exit(1);
  }
  console.log(`OK: every page within budget; no 3Dmol in ${budget.no3Dmol.join(", ")}.`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
