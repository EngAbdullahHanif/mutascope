import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { mockApi } from "./live-mock";

/** The Lighthouse CI protein (lighthouserc.json): the Landing page's first example. */
const PROTEIN = "GFP_AEQVI_Sarkisyan_2016";

/** Every page of the site, and what shows it has finished drawing. */
const PAGES: { path: string; ready: (page: Page) => Promise<void> }[] = [
  { path: "/", ready: heading },
  { path: "/explore", ready: heading },
  {
    path: `/explore/${PROTEIN}`,
    ready: async (page) => {
      await expect(page.getByTestId("render-timing")).toHaveAttribute("data-render-ms", /\d/);
      // The viewer mounts when scrolled to (ADR 0039 addendum); axe checks it mounted.
      await page.getByTestId("structure-placeholder").scrollIntoViewIfNeeded();
      await expect(page.getByTestId("structure-viewer")).toHaveAttribute("data-state", "ready");
    },
  },
  {
    path: "/score",
    ready: async (page) => {
      await expect(page.getByTestId("server-status")).toHaveAttribute("data-state", "ready");
    },
  },
  { path: "/benchmarks", ready: heading },
  { path: "/how-it-works", ready: heading },
  { path: "/about", ready: heading },
  {
    path: "/no-such-page",
    ready: async (page) => {
      await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    },
  },
];

async function heading(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** Opens a page with the live API mocked (only `/score` calls it) and waits for it to settle. */
async function open(page: Page, { path, ready }: (typeof PAGES)[number]) {
  await mockApi(page);
  await page.goto(path);
  await ready(page);
}

// WCAG 2.2 A and AA, plus axe's best practices (heading order, landmarks).
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

for (const theme of ["light", "dark"] as const) {
  test.describe(`axe, ${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      // The reader's choice (next-themes' storage key) and the system agree.
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.addInitScript((t) => window.localStorage.setItem("theme", t), theme);
    });

    for (const p of PAGES) {
      test(`${p.path} has no violations`, async ({ page }) => {
        await open(page, p);
        await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
        const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
        const summary = violations.map((v) => ({
          rule: v.id,
          impact: v.impact,
          nodes: v.nodes.map((n) => `${n.target.join(" ")}: ${n.failureSummary ?? ""}`),
        }));
        expect(summary).toEqual([]);
      });
    }
  });
}

test.describe("375 px wide", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  for (const p of [
    ...PAGES,
    // The longest protein: its heatmap scrolls inside its own box, never the page.
    {
      path: "/explore/A0A140D2T1_ZIKV_Sourisseau_2019",
      ready: (page: Page) =>
        expect(page.getByTestId("render-timing")).toHaveAttribute("data-render-ms", /\d/),
    },
  ]) {
    test(`${p.path} does not scroll sideways`, async ({ page }) => {
      await open(page, p);
      const widths = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(widths.page).toBeLessThanOrEqual(widths.viewport);
    });
  }
});

test.describe("375 px header", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the navigation folds behind a menu button that opens, navigates and closes", async ({
    page,
  }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Main" });
    const menu = page.getByRole("button", { name: "Menu" });
    await expect(nav).toBeHidden();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await nav.getByRole("link", { name: "Benchmarks" }).click();
    await expect(page).toHaveURL("/benchmarks");
    await expect(nav).toBeHidden();
  });
});

test("an unknown address is a 404 page inside the site, not indexed", async ({ page }) => {
  const res = await page.goto("/no-such-page");
  expect(res?.status()).toBe(404);
  await expect(page).toHaveTitle("Page not found · MutaScope");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  await expect(page.getByRole("contentinfo")).toBeVisible();
  await page.getByRole("link", { name: "Back to the start page" }).click();
  await expect(page).toHaveURL("/");
});
