import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

/** Blocks every request to another origin, as with the API scaled to zero; returns them. */
async function siteOnly(page: Page, baseURL: string | undefined): Promise<string[]> {
  const origin = new URL(baseURL!).origin;
  const external: string[] = [];
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (new URL(url).origin === origin) return route.continue();
    external.push(url);
    return route.abort();
  });
  return external;
}

async function open(page: Page, assayId: string) {
  await page.goto(`/explore/${assayId}`);
  await expect(page.getByTestId("render-timing")).toHaveAttribute("data-render-ms", /\d/);
}

const modelOptions = (page: Page) =>
  page.getByTestId("model-select").locator("option").allTextContents();

// Three kinds of protein: small and supervised, the largest (with an MSA region), and one
// with no supervised model (no structure, so no SaProt either).
test.describe("protein pages show the right labels", () => {
  test("small supervised protein: PIN1 (39 residues)", async ({ page, baseURL }) => {
    const external = await siteOnly(page, baseURL);
    await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Peptidyl-prolyl cis-trans isomerase NIMA-interacting 1",
    );
    await expect(page.getByTestId("model-label")).toHaveText("Supervised (n = 686)");
    expect(await modelOptions(page)).toEqual([
      "Default: Ridge (sequence + structure) — Supervised (n = 686)",
      "ESM-2 650M — Zero-shot",
      "SaProt 650M — Zero-shot",
    ]);
    await expect(page.getByTestId("msa-note")).toHaveCount(0);
    // Small enough to fit: the whole protein at the widest zoom that fits, no scrolling.
    const fit = await page
      .getByRole("application")
      .evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
    expect(fit.scroll).toBeLessThanOrEqual(fit.client);
    expect(
      Number(await page.getByTestId("heatmap").getAttribute("data-zoom")),
    ).toBeGreaterThanOrEqual(14);

    await page.getByTestId("model-select").selectOption("saprot_650m_af2");
    await expect(page.getByTestId("model-label")).toHaveText("Zero-shot");
    await page
      .getByRole("region", { name: "View options" })
      .getByText("Measured", { exact: true })
      .click();
    await expect(page.getByTestId("model-label")).toHaveText("Measured");
    expect(external).toEqual([]);
  });

  test("large supervised protein: Zika env (3,423 residues, MSA region)", async ({
    page,
    baseURL,
  }) => {
    const external = await siteOnly(page, baseURL);
    await open(page, "A0A140D2T1_ZIKV_Sourisseau_2019");
    await expect(page.getByTestId("model-label")).toHaveText("Supervised (n = 9,576)");
    await expect(page.getByTestId("msa-note")).toContainText("residues 281–804");
    // 68,460 cells: the narrowest zoom, scrolled, not squeezed.
    await expect(page.getByTestId("heatmap")).toHaveAttribute("data-zoom", "3");
    const scroller = page.getByRole("application");
    expect(await scroller.evaluate((el) => el.scrollWidth)).toBe(3423 * 3);
    await scroller.evaluate((el) => (el.scrollLeft = el.scrollWidth));
    await page.getByTestId("model-select").selectOption("esm2_650m");
    await expect(page.getByTestId("model-label")).toHaveText("Zero-shot");
    expect(external).toEqual([]);
  });

  test("zero-shot-only protein: p53 Null Nutlin (no structure)", async ({ page, baseURL }) => {
    const external = await siteOnly(page, baseURL);
    await open(page, "P53_HUMAN_Giacomelli_2018_Null_Nutlin");
    await expect(page.getByTestId("model-label")).toHaveText("Zero-shot");
    await expect(page.getByTestId("default-reason")).toHaveText(
      "No supervised model: no structure.",
    );
    expect(await modelOptions(page)).toEqual(["Default: ESM-2 650M — Zero-shot"]);
    await expect(page.getByTestId("msa-note")).toHaveCount(0);
    expect(external).toEqual([]);
  });
});

test("the picker searches name, UniProt, function type and label", async ({ page }) => {
  await page.goto("/explore");
  const count = page.getByTestId("protein-count");
  await expect(count).toHaveText("217 of 217 assays");
  await page.getByTestId("protein-search").fill("zero-shot");
  await expect(count).toHaveText("19 of 217 assays");
  await page.getByTestId("protein-search").fill("pin1 stability");
  await expect(count).toHaveText("1 of 217 assays");
  await page
    .getByRole("link", { name: "Peptidyl-prolyl cis-trans isomerase NIMA-interacting 1" })
    .click();
  await expect(page).toHaveURL("/explore/PIN1_HUMAN_Tsuboyama_2023_1I6C");
  await expect(page.getByTestId("model-label")).toHaveText("Supervised (n = 686)");
});

test("the keyboard moves between cells, and each cell is described", async ({ page }) => {
  await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
  await page.getByRole("application").focus();
  const announcer = page.getByTestId("heatmap-announcer");
  await expect(announcer).toContainText("K1A. Supervised (n = 686)");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowDown");
  await expect(announcer).toContainText("L2C. Supervised (n = 686) Ridge (sequence + structure)");
  await expect(announcer).toContainText("80% interval");
  await expect(page.getByTestId("cell-tooltip")).toContainText("L2C");
  await page.keyboard.press("End");
  await expect(announcer).toContainText("G39C.");
  // The wild type is named as such, not given a score.
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowDown"); // D
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowDown"); // K (row 8)
  await expect(announcer).toHaveText("K1: wild type.");
});

test("the top lists jump to a cell, and the CSV holds the current view", async ({ page }) => {
  await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
  await expect(page.getByTestId("top-harmful").locator("li")).toHaveCount(10);
  await expect(page.getByTestId("top-tolerated").locator("li")).toHaveCount(10);

  await page.getByRole("checkbox", { name: "Grey out unmeasured" }).check();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("download-csv").click(),
  ]);
  expect(download.suggestedFilename()).toBe("PIN1_HUMAN_Tsuboyama_2023_1I6C_ridge_seq_struct.csv");
  const lines = readFileSync((await download.path())!, "utf8")
    .trim()
    .split("\n");
  expect(lines[0]).toBe(
    "mutation,position,wt,mt,value,quantity,model,label,lower_80,upper_80,lower_90,upper_90,measured_dms_score",
  );
  // Masked to the measured variants: exactly the assay's 686.
  expect(lines).toHaveLength(1 + 686);
  expect(lines.every((l, i) => i === 0 || l.includes('"Supervised (n = 686)"'))).toBe(true);
});

test("the table shows the same view, a page of residues at a time", async ({ page }) => {
  await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
  await page.getByRole("button", { name: "Show as a table" }).click();
  const table = page.getByTestId("landscape-table");
  await expect(table.getByRole("table")).toContainText("Supervised (n = 686)");
  await expect(table.getByText("Residues 1–25 of 39")).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(1 + 25);
  await table.getByRole("button", { name: "Next" }).click();
  await expect(table.getByText("Residues 26–39 of 39")).toBeVisible();
});

// The budget (ADR 0038): a protein of about 1,000 residues is drawn within 300 ms of its data
// loading. The two closest to 1,000 residues in the bundle, on either side.
for (const [assayId, length] of [
  ["MSH2_HUMAN_Jia_2020", 934],
  ["CAR11_HUMAN_Meitlis_2020_gof", 1154],
] as const) {
  test(`a ${length}-residue protein is drawn within 300 ms of loading`, async ({ page }) => {
    await open(page, assayId);
    const ms = Number(await page.getByTestId("render-timing").getAttribute("data-render-ms"));
    test.info().annotations.push({ type: "render-ms", description: `${assayId}: ${ms}` });
    expect(ms).toBeGreaterThan(0);
    expect(ms).toBeLessThan(300);
  });
}

test("the old /explorer link redirects, and an unknown protein is a 404", async ({
  page,
  request,
}) => {
  await page.goto("/explorer");
  await expect(page).toHaveURL("/explore");
  const res = await request.get("/explore/NOT_A_PROTEIN");
  expect(res.status()).toBe(404);
});

// A browser holding a protein file cached from an earlier bundle (the 1.0.0 → 1.1.0 move): the
// loader retries once past the cache, and only then says the data is out of date.
test.describe("a cached protein file with another digest", () => {
  const assayId = "F7YBW8_MESOW_Aakre_2015";
  const path = `/data/proteins/${assayId}.json.gz`;

  /** Serves the file with one byte flipped (still gzip, another sha256) to the first `stale` requests. */
  async function staleCopy(page: Page, stale: number): Promise<string[]> {
    const requests: string[] = [];
    await page.route(
      (url) => url.pathname === path,
      async (route) => {
        requests.push(route.request().url());
        const response = await route.fetch();
        if (requests.length > stale) return route.fulfill({ response });
        const body = Buffer.from(await response.body());
        body[body.length - 5] = body[body.length - 5]! ^ 0xff;
        return route.fulfill({ response, body });
      },
    );
    return requests;
  }

  test("is fetched once more and the page loads", async ({ page }) => {
    const requests = await staleCopy(page, 1);
    await open(page, assayId);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toMatch(new RegExp(`${path}\\?v=[0-9a-f]{64}$`));
    expect(requests[1]).toBe(requests[0]);
    await expect(page.getByTestId("stale-data")).toHaveCount(0);
  });

  test("still differing, says the data is out of date and offers a reload", async ({ page }) => {
    const requests = await staleCopy(page, Infinity);
    await page.goto(`/explore/${assayId}`);
    const alert = page.getByTestId("stale-data");
    await expect(alert).toContainText("This data is out of date. Reload the page.");
    await expect(alert).not.toContainText("sha256");
    expect(requests).toHaveLength(2);
    await Promise.all([
      page.waitForEvent("load"),
      alert.getByRole("button", { name: "Reload" }).click(),
    ]);
    await expect(page.getByTestId("stale-data")).toBeVisible();
    expect(requests).toHaveLength(4);
  });
});
