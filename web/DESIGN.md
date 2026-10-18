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

A diverging scale, symmetric around 0:

- **blue = harmful** (negative: lower fitness than wild type),
- **white = neutral** (0),
- **red = tolerated** (positive).

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
- **Symmetric domain.** The scale always runs from −m to +m (`symmetricMax`), so 0 is white
  whatever the data's skew. Never use a min-to-max domain.
- **Missing** (not measured, no structure): `--color-effect-missing` (`#bdbdbd`), with its own
  legend swatch. White means "neutral", never "no value".
- **Same in light and dark.** A cell's colour means one thing everywhere, and a screenshot
  reads the same in either theme. In dark mode, give heatmaps a 1px `border` frame so the white
  midpoint does not bleed into the page.
- **Effect colours are for effects only.** Not for buttons, status or categories.
- **Every chart that uses the scale shows an `EffectLegend`**
  ([`src/components/charts/effect-legend.tsx`](src/components/charts/effect-legend.tsx)). It
  takes `maxAbs`, a `title` naming the quantity ("ESM-2 650M score", "DMS score"), `decimals` and
  `showMissing`. Its ticks use a true minus sign and the mono face.

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

## Data

- Bundle files are loaded only through [`src/lib/bundle/load.ts`](src/lib/bundle/load.ts). It
  checks the schema version, the generated Zod validators and the manifest's sha256 for protein
  files. A file that fails is shown as an error, never as data with a warning.
- API types come only from [`src/lib/api-types.ts`](src/lib/api-types.ts) (`make types`). Bundle
  types come only from `src/lib/bundle/schemas.generated.ts` (`pnpm schemas`). Never hand-write
  either.
