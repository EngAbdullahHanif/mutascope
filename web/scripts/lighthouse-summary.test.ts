import { median, table } from "./lighthouse-summary.ts";

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
});
