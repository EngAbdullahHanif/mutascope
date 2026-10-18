# 0038 — Explorer heatmap: a canvas raster, and a neutral point per quantity

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0012 (MSA region), ADR 0025 (deployed model, out-of-fold predictions),
  ADR 0028 (bundle grids), ADR 0037 (website foundation, effect scale)
- Amends: ADR 0037, section 3 ("symmetric around 0") for quantities in DMS units

## Context

Phase 5 Task 2 is the Explorer: a protein picker and, for each of the 217 assays, a 20 × L
heatmap of every substitution. The model can be switched between the default, ESM-2 650M and
SaProt where it exists. The page has predicted and measured views, a per-residue summary,
tooltips, the top 10 substitutions at each end, CSV export, keyboard navigation and a table
alternative. Proteins run from 37 to 3,423 residues, so up to 68,460 cells. The budget is a
1,000-residue protein drawn within 300 ms of its data loading.

Two questions had to be settled first: how to draw that many cells, and where white (neutral)
sits on the diverging scale.

## Decision

### 1. Routes: `/explore` and `/explore/[assayId]`, prerendered

The picker is `/explore`. Each protein is `/explore/[assayId]`, prerendered for every
manifest entry (`generateStaticParams` reads the downloaded manifest through the same validator,
`dynamicParams = false`, so any other id is a 404). The page then loads its protein file in the
browser through `load.ts`, as ADR 0037 requires. `/explorer` (the Task 1 placeholder) redirects
to `/explore`. The bundle-status box moved to the About page, where it reports provenance.

### 2. One canvas, sized to the window, drawn from a one-pixel-per-cell raster

A view (model × predicted/measured × mask) is rasterised once into an L × 20 image, one pixel
per cell, through a 257-colour lookup table of the effect scale (`effectLut`, which is tested
equal to `effectColor` at every step). The heatmap is a horizontal scroll area holding a spacer
L × zoom pixels wide, with a sticky canvas the width of the window. Each frame draws only the
visible slice of the raster, scaled up with smoothing off (`drawImage`), then the overlays for
the visible columns: the wild-type marks, the mean bars, the ruler, the sequence once letters
fit (≥ 8 px), and the hover and focus outlines. Zoom levels are 3–24 px per residue. The
default is the widest zoom that fits the whole protein, otherwise 3 px with scrolling.
Ctrl/⌘ + wheel zooms around the pointer.

**Rejected: SVG or DOM cells.** 68,460 nodes, each restyled on every view change. **Rejected:
one canvas as wide as the protein.** At 24 px, 3,423 residues is 82,152 px, beyond browsers'
canvas size limits. **Rejected: a chart library (visx/D3 scales).** The geometry is a regular
grid; the library would add weight and own nothing that matters here.

### 3. Where white sits: 0 for zero-shot, the assay's median for DMS scores

Zero-shot scores are log-ratios against the wild type, so 0 *is* the wild type, and the scale
stays centred on 0 ("Harmful", "Neutral", "Tolerated").

ProteinGym's `DMS_score` has no common zero. In the bundle, 55 of the 217 assays have fewer
than 20% of their measured scores below 0, and 56 have more than 80% below 0. So 111 assays
(51%) have at least 80% of their scores on one side of 0. Centred on 0, those landscapes would
be almost entirely red ("tolerated") or almost entirely blue ("harmful"), which says something
about each assay's units, not about the mutations. The bundle has no wild-type score to centre
on.

So views in DMS units (measured, and the supervised prediction, which is in the same units) are
centred on the assay's **median measured score**. The legend says so: its ends read "Less fit",
"Assay median", "Fitter", and a note under it says that white is the median, not the wild type.
The measured and predicted views share one centre and one half-width, so switching between them
compares like with like.

**Rejected: rank (percentile) colouring.** It hides the shape of the distribution (a bimodal
assay looks uniform). **Rejected: per-assay min-to-max.** DESIGN.md already forbids it, because
the white point then moves with the data's skew.

### 4. Half-width: the 99th percentile of |value − centre|, ends marked "≤" / "≥"

With the half-width at the largest |value| (`symmetricMax`), a few extreme variants turn
everything else near-white. In 44 of the 217 assays the largest deviation from the median, over
measured and predicted values together, is at least twice the 99th percentile. In the Zika env
assay it is 11.1 times larger. The half-width is the 99th percentile of the deviations instead
(`robustHalfWidth`). Values beyond it are drawn at the end colours, and the legend's end ticks
read "≤ −2.75" and "≥ 1.69", so the clipping is visible. For DMS views the percentile is taken
over the measured and predicted values together (point 3).

### 5. The wild type is not a cell value

Zero-shot grids store 0 at the wild type, and supervised and measured grids store null. In
every view the wild type is set to "no value", so it never enters a mean, a top-10 list or the
CSV. The canvas draws it as the page colour with an ink bar, a mark that reads as neither an
effect nor "missing" in either theme. The legend has a swatch for it ("Wild type").

### 6. Honest labels everywhere a number appears

The model selector lists each option with its label ("Supervised (n = 686)", "Zero-shot"). A
badge shows the current view's label ("Measured" for the assay's own values). The tooltip and
the screen-reader description repeat it, every CSV row carries it, and the table caption
names it. Zero-shot defaults show their reason ("No supervised model: no structure."). The five
assays whose ESM-2 benchmark used the MSA region (ADR 0012) show a note with the region, and a
bar under the ruler marks it.

### 7. Accessibility

The scroll area is a focusable `role="application"` element, labelled with the view and the
keys. Arrows move one cell, Page Up/Down ten residues, Home/End the ends, +/− zoom. A
polite live region reads the focused cell (mutation, label, model, value, intervals, measured
value, out-of-fold prediction, residue status, imputed structure), the same facts as the
tooltip. A table alternative shows the current view, 25 residues per page (the whole of a large
protein would be 68,000 DOM cells). The canvas reads its ink colours from the theme tokens at
draw time and redraws on a theme change. Cell colours are the same in both themes (ADR 0037).

### 8. The budget is measured in the browser and tested

The page records the time from the protein file being loaded and validated to the end of the
first frame drawn. That span covers decoding, building the view, rasterising and drawing. It is
reported as `data-render-ms` and as a `performance.measure`. A Playwright test fails if the two
proteins nearest 1,000 residues (MSH2 at 934, CAR11 at 1,154) take 300 ms or more. Measured on
an M4 Pro (production build, 20 cold loads each, median / max):

| Protein | Residues | CPU 1× | CPU 4× throttled |
| --- | --- | --- | --- |
| PIN1 `Tsuboyama_2023_1I6C` | 39 | 15.4 / 16.5 ms | 60.7 / 66.1 ms |
| MSH2 `Jia_2020` | 934 | 30.6 / 31.3 ms | 122.3 / 137.3 ms |
| CAR11 `Meitlis_2020_gof` | 1,154 | 26.4 / 45.1 ms | 100.5 / 119.2 ms |
| Zika env `Sourisseau_2019` | 3,423 | 56.3 / 57.2 ms | 210.1 / 218.3 ms |

A view change (a new model, measured or predicted, the mask) takes 9–12 ms at about 1,000
residues and 33 ms for the largest protein (CPU 1×).

## Consequences

- DESIGN.md's "symmetric around 0" now reads "symmetric around the quantity's neutral point":
  0 for log-ratios, the assay median for DMS scores. `symmetricMax` stays for charts of
  quantities that do have a natural zero.
- Colours in DMS views compare substitutions within an assay, never across assays. The same
  colour on two proteins can mean different things, and the legend's numbers say what.
- Everything drawn on the canvas is also available as text: the live region, the tooltip, the
  table and the CSV.
- The 3D structure view (linked hover) attaches to the heatmap's hovered column in the next
  task.
