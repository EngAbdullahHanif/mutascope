import { expect, test } from "@playwright/test";

const PAGES = [
  { href: "/explore", label: "Explorer" },
  { href: "/score", label: "Score" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/about", label: "About" },
];

// /score pings the live API on load; these tests never reach it.
test.beforeEach(async ({ page }) => {
  await page.route(/\/(health|ready)$/, (route) => route.abort());
});

test("the header navigates to every page and marks the current one", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const { href, label } of PAGES) {
    await nav.getByRole("link", { name: label }).click();
    await expect(page).toHaveURL(href);
    await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
    await expect(nav.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  }
});

test("every page carries the not-for-clinical-use note and the project links", async ({ page }) => {
  for (const href of ["/", ...PAGES.map((p) => p.href)]) {
    await page.goto(href);
    const footer = page.getByRole("contentinfo");
    await expect(footer.getByTestId("clinical-note")).toContainText("Not for clinical use.");
    await expect(footer.getByRole("link", { name: "GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/EngAbdullahHanif/mutascope",
    );
    await expect(footer.getByRole("link", { name: "Hugging Face" })).toHaveAttribute(
      "href",
      /^https:\/\/huggingface\.co\/datasets\//,
    );
  }
});

test("dark mode toggles and survives a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const html = page.locator("html");
  await expect(html).not.toHaveClass(/dark/);
  await page.getByRole("button", { name: "Toggle dark mode" }).click();
  await expect(html).toHaveClass(/dark/);
  await page.reload();
  await expect(html).toHaveClass(/dark/);
});

test("the bundle loads and validates from the site alone (API asleep)", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const external: string[] = [];
  // Anything not served by the site itself fails, as it would with the API scaled to zero.
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (new URL(url).origin === origin) return route.continue();
    external.push(url);
    return route.abort();
  });
  await page.goto("/about");
  const status = page.getByTestId("bundle-status");
  await expect(status.getByTestId("check-manifest.json")).toHaveAttribute("data-state", "ok");
  await expect(status.getByTestId("check-manifest.json")).toContainText("217 assays");
  await expect(status.getByTestId("check-benchmarks.json")).toHaveAttribute("data-state", "ok");
  await expect(status.getByTestId("check-Sample protein")).toHaveAttribute("data-state", "ok");
  expect(external).toEqual([]);
});

test("protein files are served as the gzip bytes the manifest lists", async ({ request }) => {
  const manifest = (await (await request.get("/data/manifest.json")).json()) as {
    proteins: { file: string; bytes: number }[];
  };
  const entry = manifest.proteins[0]!;
  const res = await request.get(`/data/${entry.file}`);
  expect(res.ok()).toBe(true);
  expect(res.headers()["content-encoding"]).toBeUndefined();
  const body = await res.body();
  expect(body.length).toBe(entry.bytes);
  expect([body[0], body[1]]).toEqual([0x1f, 0x8b]);
});
