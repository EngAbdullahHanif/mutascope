# MutaScope design system

The website should look like a careful scientific instrument: quiet surfaces and type, with
colour kept for the data. These rules hold for every page and component. The tokens are in
[`src/app/globals.css`](src/app/globals.css). Components use the Tailwind utilities those tokens
generate (`bg-background`, `text-muted-foreground`, `rounded-md`, `shadow-sm`, `font-mono`…),
never raw hex values, pixel sizes or ad-hoc shadows.

## Principles

1. **Colour carries meaning, not decoration.** Data has the only strong colours on a page: the
   effect scale. The interface uses neutrals and one accent.
2. **Honest labels.** Every prediction is labelled `Zero-shot` or `Supervised (n = …)`.
   Effects are "functional effect in DMS assays", never "pathogenic". The footer's
   not-for-clinical-use note is on every page.
3. **Readable without colour.** Status uses an icon and text as well as colour. The effect
   legend names its ends ("Harmful", "Neutral", "Tolerated"). A missing value has its own
   swatch, never the neutral colour.
4. **Works with the API asleep.** Pages read the bundle from the site's own `/data`. Only
   live scoring calls the API.

## Colour

All values are OKLCH. Light and dark are separate token sets (`:root` and `.dark`), not an
automatic inversion. The theme follows the system until the reader picks one (next-themes, class
on `<html>`).

| Token                      | Light             | Dark              | Use                                                                 |
| -------------------------- | ----------------- | ----------------- | ------------------------------------------------------------------- |
| `background`               | `0.99 0.002 250`  | `0.17 0.008 260`  | Page                                                                |
| `foreground`               | `0.22 0.012 260`  | `0.95 0.004 250`  | Body text, headings                                                 |
| `card` / `popover`         | white             | `0.205 0.009 260` | Raised surfaces                                                     |
| `muted`                    | `0.965 0.004 250` | `0.24 0.01 260`   | Subtle fills, placeholders                                          |
| `muted-foreground`         | `0.47 0.014 260`  | `0.72 0.012 255`  | Secondary text, axes, captions                                      |
| `border` / `input`         | `0.91 0.006 250`  | `0.30 0.01 260`   | Hairlines, fields                                                   |
| `primary` = `accent-brand` | `0.50 0.09 195`   | `0.76 0.10 190`   | **The one accent**: primary buttons, focus ring, links, "ok" status |
| `accent` (shadcn role)     | `0.95 0.006 250`  | `0.27 0.01 260`   | Hover and active fill. A grey, not a second hue                     |
| `destructive`              | `0.55 0.20 27`    | `0.68 0.18 25`    | Errors only                                                         |

**Accent.** Teal sits away from both poles of the effect scale (blue, red), so interface colour
is never confused with data. It never appears inside a chart.

**Contrast** (WCAG 2.1, computed from these tokens; AA needs 4.5:1 for text):

| Pair                                | Light | Dark |
| ----------------------------------- | ----- | ---- |
| foreground / background             | 16.8  | 16.5 |
| muted-foreground / background       | 6.6   | 7.7  |
| muted-foreground / muted            | 6.2   | 6.6  |
| primary-foreground / primary        | 5.5   | 9.3  |
| primary (as link text) / background | 5.5   | 9.3  |

Re-check this table when you change a token.

## Effect colours (the data scale)

A diverging scale, symmetric around the quantity's neutral point:

- **blue = harmful** (below neutral: lower fitness),
- **white = neutral**,
- **red = tolerated** (above neutral).

The neutral point is 0 for zero-shot log-ratios (0 is the wild type) and **the assay's median
measured score** for anything in DMS units (measured scores, supervised predictions). ProteinGym's
`DMS_score` has no common zero: in 111 of 217 assays at least 80% of scores sit on one side of 0
(ADR 0038). A DMS-unit legend says so: ends "Less fit", "Assay median", "Fitter".

| Stop | −1        | −0.8      | −0.6      | −0.4      | −0.2      | 0         | +0.2      | +0.4      | +0.6      | +0.8      | +1        |
| ---- | --------- | --------- | --------- | --------- | --------- | --------- | --------- | --------- | --------- | --------- | --------- |
| Hex  | `#053061` | `#2166ac` | `#4393c3` | `#92c5de` | `#d1e5f0` | `#ffffff` | `#fddbc7` | `#f4a582` | `#d6604d` | `#b2182b` | `#67001f` |

- The arms are **ColorBrewer RdBu** (11 classes), which stays distinguishable under protanopia
  and deuteranopia. The midpoint is pure white. Lightness falls steadily from 0 towards each
  end and at about the same rate on both sides (unit-tested: OKLab ΔL < 0.08), so equal
  |scores| look equally strong.
- **Interpolation** is in OKLab: `effectColor(value, maxAbs)` in
  [`src/lib/color/diverging.ts`](src/lib/color/diverging.ts) for cells, and
  `linear-gradient(in oklab …)` for the legend. The CSS tokens `--color-effect-*` hold the same
  stops; a test fails if they drift apart.
- **Symmetric domain.** The scale always runs from centre − m to centre + m, so the neutral
  point is white whatever the data's skew. Never use a min-to-max domain. Landscapes use
  `robustHalfWidth` (m = 99th percentile of |value − centre|) and pass `clamped` to the legend,
  whose ends then read "≤" / "≥"; `symmetricMax` (m = largest |value|) is for small charts
  without outliers.
- **Missing** (not measured, no structure): `--color-effect-missing` (`#bdbdbd`), with its own
  legend swatch. White means "neutral", never "no value".
- **Same in light and dark.** A cell's colour means one thing everywhere, and a screenshot
  reads the same in either theme. In dark mode, give heatmaps a 1px `border` frame so the white
  midpoint does not bleed into the page.
- **Effect colours are for effects only.** Not for buttons, status or categories.
- **Wild type** is never a value on the scale. In a heatmap its cell is the page colour with an
  ink bar, and the legend shows it (`showWildType`).
- **Every chart that uses the scale shows an `EffectLegend`**
  ([`src/components/charts/effect-legend.tsx`](src/components/charts/effect-legend.tsx)). It
  takes `maxAbs`, a `title` naming the quantity ("ESM-2 650M score", "DMS score"), `decimals` and
  `showMissing`, plus `center`, `ends`, `clamped` and `showWildType` where they apply. Its ticks
  use a true minus sign and the mono face.

## Charts (Benchmarks, ADR 0041)

- **Built from the bundle only.** Every number is a row of `benchmarks.json`, looked up by its
  keys in `src/lib/benchmarks/select.ts`; a missing row fails the build. Chart code computes
  nothing.
- **Differences, not side-by-side bars.** Two models are compared by their paired difference
  (`A − B`) with its 95% interval and a dashed line at 0. A model's own value and CI go in a
  table, captioned that it is not a comparison. Coverage is drawn against its nominal level.
- **Ink only.** Marks `foreground`, gridlines `border`, reference lines dashed
  `muted-foreground`. No accent and no effect colours inside a chart. A second series, or an
  interval that includes 0, is a hollow marker, and the row says so in words.
- **Every chart states its assay count** in its header (`ChartFigure`), and every value is also
  written beside its row (mono, tabular, true minus sign), so nothing depends on hover or colour.
- `DotPlot` (`src/components/charts/dot-plot.tsx`) is server-rendered HTML: marks positioned in
  percent, small multiples on one shared axis (`niceAxis`).

## Typography

| Role                                  | Face                             | Notes                                        |
| ------------------------------------- | -------------------------------- | -------------------------------------------- |
| UI, prose, headings                   | **Inter** (`font-sans`)          | Self-hosted by `next/font` at build time     |
| Sequences, numbers, identifiers, code | **JetBrains Mono** (`font-mono`) | Add `tabular` where numbers stack in columns |

Type scale (1.2 ratio, 15px body):

| Utility     | Size / line height | Use                           |
| ----------- | ------------------ | ----------------------------- |
| `text-xs`   | 12 / 16px          | Axis ticks, legends, badges   |
| `text-sm`   | 13 / 20px          | Tables, captions, nav, footer |
| `text-base` | 15 / 24px          | Body                          |
| `text-lg`   | 18 / 28px          | Lead paragraphs               |
| `text-xl`   | 21.6 / 29.6px      | Section headings (h2)         |
| `text-2xl`  | 25.9 / 33.6px      | Sub-page titles               |
| `text-3xl`  | 31.1 / 38.4px      | Page titles (h1)              |
| `text-4xl`  | 37.3 / 44px        | Landing hero only             |

Headings use `font-semibold tracking-tight`. Body text stays within `max-w-prose`. Amino-acid
sequences are always mono and never hyphenated.

## Spacing, radius, elevation

- **Spacing:** a 4px grid (`--spacing: 0.25rem`; `p-1` = 4px). Pages use `px-4` gutters,
  `max-w-6xl` content and `gap-6`/`gap-8` between blocks. Use the scale; no arbitrary values.
- **Radius:** `rounded-sm` 4px (swatches, small chips), `rounded-md` 6px (buttons, inputs),
  `rounded-lg` 8px (cards), `rounded-xl` 12px (rare: large panels). Heatmap cells are square.
- **Shadows:** two levels only. `shadow-sm` for cards, `shadow-md` for popovers and tooltips.
  They are tinted per mode (`--shadow-color`). Never stack a shadow on a dashed placeholder.

## Components

- **shadcn/ui** (`components.json`, style `new-york`). Add components with
  `pnpm dlx shadcn@4.22.1 add <name>`, then check the diff: the CLI once installed an unrelated
  npm package named `cn` instead of using `@/lib/utils` (see build log 39).
- **Layout:** `SiteHeader` (logo, nav, theme toggle; on phones the nav moves to its own row),
  `SiteFooter` (not-for-clinical-use note, GitHub, Hugging Face), and `PageHeader` /
  `Placeholder` for page tops.
- **Status:** an icon (lucide) plus words ("valid", "failed"), coloured `primary` or
  `destructive`. Never colour alone.
- **Focus:** every interactive element shows the `ring` on `:focus-visible`. There is a
  skip-to-content link.

## Heatmap (Explorer, ADR 0038)

- Canvas, never SVG: one sticky canvas the width of the window over a scroll spacer; each frame
  draws only the visible columns from a one-pixel-per-cell raster. Rows 14 px, columns 3–24 px.
- Above the grid: mean effect per residue (signed bars from the neutral line, same scale), a
  1-indexed ruler, and the sequence once columns are 8 px or wider. The amino-acid axis is
  `AA_ORDER`, top to bottom.
- Canvas ink (text, marks, outlines) comes from the theme tokens at draw time (`--foreground`,
  `--muted-foreground`, `--border`, `--ring`), so it follows light and dark mode.
- Anything drawn is also text: the tooltip, the live region for the focused cell, the table
  alternative and the CSV. Each one carries the label ("Supervised (n = …)", "Zero-shot" or
  "Measured").

## Structure (ADR 0039)

- 3Dmol.js, below the heatmap, mounted after the heatmap's first frame. Cartoon on a transparent
  background (the page colour shows through in either theme), inside a bordered panel.
- **Colour by mean effect** (default): each residue is its summary-bar colour (same lookup
  table, centre, half-width, clipping), with the same `EffectLegend` and the view's label chip.
- **Colour by pLDDT**: ColorBrewer Purples, light (≤ 40) to dark (100), interpolated in OKLab
  (`src/lib/color/sequential.ts`, tokens `--color-plddt-0…6`, a test keeps them equal). Purple
  is on neither pole of the effect scale nor the accent, so it is never read as an effect. Its
  legend marks the low-confidence threshold.
- **Low confidence** (pLDDT below the bundle's threshold, 70): a thin trace instead of a
  cartoon, in both colourings, explained beside the viewer.
- **Highlight**: a sphere on the residue's CA in the `foreground` ink; the residue's code, PDB
  number, pLDDT and mean are written under the viewer. In the heatmap, a residue pointed at in
  3D gets its whole column outlined in the `ring` colour.
- **Outside a partial structure**: a coverage strip of the whole target in the viewer's colours,
  residues without a 3D position in the missing grey, with its own swatch. Never white.
- No structure: an info box saying why, never an empty panel. The panel always states that the
  3D view is not keyboard-accessible and that the heatmap and table are.

## Live scoring (ADR 0040)

- `/score` is the only page that calls the API. A sleeping server is a state, not an error:
  "Waking up the server…" with a spinner, and the submit button says the same while disabled.
- Every live result carries one label chip, `Zero-shot, <model>` (from the response), and the
  method in words. No 3D view, no supervised model, nothing about measurements.
- A running precise job: a `primary` progress bar with residues done / total, the connection's
  state in words ("Connection lost. Reconnecting (attempt n)…"), unscored columns grey with a
  "Not scored yet" legend swatch.
- Input errors sit under the textarea (`aria-invalid`, `aria-describedby`), naming the first
  bad residue's 1-indexed position. Other errors are one sentence saying what to do, with the
  `Retry-After` wait as a time; the button counts down meanwhile.

## Data

- Bundle files are loaded only through [`src/lib/bundle/load.ts`](src/lib/bundle/load.ts). It
  checks the schema version, the generated Zod validators and the manifest's sha256 for protein
  files (for structure files, the sha256 their protein file lists, and their sequence). A file that fails is shown as an error, never as data with a warning.
- API types come only from [`src/lib/api-types.ts`](src/lib/api-types.ts) (`make types`). Bundle
  types come only from `src/lib/bundle/schemas.generated.ts` (`pnpm schemas`). Never hand-write
  either.
