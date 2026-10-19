import { expect, test, type Page } from "@playwright/test";

import { SEQ, event, gate, grid, job, json, mockApi, problem, progress, sse } from "./live-mock";

async function enter(page: Page, text = SEQ) {
  await page.getByLabel("Protein sequence").fill(text);
}

const result = (page: Page) => page.getByTestId("live-result");
const scoreButton = (page: Page) => page.getByTestId("score-button");

test.describe("fast mode", () => {
  test("scores a sequence and draws the full zero-shot landscape", async ({ page }) => {
    const requests = await mockApi(page);
    await page.goto("/score");
    await expect(page.getByTestId("server-status")).toHaveAttribute("data-state", "ready");
    await enter(page, `>my protein\n${SEQ.toLowerCase().slice(0, 4)}\n${SEQ.slice(4)}`);
    await scoreButton(page).click();

    await expect(result(page)).toHaveAttribute("data-complete", "true");
    await expect(result(page)).toHaveAttribute("data-done", String(SEQ.length));
    await expect(page.getByTestId("live-label")).toHaveText("Zero-shot, ESM-2 35M");
    await expect(page.getByTestId("heatmap")).toBeVisible();
    // The most harmful substitution of the mock's grid: the last residue, the last amino acid.
    await expect(page.getByTestId("top-harmful").locator("li").first()).toContainText("K8Y");
    // No supervised model for a live sequence.
    await expect(page.getByText(/Supervised/)).toHaveCount(0);

    // The sequence goes only in the request body: not in the page's URL, nor any request's.
    const score = requests.find((r) => r.url().endsWith("/v1/score"))!;
    expect(score.postDataJSON()).toEqual({ sequence: SEQ });
    expect(page.url()).not.toContain(SEQ);
    for (const r of requests) expect(r.url()).not.toContain(SEQ);
  });

  test("validation errors appear at the input and nothing is sent", async ({ page }) => {
    const requests = await mockApi(page);
    await page.goto("/score");
    await enter(page, "MKXTB");
    await scoreButton(page).click();
    const error = page.getByTestId("sequence-error");
    await expect(error).toContainText(
      "“X” at position 3 is not one of the 20 canonical amino acids",
    );
    await expect(error).toContainText("(and 1 more)");
    await expect(page.getByLabel("Protein sequence")).toHaveAttribute("aria-invalid", "true");
    // Fixing the input clears the error as you type.
    await enter(page, SEQ);
    await expect(error).toBeEmpty();
    expect(requests.filter((r) => r.url().includes("/v1/"))).toEqual([]);
  });

  test("a 422 from the API is shown at the input too", async ({ page }) => {
    await mockApi(page, {
      score: (route) =>
        route.fulfill({
          status: 422,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/problem+json",
          },
          body: JSON.stringify({
            type: "urn:mutascope:problem:validation-error",
            title: "Validation error",
            status: 422,
            detail: "body.sequence",
            instance: "/v1/score",
            errors: [
              {
                loc: ["body", "sequence"],
                msg: "Value error, the server says no",
                type: "value_error",
              },
            ],
          }),
        }),
    });
    await page.goto("/score");
    await enter(page);
    await scoreButton(page).click();
    await expect(page.getByTestId("sequence-error")).toHaveText("The server says no");
    await expect(page.getByTestId("live-notice")).toHaveCount(0);
  });

  test("429 shows a friendly message with the Retry-After time", async ({ page }) => {
    await mockApi(page, {
      score: (route) =>
        problem(route, 429, "rate-limited", "Rate limit for score exceeded.", {
          "Retry-After": "30",
        }),
    });
    await page.goto("/score");
    await enter(page);
    await scoreButton(page).click();
    await expect(page.getByTestId("live-notice")).toContainText(
      "Too many requests from your address. Try again in 30 seconds.",
    );
    // The button counts down instead of sending a request the server would refuse.
    await expect(scoreButton(page)).toBeDisabled();
    await expect(scoreButton(page)).toHaveText(/Try again in (30|29|28) s/);
    await expect(result(page)).toHaveCount(0);
  });

  test("503 scoring-busy shows a friendly message with the Retry-After time", async ({ page }) => {
    await mockApi(page, {
      score: (route) =>
        problem(route, 503, "scoring-busy", "Every scoring slot stayed busy for 10 s.", {
          "Retry-After": "5",
        }),
    });
    await page.goto("/score");
    await enter(page);
    await scoreButton(page).click();
    await expect(page.getByTestId("live-notice")).toContainText(
      "The server is busy scoring another sequence. Try again in 5 seconds.",
    );
  });
});

test("cold start: says the server is waking up, then scores", async ({ page }) => {
  const wakeUp = gate();
  await mockApi(page, {
    health: async (route) => {
      await wakeUp.opened;
      await json(route, { status: "ok", version: "0.6.0" });
    },
  });
  await page.goto("/score");
  const status = page.getByTestId("server-status");
  await expect(status).toHaveAttribute("data-state", "waking");
  await expect(status).toContainText("Waking up the server…");
  await expect(scoreButton(page)).toBeDisabled();
  await expect(scoreButton(page)).toHaveText("Waking up the server…");
  // The reader can type meanwhile; no error is shown for a sleeping server.
  await enter(page);
  await expect(page.getByTestId("sequence-error")).toBeEmpty();
  await expect(page.getByTestId("live-notice")).toHaveCount(0);

  wakeUp.open();
  await expect(status).toHaveAttribute("data-state", "ready");
  await expect(page.getByText("Up to 400 residues on this server.")).toBeVisible();
  await scoreButton(page).click();
  await expect(result(page)).toHaveAttribute("data-complete", "true");
});

test.describe("precise mode", () => {
  test("reads the length limit from /ready", async ({ page }) => {
    const requests = await mockApi(page, { ready: { precise_max_length: 5 } });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await expect(page.getByText("Up to 5 residues on this server.")).toBeVisible();
    await enter(page);
    await expect(page.getByTestId("sequence-error")).toContainText(
      "Precise mode takes at most 5 residues on this server (this sequence has 8)",
    );
    await scoreButton(page).click();
    expect(requests.filter((r) => r.url().includes("/v1/"))).toEqual([]);
    // Fast mode has no such limit.
    await page.getByLabel(/Fast/).check();
    await expect(page.getByTestId("sequence-error")).toBeEmpty();
  });

  test("follows the SSE stream to the full landscape", async ({ page }) => {
    const requests = await mockApi(page, {
      streams: [
        (route) =>
          sse(
            route,
            event(0, "queued", job("queued")) +
              event(1, "running", job("running")) +
              event(2, "progress", progress(4, 0, 4)) +
              event(3, "progress", progress(8, 4, 8)) +
              event(4, "succeeded", job("succeeded", 8, SEQ, grid())),
          ),
      ],
    });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await enter(page);
    await scoreButton(page).click();
    await expect(result(page)).toHaveAttribute("data-complete", "true");
    await expect(result(page)).toHaveAttribute("data-method", "masked_marginal");
    await expect(page.getByTestId("live-label")).toHaveText("Zero-shot, ESM-2 35M");
    await expect(page.getByTestId("job-progress")).toHaveCount(0);
    const create = requests.find((r) => r.url().endsWith("/v1/jobs"))!;
    expect(create.postDataJSON()).toEqual({ sequence: SEQ, model: "esm2_35m" });
    const stream = requests.find((r) => r.url().endsWith("/stream"))!;
    expect(await stream.headerValue("last-event-id")).toBeNull();
  });

  test("a dropped stream reconnects with Last-Event-ID and resumes", async ({ page }) => {
    const resume = gate();
    const requests = await mockApi(page, {
      streams: [
        // The first connection drops after one batch: no final status.
        (route) =>
          sse(route, event(0, "running", job("running")) + event(1, "progress", progress(4, 0, 4))),
        async (route) => {
          await resume.opened;
          await sse(
            route,
            event(2, "progress", progress(8, 4, 8)) +
              event(3, "succeeded", job("succeeded", 8, SEQ, grid())),
          );
        },
      ],
    });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await enter(page);
    await scoreButton(page).click();

    // Half filled, batch by batch, and saying it is reconnecting.
    await expect(result(page)).toHaveAttribute("data-done", "4");
    await expect(result(page)).toHaveAttribute("data-complete", "false");
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "4");
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "8");
    const connection = page.getByTestId("connection");
    // The reconnect is held, so the page stays in "reconnecting" until it is let through.
    await expect(connection).toHaveAttribute("data-state", "reconnecting");
    await expect(connection).toContainText("Connection lost. Reconnecting (attempt 1)");

    resume.open();
    await expect(result(page)).toHaveAttribute("data-complete", "true");
    await expect(result(page)).toHaveAttribute("data-done", "8");
    const streams = requests.filter((r) => r.url().endsWith("/stream"));
    expect(streams).toHaveLength(2);
    expect(await streams[0]!.headerValue("last-event-id")).toBeNull();
    expect(await streams[1]!.headerValue("last-event-id")).toBe("1");
  });

  test("shows that it is reconnecting while the connection is down", async ({ page }) => {
    const resume = gate();
    let reconnects = 0;
    await mockApi(page, {
      streams: [
        (route) =>
          sse(route, event(0, "running", job("running")) + event(1, "progress", progress(4, 0, 4))),
        // Down: the first reconnect is refused (backoff 1 s, then 2 s); the second is held.
        async (route) => {
          reconnects++;
          if (reconnects === 1) return route.abort("connectionreset");
          await resume.opened;
          await sse(route, event(2, "succeeded", job("succeeded", 8, SEQ, grid())));
        },
      ],
    });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await enter(page);
    await scoreButton(page).click();
    const connection = page.getByTestId("connection");
    await expect(connection).toHaveAttribute("data-state", "reconnecting");
    await expect(connection).toContainText("Connection lost. Reconnecting (attempt 2)");
    resume.open();
    await expect(result(page)).toHaveAttribute("data-complete", "true");
    expect(reconnects).toBe(2);
  });

  test("cancel calls DELETE and keeps the rows scored so far", async ({ page }) => {
    const never = gate();
    const requests = await mockApi(page, {
      streams: [
        (route) =>
          sse(
            route,
            event(0, "queued", job("queued")) +
              event(1, "running", job("running")) +
              event(2, "progress", progress(2, 0, 2)),
          ),
        // The reconnect hangs: the job is still running when the reader cancels.
        async (route) => {
          await never.opened;
          await route.abort();
        },
      ],
      cancel: (route) => json(route, job("cancelled", 2)),
    });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await enter(page);
    await scoreButton(page).click();
    await expect(result(page)).toHaveAttribute("data-done", "2");

    await page.getByTestId("cancel-button").click();
    await expect(page.getByTestId("live-notice")).toContainText(
      "Cancelled after 2 of 8 residues. The rows scored so far are shown",
    );
    await expect(page.getByTestId("cancel-button")).toHaveCount(0);
    await expect(result(page)).toHaveAttribute("data-complete", "false");
    await expect(scoreButton(page)).toBeEnabled();
    const deletes = requests.filter((r) => r.method() === "DELETE");
    expect(deletes.map((r) => new URL(r.url()).pathname)).toEqual(["/v1/jobs/job-1"]);
  });

  test("an identical earlier job is answered at once from the cache", async ({ page }) => {
    await mockApi(page, {
      createJob: (route) => json(route, { ...job("succeeded", 8, SEQ, grid()), cached: true }, 202),
    });
    await page.goto("/score");
    await page.getByLabel(/Precise/).check();
    await enter(page);
    await scoreButton(page).click();
    await expect(result(page)).toHaveAttribute("data-complete", "true");
  });
});

test("an example loads into the input and links to its assay", async ({ page }) => {
  await mockApi(page);
  await page.goto("/score");
  await page.getByRole("button", { name: "Ubiquitin (yeast, 128 residues)" }).click();
  await expect(page.getByLabel("Protein sequence")).toHaveValue(/^MQIFVKTLTG/);
  await expect(page.getByText("128 residues.")).toBeVisible();
});
