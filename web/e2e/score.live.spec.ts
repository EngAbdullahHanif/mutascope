import { expect, test } from "@playwright/test";

/**
 * Smoke test against the live API (Cloud Run). Opt-in, never in CI: it needs the network, can
 * wait ~20 s for a cold start, and uses the service's rate limits.
 *
 *   MUTASCOPE_LIVE_API=1 pnpm test:e2e score.live
 *
 * The API's CORS allows only the production origins, not this test server's, so the browser's
 * API calls are relayed by Playwright (`route.fetch`, outside the browser): the real requests
 * and the real responses, with only an `Access-Control-Allow-Origin` header added.
 */
test.skip(!process.env.MUTASCOPE_LIVE_API || !!process.env.CI, "set MUTASCOPE_LIVE_API=1 to run");

const API = /^https:\/\/mutascope-api-[^/]+\.run\.app\//;

test("fast mode on the live API: wakes the server and returns a full landscape", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.route(API, async (route) => {
    const allow = {
      "access-control-allow-origin": "*",
      "access-control-expose-headers": "Retry-After",
    };
    if (route.request().method() === "OPTIONS") {
      return route.fulfill({
        status: 204,
        headers: {
          ...allow,
          "access-control-allow-methods": "GET, POST, DELETE",
          "access-control-allow-headers": "Content-Type, Last-Event-ID",
        },
      });
    }
    const headers = { ...route.request().headers() };
    delete headers.origin;
    const response = await route.fetch({ headers, timeout: 90_000 });
    await route.fulfill({ response, headers: { ...response.headers(), ...allow } });
  });

  await page.goto("/score");
  await expect(page.getByTestId("server-status")).toHaveAttribute("data-state", "ready", {
    timeout: 90_000,
  });
  // The limit shown is the live setting (300 when this test was last updated; not asserted).
  await expect(page.getByText(/Up to [\d,]+ residues on this server\./)).toBeVisible();

  await page.getByRole("button", { name: "Ubiquitin (yeast, 128 residues)" }).click();
  await page.getByTestId("score-button").click();
  const result = page.getByTestId("live-result");
  await expect(result).toHaveAttribute("data-complete", "true", { timeout: 60_000 });
  await expect(result).toHaveAttribute("data-done", "128");
  await expect(page.getByTestId("live-label")).toHaveText(/^Zero-shot, ESM-2 (35M|150M)$/);
  await expect(page.getByTestId("heatmap")).toBeVisible();
});
