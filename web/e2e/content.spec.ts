/**
 * The content pages (ADR 0041): every number shown is the one in the bundle's benchmarks.json,
 * the Landing structure loads after first paint and respects reduced motion, and the SEO files
 * point at the production origin. Expected values are read from the served file here, never
 * written into the test.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

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

test("the Landing headline, and a subline whose assay count is the manifest's", async ({
  page,
  request,
}) => {
  const manifest = (await (await request.get("/data/manifest.json")).json()) as {
    proteins: unknown[];
  };
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "See what every mutation does to a protein, and how well models predict it.",
  );
  await expect(page.getByTestId("landing-subline")).toHaveText(
    `Predicted effects of every single amino-acid substitution in ${manifest.proteins.length} ProteinGym assays, tested against real measurements.`,
  );
});

test("the Landing's title is the name alone; its description and Open Graph tags are unchanged", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page).toHaveTitle("MutaScope");
  const longTitle = "MutaScope: mutation landscapes for ProteinGym assays";
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", longTitle);
  await expect(page.locator('meta[name="twitter:title"]')).toHaveAttribute("content", longTitle);
  await expect(page.locator('meta[name="description"]')).toHaveAttribute(
    "content",
    /^Predicted functional effect of every single amino-acid substitution in \d+ ProteinGym DMS assays, with an honest benchmark\.$/,
  );
  // Other pages keep "<page> · MutaScope".
  await page.goto("/about");
  await expect(page).toHaveTitle("About · MutaScope");
});

test("the Landing hero spaces its subline, buttons, examples and the benchmark heading", async ({
  page,
}) => {
  await page.goto("/");
  const box = async (testId: string) => (await page.getByTestId(testId).boundingBox())!;
  const subline = await box("landing-subline");
  const actions = await box("landing-actions");
  const examples = await box("landing-examples");
  // About 32 px from the subline to the buttons, 40 px from the buttons to the examples.
  const gap1 = actions.y - (subline.y + subline.height);
  expect(gap1).toBeGreaterThanOrEqual(30);
  expect(gap1).toBeLessThanOrEqual(34);
  const gap2 = examples.y - (actions.y + actions.height);
  expect(gap2).toBeGreaterThanOrEqual(38);
  expect(gap2).toBeLessThanOrEqual(42);
  // About 80 px from the bottom of the hero to "What the benchmark shows".
  const hero = (await page.locator("main section").first().boundingBox())!;
  const heading = (await page
    .getByRole("heading", { name: "What the benchmark shows" })
    .boundingBox())!;
  const gap3 = heading.y - (hero.y + hero.height);
  expect(gap3).toBeGreaterThanOrEqual(76);
  expect(gap3).toBeLessThanOrEqual(84);
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

/**
 * Scrolls the Landing figure into view. The figure, not the structure inside it: that element is
 * replaced as it goes from placeholder to loading to viewer, and scrolling an element that is
 * replaced mid-scroll fails ("not attached to the DOM").
 */
async function scrollToFigure(page: Page) {
  await page
    .locator("figure", { has: page.getByTestId("landing-structure") })
    .scrollIntoViewIfNeeded();
}

test("the Landing structure loads after first paint and turns", async ({ page }) => {
  const structureRequests: number[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/data/structures/")) structureRequests.push(Date.now());
  });
  await page.goto("/", { waitUntil: "load" });
  const loadedAt = Date.now();
  const viewer = page.getByTestId("landing-structure");
  await scrollToFigure(page);
  await expect(viewer).toHaveAttribute("data-state", "ready", { timeout: 15_000 });
  await expect(viewer).toHaveAttribute("data-spinning", "true");
  expect(structureRequests.length).toBe(1);
  // Fetched once the page had loaded, never as part of it.
  expect(structureRequests[0]!).toBeGreaterThanOrEqual(loadedAt - 50);
});

test("on a phone the Landing structure loads only when scrolled to", async ({ page }) => {
  const structureRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/data/structures/")) structureRequests.push(r.url());
  });
  // An iPhone SE-sized window, where the figure starts below the fold (at 375 × 812 its top
  // edge is already on screen, and it loads at once).
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto("/", { waitUntil: "load" });
  const viewer = page.getByTestId("landing-structure");
  expect((await viewer.boundingBox())!.y).toBeGreaterThan(667);
  // Still the placeholder after the page is idle.
  await page.waitForTimeout(2500);
  await expect(viewer).toHaveAttribute("data-state", "waiting");
  expect(structureRequests).toEqual([]);
  await scrollToFigure(page);
  await expect(viewer).toHaveAttribute("data-state", "ready", { timeout: 15_000 });
  await expect(viewer).toHaveAttribute("data-spinning", "true");
  expect(structureRequests).toHaveLength(1);
});

test("with reduced motion the structure never turns, and stops when the setting changes", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const viewer = page.getByTestId("landing-structure");
  await scrollToFigure(page);
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
  // The not-for-clinical-use note is the footer's alone; the page has no box of its own.
  await expect(page.getByTestId("not-clinical")).toHaveCount(0);
  await expect(page.getByRole("main")).not.toContainText("Not for clinical use.");
  await expect(page.getByTestId("pipeline")).toBeVisible();
});

test("About has the author's text, with bold lead-ins, and links", async ({ page }) => {
  await page.goto("/about");
  const paragraphs = page.getByTestId("about-text").locator("p");
  await expect(paragraphs).toHaveText([
    "Hi, I'm Abdullah Hanif. I use machine learning to understand genomes and proteins, and I'm doing a Master's in Bioinformatics at Saarland University.",
    "Why I built MutaScope. Protein language models are usually summarised by a single score. I wanted to see where they predict well and where they don't, so the benchmark results here come with their test setup, their uncertainty and their limits.",
    "Let's talk. I'd love to join a team working on similar ideas, where machine learning meets genomics and biomedicine. If you're working on something similar, I'd love to hear from you.",
  ]);
  await expect(paragraphs.locator("strong")).toHaveText([
    "Hi, I'm Abdullah Hanif.",
    "Why I built MutaScope.",
    "Let's talk.",
  ]);
  const block = page.getByTestId("about-links");
  const links = block.getByRole("link");
  await expect(links).toHaveText(["abdullahhanif.me", "Email", "GitHub", "LinkedIn"]);
  const hrefs = await links.evaluateAll((els) => els.map((el) => el.getAttribute("href")));
  expect(hrefs).toEqual([
    "https://abdullahhanif.me",
    "mailto:abha00004@stud.uni-saarland.de",
    "https://github.com/EngAbdullahHanif/",
    "https://www.linkedin.com/in/engabdullahhanif/",
  ]);
  // The address is the Email link's title, never visible text.
  await expect(links.nth(1)).toHaveAttribute("title", "abha00004@stud.uni-saarland.de");
  await expect(block).not.toContainText("abha00004");
  // Two lines: the site, then Email · GitHub · LinkedIn.
  await expect(block.locator("div")).toHaveText("abdullahhanif.me");
  await expect(block.locator("ul")).toHaveText(/^Email\s*·\s*GitHub\s*·\s*LinkedIn$/);
  // About 56 px between the "Let's talk" paragraph and the links, about 24 px between the lines.
  const above = (await paragraphs.last().boundingBox())!;
  const below = (await block.boundingBox())!;
  const gap = below.y - (above.y + above.height);
  expect(gap).toBeGreaterThanOrEqual(52);
  expect(gap).toBeLessThanOrEqual(60);
  const line1 = (await block.locator("div").boundingBox())!;
  const line2 = (await block.locator("ul").boundingBox())!;
  const lineGap = line2.y - (line1.y + line1.height);
  expect(lineGap).toBeGreaterThanOrEqual(22);
  expect(lineGap).toBeLessThanOrEqual(26);
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
