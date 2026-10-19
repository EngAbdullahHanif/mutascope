/**
 * The content pages (ADR 0041): every number shown is the one in the bundle's benchmarks.json,
 * the Landing structure loads after first paint and respects reduced motion, and the SEO files
 * point at the production origin. Expected values are read from the served file here, never
 * written into the test.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

const ORIGIN = "https://mutascope.abdullahhanif.me";

type Row = Record<string, unknown>;
type Benchmarks = Record<string, Row[]> & {
  deployed_model: string;
  zero_shot_comparator: string;
};

async function benchmarks(request: APIRequestContext): Promise<Benchmarks> {
  return (await (await request.get("/data/benchmarks.json")).json()) as Benchmarks;
}

function only(rows: Row[], match: Row): Row {
  const found = rows.filter((r) => Object.entries(match).every(([k, v]) => r[k] === v));
  expect(found, JSON.stringify(match)).toHaveLength(1);
  return found[0]!;
}

const signed = (x: number) => `${x < 0 ? "−" : "+"}${Math.abs(x).toFixed(3)}`;

// /score pings the live API; nothing here should reach it.
test.beforeEach(async ({ page }) => {
  await page.route(/\/(health|ready)$/, (route) => route.abort());
});

test("the Landing headlines are the numbers in benchmarks.json", async ({ page, request }) => {
  const b = await benchmarks(request);
  const structure = only(b.zero_shot_comparisons!, {
    model_a: "saprot_650m_af2",
    model_b: "esm2_650m",
    metric: "spearman",
    level: "overall",
  });
  const supervised = only(b.supervised_comparisons!, {
    model_a: b.deployed_model,
    model_b: b.zero_shot_comparator,
    split: "contiguous",
    level: "overall",
  });
  const coverage = only(b.calibration!, {
    scheme: "contiguous",
    mode: "scheme",
    level: 0.9,
    size_bin: "all",
    statistic: "mean",
  });

  await page.goto("/");
  await expect(page.getByTestId("headline-structure")).toContainText(
    signed(structure.diff as number),
  );
  await expect(page.getByTestId("headline-structure")).toContainText(
    `${structure.n_assays as number} assays`,
  );
  await expect(page.getByTestId("headline-supervised")).toContainText(
    signed(supervised.diff as number),
  );
  await expect(page.getByTestId("headline-coverage")).toContainText(
    `${((coverage.value as number) * 100).toFixed(1)}%`,
  );
});

test("the Landing links to the examples and to live scoring", async ({ page }) => {
  await page.goto("/");
  const examples = page.getByTestId("examples").getByRole("link");
  await expect(examples).toHaveCount(3);
  for (const link of await examples.all()) {
    await expect(link).toHaveAttribute("href", /^\/explore\/[A-Z0-9_]+/);
  }
  await page.getByRole("link", { name: /Score a sequence/ }).click();
  await expect(page).toHaveURL("/score");
});

test("the Landing structure loads after first paint and turns", async ({ page }) => {
  const structureRequests: number[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/data/structures/")) structureRequests.push(Date.now());
  });
  await page.goto("/", { waitUntil: "load" });
  const loadedAt = Date.now();
  const viewer = page.getByTestId("landing-structure");
  await expect(viewer).toHaveAttribute("data-state", "ready", { timeout: 15_000 });
  await expect(viewer).toHaveAttribute("data-spinning", "true");
  expect(structureRequests.length).toBe(1);
  // Fetched once the page had loaded, never as part of it.
  expect(structureRequests[0]!).toBeGreaterThanOrEqual(loadedAt - 50);
});

test("with reduced motion the structure never turns, and stops when the setting changes", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const viewer = page.getByTestId("landing-structure");
  await expect(viewer).toHaveAttribute("data-state", "ready", { timeout: 15_000 });
  await expect(viewer).toHaveAttribute("data-spinning", "false");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(viewer).toHaveAttribute("data-spinning", "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(viewer).toHaveAttribute("data-spinning", "false");
});

test("every Benchmarks chart states its assay count; the Binding caveat cites its ADR", async ({
  page,
  request,
}) => {
  const b = await benchmarks(request);
  await page.goto("/benchmarks");
  for (const id of ["zero-shot", "ladder", "by-size", "function-types", "calibration"]) {
    await expect(page.getByTestId(`chart-${id}`).getByTestId("assay-count")).toHaveText(
      /\d+.*assays/,
    );
  }
  for (const caveat of b.caveats!) {
    const box = page.getByTestId(`caveat-${caveat.function_type as string}`);
    await expect(box).toContainText(caveat.text as string);
    await expect(box.getByRole("link", { name: "ADR 0026" })).toHaveAttribute(
      "href",
      /docs\/adr\/0026-phase-3-close\.md$/,
    );
  }
  // Paired differences, never two score sets as bars: the zero-shot chart's rows are A − B.
  await expect(page.getByTestId("chart-zero-shot")).toContainText("SaProt 650M − ESM-2 650M");
});

test("How it works states the deployment rule from the manifest and keeps the clinical note", async ({
  page,
  request,
}) => {
  const manifest = (await (await request.get("/data/manifest.json")).json()) as {
    proteins: { default_model: string }[];
  };
  const ridge = manifest.proteins.filter((p) => p.default_model === "ridge_seq_struct").length;
  const rest = manifest.proteins.length - ridge;
  await page.goto("/how-it-works");
  await expect(page.getByTestId("deployment-rule")).toContainText(`${ridge} assays`);
  await expect(page.getByTestId("deployment-rule")).toContainText(`other ${rest}`);
  await expect(page.getByTestId("upstream-errors")).toContainText("POLG_HCVJF_Qi_2014");
  await expect(page.getByTestId("upstream-errors")).toContainText(
    "BRCA2_HUMAN_Erwood_2022_HEK293T",
  );
  await expect(page.getByTestId("not-clinical")).toContainText("Not for clinical use.");
  await expect(page.getByTestId("pipeline")).toBeVisible();
});

test("About has the author's links and the AI statement", async ({ page }) => {
  await page.goto("/about");
  const links = page.getByTestId("about-links");
  await expect(links.getByRole("link", { name: "GitHub" })).toHaveAttribute(
    "href",
    "https://github.com/EngAbdullahHanif/",
  );
  await expect(links.getByRole("link", { name: "LinkedIn" })).toHaveAttribute(
    "href",
    "https://www.linkedin.com/in/engabdullahhanif/",
  );
  await expect(page.getByRole("link", { name: "http://abdullahhanif.me/" })).toBeVisible();
  await expect(page.getByTestId("ai-statement")).toContainText("Claude Code");
});

test("every page has a canonical URL and an Open Graph image on the production origin", async ({
  page,
  request,
}) => {
  for (const path of ["/", "/explore", "/score", "/benchmarks", "/how-it-works", "/about"]) {
    await page.goto(path);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href",
      path === "/" ? ORIGIN : `${ORIGIN}${path}`,
    );
    const image = await page.locator('meta[property="og:image"]').getAttribute("content");
    expect(image).toMatch(new RegExp(`^${ORIGIN}${path === "/" ? "" : path}/opengraph-image`));
    const res = await request.get(new URL(image!).pathname + new URL(image!).search);
    expect(res.headers()["content-type"]).toBe("image/png");
  }
});

test("robots.txt and the sitemap list the site on its production origin", async ({ request }) => {
  const robots = await (await request.get("/robots.txt")).text();
  expect(robots).toContain(`Sitemap: ${ORIGIN}/sitemap.xml`);
  const manifest = (await (await request.get("/data/manifest.json")).json()) as {
    proteins: unknown[];
  };
  const sitemap = await (await request.get("/sitemap.xml")).text();
  const urls = sitemap.match(/<loc>[^<]+<\/loc>/g) ?? [];
  expect(urls).toHaveLength(6 + manifest.proteins.length);
  expect(urls.every((u) => u.startsWith(`<loc>${ORIGIN}/`))).toBe(true);
});
