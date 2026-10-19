import { hasMarker, initialScripts, overBudget, type Budget } from "./bundle-size.ts";

const budget: Budget = {
  pages: { "/": { html: "index.html", maxGzipKb: 100 } },
  no3Dmol: ["/"],
};

describe("initialScripts", () => {
  it("reads script tags and script preloads, once each, and nothing else", () => {
    const html = [
      '<link rel="stylesheet" href="/_next/static/chunks/a.css"/>',
      '<link rel="preload" as="script" fetchPriority="low" href="/_next/static/chunks/p.js"/>',
      '<link rel="preload" as="font" href="/_next/static/media/f.woff2"/>',
      '<script src="/_next/static/chunks/a.js" async=""></script>',
      '<script src="/_next/static/chunks/p.js" async=""></script>',
      '<script src="https://example.org/x.js"></script>',
      "<script>self.__next_f.push([1])</script>",
    ].join("");
    expect(initialScripts(html)).toEqual([
      "/_next/static/chunks/a.js",
      "/_next/static/chunks/p.js",
    ]);
  });
});

describe("hasMarker", () => {
  it("recognises 3Dmol by its class names", () => {
    expect(hasMarker("class GLViewer{constructor(){}}")).toBe(true);
    expect(hasMarker("export{GLModel as M}")).toBe(true);
    expect(hasMarker("function createRoot(){}")).toBe(false);
  });
});

describe("overBudget", () => {
  it("fails a page over its gzip budget and passes one at it", () => {
    const at = { route: "/", scripts: [], rawBytes: 0, gzipBytes: 100_000 };
    expect(overBudget([at], budget)).toEqual([]);
    expect(overBudget([{ ...at, gzipBytes: 100_001 }], budget)).toEqual([
      "/: 100.0 kB gzip > budget 100 kB",
    ]);
  });
});
