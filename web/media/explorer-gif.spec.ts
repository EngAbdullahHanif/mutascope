/**
 * Frames for the README's animated Explorer (`make readme-gif`): GFP's landscape with the
 * pointer moving along one row of the heatmap while the 3D view marks the same residue. The
 * page is the production build, unchanged; `tools/readme/make_gif.py` turns the frames into
 * `docs/img/explorer.gif`.
 */
import { mkdirSync, rmSync } from "node:fs";

import { expect, test } from "@playwright/test";

const ASSAY = "GFP_AEQVI_Sarkisyan_2016";
const OUT = ".media-frames";
/** Heatmap geometry (heatmap.tsx): the grid starts below the bar, ruler and sequence. */
const GRID_TOP = 36 + 18 + 16 + 4;
const ROW_H = 14;
const ROW = 18; // W: the cell tooltip opens below the grid, not over it
const PX_PER_RESIDUE = 3; // the zoom the page opens at for this protein
const STEP = 4; // residues per frame

test.use({ viewport: { width: 1280, height: 1300 }, colorScheme: "light", deviceScaleFactor: 2 });
test.setTimeout(180_000);

test("Explorer frames", async ({ page }) => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT);
  await page.goto(`/explore/${ASSAY}`);
  await expect(page.getByTestId("render-timing")).toHaveAttribute("data-render-ms", /\d/);
  const structure = page.getByRole("region", { name: "Structure" });
  await structure.scrollIntoViewIfNeeded();
  const viewer = page.getByTestId("structure-viewer");
  await expect(viewer).toHaveAttribute("data-state", "ready");
  // Heatmap and structure on screen together, below the sticky header.
  await page.evaluate(() => window.scrollTo(0, 240));
  // The viewer fits the protein (zoomTo) on its first styled frame: zoom only after that.
  await page.waitForTimeout(1500);
  // About twice as close, so the marker on the hovered residue is easy to see.
  const canvas = (await viewer.locator("canvas").first().boundingBox())!;
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
  for (let i = 0; i < 4; i++) {
    await page.mouse.wheel(0, 100);
    await page.waitForTimeout(100);
  }

  const grid = (await page.getByRole("application").boundingBox())!;
  const box = (await structure.boundingBox())!;
  const top = grid.y - 130; // from the legend above the heatmap
  const clip = {
    x: box.x - 8,
    y: top,
    width: box.width + 16,
    height: box.y + box.height + 8 - top,
  };
  const y = grid.y + GRID_TOP + ROW * ROW_H + ROW_H / 2;
  const manifest = (await (await page.request.get("/data/manifest.json")).json()) as {
    proteins: { assay_id: string; length: number }[];
  };
  const { length } = manifest.proteins.find((p) => p.assay_id === ASSAY)!;
  let frame = 0;
  for (let position = 0; position < length; position += STEP) {
    await page.mouse.move(grid.x + (position + 0.5) * PX_PER_RESIDUE, y);
    await expect(viewer).toHaveAttribute("data-highlighted-position", String(position));
    await page.waitForTimeout(80); // the marker is drawn on the next frame
    await page.screenshot({ path: `${OUT}/${String(frame++).padStart(3, "0")}.png`, clip });
  }
});
