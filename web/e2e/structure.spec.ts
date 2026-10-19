/**
 * The 3D structure and the heatmap are linked both ways (ADR 0039). Assertions read the state
 * both views expose as data attributes (0-indexed target positions), never pixels or WebGL
 * output; the pointer only finds a residue in 3D by moving until the viewer reports one.
 */
import { expect, test, type Page } from "@playwright/test";

/** Heatmap geometry (heatmap.tsx): the grid starts below the bar, ruler and sequence. */
const GRID_TOP = 36 + 18 + 16 + 4;
const ROW_H = 14;

async function open(page: Page, assayId: string) {
  await page.goto(`/explore/${assayId}`);
  await expect(page.getByTestId("render-timing")).toHaveAttribute("data-render-ms", /\d/);
}

/**
 * The viewer mounts only once its box scrolls into view (ADR 0039 addendum). Scrolls the
 * section, which stays, not the placeholder, which is replaced as it comes into view.
 */
async function revealStructure(page: Page) {
  await page.getByRole("region", { name: "Structure" }).scrollIntoViewIfNeeded();
  await expect(page.getByTestId("structure-viewer")).toHaveAttribute("data-state", "ready");
}

/** Moves over the 3D canvas until the viewer reports a hovered residue; returns where. */
async function findResidueIn3D(page: Page): Promise<{ x: number; y: number; position: string }> {
  const viewer = page.getByTestId("structure-viewer");
  const canvas = viewer.locator("canvas").first();
  // Below the heatmap: bring it on screen, where the pointer can reach it.
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  for (let fy = 0.2; fy <= 0.8; fy += 0.04) {
    for (let fx = 0.2; fx <= 0.8; fx += 0.04) {
      const x = box.x + fx * box.width;
      const y = box.y + fy * box.height;
      await page.mouse.move(x, y);
      // 3Dmol.js reports a hover after `hoverDuration` (40 ms) without movement.
      await page.waitForTimeout(80);
      const position = await viewer.getAttribute("data-hovered-position");
      if (position) return { x, y, position };
    }
  }
  throw new Error("no residue found under the pointer in the 3D view");
}

test("hovering links the heatmap and the 3D structure both ways (PIN1)", async ({
  page,
  baseURL,
}) => {
  // The site works with the API asleep: nothing leaves its origin, the structure included.
  const origin = new URL(baseURL!).origin;
  const external: string[] = [];
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (new URL(url).origin === origin) return route.continue();
    external.push(url);
    return route.abort();
  });
  await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
  const heatmap = page.getByTestId("heatmap");
  const viewer = page.getByTestId("structure-viewer");
  await revealStructure(page);
  await expect(page.getByTestId("structure-a11y-note")).toContainText("heatmap");

  // Heatmap -> 3D: hovering residue 13's column (position 12) highlights it in the structure,
  // which stays mounted after scrolling back to the heatmap.
  await page.getByRole("application").scrollIntoViewIfNeeded();
  const grid = (await page.getByRole("application").boundingBox())!;
  const zoom = Number(await heatmap.getAttribute("data-zoom"));
  await page.mouse.move(grid.x + 12.5 * zoom, grid.y + GRID_TOP + 5 * ROW_H);
  await expect(heatmap).toHaveAttribute("data-hovered-position", "12");
  await expect(viewer).toHaveAttribute("data-highlighted-position", "12");
  await expect(viewer).toContainText("S13 · PDB residue 13");
  // Leaving the heatmap clears it, so what follows cannot be left over from here.
  await page.mouse.move(1, 1);
  await expect(viewer).toHaveAttribute("data-highlighted-position", "");
  await expect(heatmap).toHaveAttribute("data-linked-position", "");

  // 3D -> heatmap: the residue under the pointer is outlined in the heatmap.
  const hit = await findResidueIn3D(page);
  await expect(heatmap).toHaveAttribute("data-linked-position", hit.position);
  await expect(viewer).toHaveAttribute("data-highlighted-position", hit.position);
  await expect(heatmap).toHaveAttribute("data-hovered-position", "");

  // Clicking it selects that residue's column in the heatmap (keyboard focus moves there).
  await page.mouse.click(hit.x, hit.y);
  await expect(heatmap).toHaveAttribute("data-focus-position", hit.position);
  await expect(page.getByRole("application")).toBeFocused();
  const residue = `${Number(hit.position) + 1}`;
  await expect(page.getByTestId("heatmap-announcer")).toContainText(residue);
  expect(external).toEqual([]);
});

test("a click in 3D scrolls a long heatmap to the residue (ZIKV, partial structure)", async ({
  page,
}) => {
  await open(page, "A0A140D2T1_ZIKV_Sourisseau_2019");
  await revealStructure(page);
  await expect(page.getByTestId("coverage")).toContainText("residues 291–794 of 3,423");
  const scroller = page.getByRole("application");
  expect(await scroller.evaluate((el) => el.scrollLeft)).toBe(0);
  const hit = await findResidueIn3D(page);
  // The structure models residues 291–794 only: every hit is inside them.
  expect(Number(hit.position)).toBeGreaterThanOrEqual(290);
  expect(Number(hit.position)).toBeLessThan(794);
  await page.mouse.click(hit.x, hit.y);
  await expect(page.getByTestId("heatmap")).toHaveAttribute("data-focus-position", hit.position);
  const zoom = Number(await page.getByTestId("heatmap").getAttribute("data-zoom"));
  const { left, width } = await scroller.evaluate((el) => ({
    left: el.scrollLeft,
    width: el.clientWidth,
  }));
  const x = Number(hit.position) * zoom;
  expect(x).toBeGreaterThanOrEqual(left);
  expect(x).toBeLessThan(left + width);
});

test("the viewer and 3Dmol load only when the structure scrolls into view", async ({ page }) => {
  const fetched: string[] = [];
  page.on("request", (r) => {
    if (/\/data\/structures\/|\/_next\/static\/chunks\//.test(r.url())) fetched.push(r.url());
  });
  // A phone-sized window: the structure starts well below the fold.
  await page.setViewportSize({ width: 375, height: 812 });
  await open(page, "PIN1_HUMAN_Tsuboyama_2023_1I6C");
  await page.waitForTimeout(1000);
  await expect(page.getByTestId("structure-placeholder")).toBeAttached();
  await expect(page.getByTestId("structure-viewer")).toHaveCount(0);
  expect(fetched.filter((u) => u.includes("/data/structures/"))).toEqual([]);
  const chunksBefore = fetched.length;
  await revealStructure(page);
  // Scrolling to it fetched the structure file and new chunks (the viewer and 3Dmol).
  expect(fetched.filter((u) => u.includes("/data/structures/"))).toHaveLength(1);
  expect(fetched.length).toBeGreaterThan(chunksBefore + 1);
});

test("an assay without a structure says so instead of showing an empty viewer", async ({
  page,
}) => {
  await open(page, "P53_HUMAN_Giacomelli_2018_WT_Nutlin");
  await expect(page.getByTestId("no-structure")).toContainText("No 3D structure for this assay");
  await expect(page.getByTestId("structure-viewer")).toHaveCount(0);
});
