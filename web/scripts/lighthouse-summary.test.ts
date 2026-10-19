import { heading, machineLabel, median, metricsRow, table } from "./lighthouse-summary.ts";

describe("lighthouse summary", () => {
  it("takes the median of three runs, as the assertions do", () => {
    expect(median([0.92, 0.86, 0.92])).toBe(0.92);
    expect(median([0.5, 0.9])).toBe(0.7);
  });

  it("lists every run and the medians per page", () => {
    const run = (url: string, performance: number) => ({
      url,
      summary: { performance, accessibility: 1 },
    });
    const text = table("mobile", [
      run("http://127.0.0.1:3200/", 0.95),
      run("http://127.0.0.1:3200/", 0.93),
      run("http://127.0.0.1:3200/", 0.94),
    ]);
    expect(text).toBe(
      "| mobile | `/` | 0.95, 0.93, 0.94 | **0.94** | 1.00, 1.00, 1.00 | **1.00** |",
    );
  });

  it("shows the metrics behind a median run and the machine's benchmark", () => {
    const report = {
      environment: { benchmarkIndex: 1234.4 },
      audits: {
        "first-contentful-paint": { numericValue: 752.6 },
        "largest-contentful-paint": { numericValue: 2926 },
        "total-blocking-time": { numericValue: 186 },
        "cumulative-layout-shift": { numericValue: 0 },
      },
    };
    expect(metricsRow("mobile", "http://127.0.0.1:3200/explore/X", report)).toBe(
      "| mobile | `/explore/X` | 753 | 2926 | 186 | 0.000 | – | 1234 |",
    );
  });

  it("labels scores from a machine where they are not asserted", () => {
    expect(machineLabel(["--machine", "no-GPU runner"])).toBe("no-GPU runner");
    expect(machineLabel([])).toBeNull();
    expect(heading("no-GPU runner")).toMatch(
      /^### Lighthouse on a no-GPU runner: recorded, not asserted/,
    );
    expect(heading(null)).toBe(
      "### Lighthouse (budgets: performance ≥ 0.90, accessibility ≥ 0.95, median of 3)",
    );
  });
});
