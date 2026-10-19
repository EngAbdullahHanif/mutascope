import { existsSync } from "node:fs";
import { join } from "node:path";

import { adrUrl, SITE } from "./site";

describe("ADR links", () => {
  it("point at files that exist in docs/adr", () => {
    for (const n of [3, 10, 11, 12, 14, 15, 16, 19, 21, 24, 25, 26, 27, 28, 29, 31, 34, 41]) {
      const url = adrUrl(n);
      const file = url.split("/blob/main/")[1]!;
      expect(existsSync(join(process.cwd(), "..", file)), file).toBe(true);
    }
  });

  it("refuse an ADR that is not listed", () => {
    expect(() => adrUrl(999)).toThrow();
  });
});

describe("SITE", () => {
  it("uses the production origin for canonical URLs", () => {
    expect(SITE.url).toBe("https://mutascope.abdullahhanif.me");
  });
});
