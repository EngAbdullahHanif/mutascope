# 0037 — Website foundation: the bundle on the site's own CDN, validated twice

- Status: accepted
- Date: 2026-10-18
- Builds on: ADR 0028 (the website bundle, its schemas and checksums), ADR 0030 (pins are
  commit hashes), ADR 0034 (CI, required checks)

## Context

Phase 5 builds the website. It must work with the API asleep (Cloud Run scales to zero; cold
start about 20 s), so the 217 protein landscapes are read as static files, never through the API.
The bundle (`bundle-v1.0.0`, 223 files, 29 MB) is public on the Hub at a pinned commit, with a
`SHA256SUMS` and JSON Schemas generated from the Pydantic models that wrote it. The API already
checks that bundle at startup (ADR 0028, 0030). The website needs the same guarantees from a
different runtime, the browser, without hand-written types that could drift from the schema.

## Decision

### 1. The bundle is copied into the site at build time, from the pinned commit

`web/bundle.json` pins `repo`, `path`, `tag` and `revision`. The revision is
`export.publish.revision`, and `tests/test_web_pins.py` fails if they differ. `pnpm build` runs
`scripts/fetch-bundle.ts` first:

- it downloads `SHA256SUMS` from that commit, then every file it lists (a path outside the
  bundle directory, a malformed line or a duplicate is refused);
- each file must have its listed digest, and every protein file the manifest names must be
  listed with the manifest's digest;
- manifest, benchmarks and all 217 protein files must pass the generated Zod validators
  (0.6 s for all of them). Each JSON Schema shipped in the bundle must equal the copy in
  `docs/schema/bundle/` that the validators were generated from;
- files go to a staging directory and replace `public/data/` only when all of that passes. A
  failed or tampered download leaves the previous data in place;
- a later run re-verifies `public/data/` against its `SHA256SUMS` and pin, and downloads again
  only if that fails.

The site then serves `/data/...` from its own CDN (Vercel). Pages never call the Hub or the API
for bundle data. An end-to-end test blocks every request that does not go to the site and checks
that the Explorer still loads and validates real files.

**Rejected: fetching from the Hub in the browser.** It ties every page view to a third party's
availability and rate limits, and its redirects to a CDN make caching and CORS someone else's
policy. **Rejected: committing the bundle.** 29 MB of generated data in git, duplicated on every
bundle change. **Rejected: Git LFS.** Vercel's LFS support is a project setting that is easy to
lose, and the Hub already is the versioned store.

### 2. Zod validators generated from the bundle's JSON Schemas, checked again at runtime

`scripts/gen-bundle-schemas.ts` turns `docs/schema/bundle/*.schema.json` into
`src/lib/bundle/schemas.generated.ts` (committed; CI regenerates it and fails on a diff). It
handles exactly the keywords Pydantic emits for these models (`$ref`, `anyOf`, `const`, `enum`,
`prefixItems` as a fixed tuple, `minimum`/`maximum`, `pattern`, `additionalProperties`) and
fails on any other keyword. `additionalProperties: false` becomes `z.strictObject`, so unknown
fields are refused, as the Pydantic models refuse them.

**Rejected: `json-schema-to-zod` and similar converters.** They silently skip keywords they do
not support, which would make the validator looser than the schema. A generator of under 300 lines that
fails loudly is easier to trust and to explain.

In the browser, `src/lib/bundle/load.ts` checks every file it loads: the schema version first
(a newer bundle fails with that message, not a list of field errors), then the Zod validator,
and for a protein file the manifest's sha256 of the gzip bytes, then that it holds the assay the
manifest says. The download already checked all of this, but the runtime check guards what the
browser actually received (a CDN or proxy that rewrites files, a stale cache across deploys).
Protein files are fetched as gzip and decompressed with `DecompressionStream`. If a host serves
them decoded (`Content-Encoding: gzip`), the digest cannot be checked, so that is an error, not a
skipped check (unit-tested; an end-to-end test checks that `next start` serves the gzip bytes).

### 3. Design system: one accent, and a fixed diverging scale for effects

The tokens and rules are in `web/DESIGN.md`. The effect scale is ColorBrewer RdBu (11 classes)
with a pure-white midpoint: blue = harmful (negative), white = neutral, red = tolerated
(positive), symmetric around 0 (`symmetricMax`). It is interpolated in OKLab and is the same in
light and dark mode. Missing values get their own grey, never white. The interface accent is a
single teal, away from both poles. Every text and accent token pair passes WCAG AA (the lowest
is 5.5:1). Tests keep the CSS tokens and the TypeScript stops equal, check that lightness falls
steadily on both arms, and check that the two arms match within ΔL 0.08.

### 4. Tooling and pins

Next.js 16 (App Router, Turbopack), React 19, Tailwind 4, shadcn/ui (new-york), Zod 4, Vitest 5,
Playwright 1.64, all pinned exactly (`.npmrc` `save-exact`). Two packages are held below their
latest major on purpose. TypeScript stays at 5.9.3 because openapi-typescript 7.13 (`make types`)
requires `^5`. ESLint stays at 9.39.5 because eslint-config-next 16.4's plugins (`react`,
`import`, `jsx-a11y`) do not support ESLint 10. Dependabot covers `/web` and does not propose those
two majors. Scripts run as TypeScript directly on Node 26 (type stripping; erasable syntax only),
with no tsx or ts-node.

CI gets a sixth job, `web (lint, typecheck, unit tests, build)`. To make it a required check, it
must be added to the `main` ruleset (ADR 0034).

## Consequences

- A new bundle is a new tag and commit in `conf/export/default.yaml`, then the same commit in
  `web/bundle.json` (the pin test enforces it), regenerated validators if the schema changed,
  and a redeploy. The site cannot serve a bundle it was not built with.
- Builds need network access to the Hub and Google Fonts (`next/font` self-hosts the fonts at
  build time; readers never contact Google).
- The Explorer's placeholder loads the manifest, the benchmarks and the smallest protein through
  the runtime checks, so the data path is tested in a real browser before any chart exists.
