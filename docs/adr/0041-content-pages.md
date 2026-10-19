# 0041 — Content pages: Landing, Benchmarks, How it works, About, and SEO

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0014 (paired comparisons only), ADR 0016 (figures compute nothing), ADR 0024
  (CV+ intervals and their test-fold coverage), ADR 0026 (Phase 3 close: the Binding weak point,
  coverage of measured variants proves nothing), ADR 0028 (bundle, `benchmarks.json`, caveat
  rule), ADR 0037 (site foundation), ADR 0039 (structure files)

## Context

Phase 5 Task 5 replaces the four placeholder pages. The site's numbers already exist: the bundle's
`benchmarks.json` holds every summary, paired comparison, function-type gain, caveat and coverage
statistic the website may show, each copied from a results table by the export (ADR 0028). The
risk on these pages is not computing a number wrongly; it is typing one in, choosing a flattering
one, or drawing two models' own intervals side by side as if that compared them.

## Decision

1. **Numbers are rows, found by their keys, at build time.** `benchmarks.json` is read from disk
   during `next build` (`benchmarksFromDisk`, the same validator as `load.ts`, after `pnpm build`
   has checked the file against `SHA256SUMS`) and rendered into the Landing and Benchmarks HTML.
   `src/lib/benchmarks/select.ts` looks each number up by model, split, metric, level and group;
   a lookup that finds no row or more than one throws, so a missing number fails the build rather
   than showing something else. Nothing on these pages computes a statistic. The pages read
   without script, and search engines see the numbers.
2. **The three headlines.**
   - *SaProt vs ESM-2, zero-shot:* `saprot_650m_af2 − esm2_650m`, overall Spearman, from
     `zero_shot_comparisons` (paired, 213 shared assays).
   - *Supervised gain on unseen positions:* the deployed model − its zero-shot comparator
     (`deployed_model`, `zero_shot_comparator`) under **contiguous**, with modulo beside it.
     Both splits hold out whole positions; contiguous is the harder of the two and the one the
     deployed intervals calibrate against at unmeasured positions. Random (the largest gain) is
     not a headline: it is not the task the website does (ADR 0026, decision 1).
   - *Interval coverage:* mean test-fold coverage of the 90% intervals under contiguous with
     split-matched calibration (`calibration`, statistic `mean`; the ADR 0024 headline), with the
     count of assays more than 10 points below target. `final_coverage` (0.800 / 0.900 on
     measured variants) is **not** used: it holds by construction (ADR 0026, weak point 11). The
     Benchmarks page says so next to it.
3. **Charts show differences, levels go in tables.** Every chart is a dot-and-interval plot of a
   paired difference (`model_a − model_b`) with a dashed line at 0, small multiples per split on
   one shared axis. A score set's own aggregate and CI appear only in a table captioned "compare
   models only with the paired differences". The one exception is coverage, which is a property
   of an interval procedure, not a model score: it is drawn against its nominal level, with a
   10th–90th percentile bar labelled as a spread, not a confidence interval (the bundle has no CI
   for it).
4. **Charts are HTML and ink.** Rows are server-rendered HTML with marks positioned in percent
   (no canvas, no chart library, no script). Marks are `foreground`, gridlines `border`, the
   reference line dashed `muted-foreground`; no accent and no effect colours (DESIGN.md:
   the accent never appears inside a chart; effect colours are for effects). The second series
   and "interval includes 0 / gain not shown" are hollow markers, always with the words too.
   Every chart states its assay count in its header, and every number is also written beside its
   row, so a reader never needs the hover or the colour.
5. **What is left out, and said.** The int8 ONNX rows of `zero_shot_comparisons` compare a
   rejected export with its fp32 source (ADR 0029); they are a deployment check, not a model
   result, and the caption says they are omitted. The by-size chart notes that its bins are a
   plain mean, not the ProteinGym aggregate (ADR 0025).
6. **The Binding caveat** is the file's own `caveats[].text`, shown under the function-type
   chart, citing ADR 0026 (weak point 7, where it is documented) and ADR 0028 section 8 (the rule
   that writes it). Binding's markers are hollow wherever `gain_shown` is false.
7. **How it works counts from the manifest.** Assays, measured variants, assays with a structure
   and the deployment split (198 ridge, 15 SaProt, 4 ESM-2 650M) are counted from
   `manifest.json` at build time. Protocol constants (the 300-variant rule, 10 points below
   target, 10,000 resamples, 1,022 residues) are definitions, not results, and are written in
   prose. The two upstream SaProt errors are described in words from ADR 0015.
8. **The Landing structure loads after first paint.** `next/dynamic` with `ssr: false` mounts it
   after hydration, and it fetches the protein file, the structure file (both through `load.ts`)
   and 3Dmol.js only when the browser is idle. It is a plain cartoon in the muted ink: an
   illustration, so no data colour and no legend. It turns slowly (`spin("y", 0.25)`, only while
   on screen); `prefers-reduced-motion: reduce` stops it or keeps it from starting, read through
   `useSyncExternalStore` so a change to the setting applies at once. The example protein is GFP,
   whose files are small (88 kB protein file).
9. **"Try your own sequence" is a link, not a form.** A form on the Landing would have to carry
   the sequence to `/score` in the URL or in storage, both of which ADR 0040 forbids. The box
   describes live scoring and links to `/score`.
10. **About is the author's text only.** A link to the author's site, GitHub and LinkedIn, and
    one sentence on AI use. There is no CV link, by choice: a CV is a document to keep current
    in two places, and the project itself serves that purpose.
    The bundle provenance box stays (an end-to-end test uses it).
11. **SEO.** `metadataBase` is `SITE.url` (`https://mutascope.abdullahhanif.me`). Every page
    gets its title, description, canonical URL, Open Graph and Twitter fields from one helper
    (`pageMetadata`; Next merges `openGraph` shallowly, so each page passes the full set). Each
    top-level route has an `opengraph-image.tsx` rendered at build by `next/og`; protein pages
    inherit the Explorer's. The image renderer reads no CSS, so `src/lib/og.tsx` holds the light
    theme's colours as hex (commented with their tokens) and the effect stops for the logo. The
    sitemap lists the six pages and every protein page in the manifest; `robots.txt` allows all
    but `/data/`.

## Tests

- Vitest: selectors find exactly one row and throw on none or two; the headlines fail if a row is
  missing; int8 rows and other metrics stay out of the zero-shot chart; axis maths; every cited
  ADR link names a file that exists.
- Playwright (`e2e/content.spec.ts`), expected values read from the served files: the three
  headlines equal the `benchmarks.json` rows; every chart shows an assay count; the Binding
  caveat text and its ADR 0026 link; the deployment counts equal the manifest's; the Landing
  structure is fetched only after the `load` event, turns, and stops under reduced motion (also
  when the setting changes); About's links; canonical URL and a PNG Open Graph image on every
  page; robots.txt and a sitemap of 6 + 217 URLs on the production origin.

## Consequences

- A bundle whose `benchmarks.json` lacks a row the pages use fails `next build`; the fix is in
  the export or in the selector, never a typed number.
- Benchmarks are in the HTML at build time, so a new bundle needs a new deploy (as for protein
  routes already).
- The header's navigation is 13 px wider than a 390 px phone (an existing issue of the Task 1
  header, not changed here).
