# 0043 — Phase 5 close: website and launch

- Status: accepted
- Date: 2026-10-20
- Changes: no code. Records what Phase 5 delivered, checked against the repository, GitHub and
  the live site and API on 2026-10-20; lists what is open, and which open items stand between
  the project and a `v1.0.0` label (see "Open and weak")

## Context

Phase 5 (weeks 8–10) built the website on top of the Phase 4 service and launched it: the
foundation (ADR 0037), the Explorer and heatmap (ADR 0038), the structure view (ADR 0039), live
scoring (ADR 0040), the content pages (ADR 0041) and the quality checks (ADR 0042), plus the
service follow-ups that ADR 0035 carried over (ADR 0036). This record checks the result as it
stands on `main` at `1147197` (release `v0.11.0`) and says what is still open. Nothing here was
changed to make a check pass.

## What is live

All values were checked on 2026-10-20 from the source named in the last column.

| What | Value | Source |
|---|---|---|
| Website | https://mutascope.abdullahhanif.me (Vercel; `main` deploys to production) | `tools/smoke/launch.py` |
| API | https://mutascope-api-2lvmztrsha-ew.a.run.app, version 0.11.0 (`release.yml` run for `v0.11.0` succeeded) | `/health`, GitHub Actions |
| API readiness | bundle `1bfc294…` (format 1.1.0), 217 proteins, fp32 ESM-2 35M and 150M, default `esm2_35m`, `precise_max_length` 400 | `/ready` |
| Launch smoke test | 14 of 14 passed at 11:42 UTC: `/health`, a protein, seven pages, the protein page's title, a CORS preflight allowed from the site and refused from another origin, a fast-mode score from the site's origin with its CORS header | `uv run python tools/smoke/launch.py` (defaults: one allowed origin) |
| CI on `main` | `ci.yml` on `1147197`: all seven jobs passed | GitHub Actions |
| GitHub repository | public; website field set to the site | `gh repo view` |
| API image on GHCR | public: an anonymous pull of the `v0.11.0` manifest returns 200 | `ghcr.io` token and manifest requests |
| Cold start | `/health` 20.4 s from cold (v0.6.0, ADR 0040's measurement); not re-measured after a confirmed idle period | ADR 0040 |

### Headline numbers

The Landing page reads these three from `benchmarks.json` in `bundle-v1.1.0` (`web/src/lib/benchmarks/select.ts`,
`headlines`). The file served by the site has the same sha256 as the build's checked copy
(`6fd57688…`), and the live Landing page shows exactly these values.

| Headline | Value | Assays |
|---|---|---|
| SaProt 650M − ESM-2 650M, zero-shot, Spearman | +0.046, paired 95% CI [+0.028, +0.062] | 213 (with a structure) |
| Deployed ridge (`seq_struct`) − zero-shot SaProt, contiguous split, Spearman | +0.092 [+0.065, +0.115]; modulo split +0.135 | 213 |
| Test-fold coverage of the 90% intervals (contiguous, split-matched calibration) | 90.8% mean; 0 assays more than 10 points below | 213 |

### Lighthouse

Budgets (ADR 0042): performance ≥ 0.90, accessibility ≥ 0.95, median of 3, asserted with GPU
rendering only.

`make web-check` then `make web-quality` on an Apple M4 Pro (GPU rendering), built from
`1147197`, 2026-10-20: exit 0. Vitest 151, Playwright 72 passed (1 skipped: the opt-in live test),
every page within its JavaScript budget, no 3Dmol on Landing. Performance / accessibility,
median of 3:

| Page | Mobile | Desktop |
|---|---|---|
| Landing | 0.93 / 1.00 | 1.00 / 1.00 |
| Explorer, GFP | 0.95 / 1.00 | 1.00 / 1.00 |

The Landing's mobile runs were 0.63, 0.96 and 0.93: one of three below budget. The median is
what is asserted. A first attempt in the same session failed two Playwright tests because it
reused a `.next` build from an older branch; `make web-quality` does not build, so `make
web-check` must run first.

CI, **no-GPU runner** (recorded, not asserted; `web quality` job of the `0b20756` merge,
performance / accessibility, median of 3):

| Page | Mobile | Desktop |
|---|---|---|
| Landing | 0.66 / 1.00 | 0.69 / 1.00 |
| Explorer, GFP | 0.93 / 1.00 | 1.00 / 1.00 |

On that runner, the Landing page's total blocking time on mobile is about 140 s (136,798 ms on
`0b20756`). Every successful `main` run checked from `6b83b0b` onward shows about the same, except
`9a1ceeb` at 6.2 s. ADR 0042's follow-up measured 7.3 s on its first CI run. The cause has not
been investigated (see "Weak").

## Checklist audit

Done and checked:

- Bounded inference and one ONNX thread on Cloud Run (ADR 0036); live metrics behind the token;
  the site's two origins in CORS (the smoke test's preflights).
- Foundation, Explorer and heatmap, structure view, live scoring, content pages, the narrow-screen
  menu and the quality checks (ADRs 0037–0042), deployed on Vercel.
- Launch checks (`tools/smoke/launch.py`): passing, above.
- README for first-time visitors, with the hero image (`docs/img/explorer.png`, tracked).
- GHCR visibility: the image is public (above). This item was still listed as open.
- Stale-cache fix (#77, in `v0.11.0`): bundle URLs carry their content's sha256 or the pinned
  revision, and a file that still fails its sha256 check is fetched once more with
  `cache: "reload"`. Checked on the live site in a persistent (non-private) Chromium profile:
  three visits to `/explore/F7YBW8_MESOW_Aakre_2015` (the assay of the original report) requested
  `proteins/F7YBW8_MESOW_Aakre_2015.json.gz?v=6b55d502…`, equal to that protein's sha256 in the
  manifest, and drew the heatmap without an error. The browser that first showed the error, with
  its 1.0.0-era cache, was not rechecked: the old copies sit under unversioned URLs, which the
  site no longer requests.

## Open and weak

### Blockers for `v1.0.0`

1. **Precise-mode limit misses its own rule.** A precise job is meant to finish in about 3
   minutes (ADR 0029, 0031). With one ONNX thread (ADR 0036, section 7), 400 residues took 293 s
   live. `conf/deploy/cloudrun.yaml` still sets `MUTASCOPE_API_PRECISE_MAX_LENGTH` to 400, and its
   comment gives the two-thread time (163 s). To do: measure at about 250–300 residues on the
   live service and set the limit from the result. Changing it means a deploy.
2. **Clients' addresses in the logs are not checked.** `MUTASCOPE_API_TRUSTED_PROXIES` is
   `169.254.169.126/32`, the one address found in the first deploy's logs (ADR 0035). Nobody has
   checked since that the API's access lines now show visitors' own addresses. Per-client rate
   limits, the one-job-per-client rule and stream slots all depend on it (ADR 0032). To do: a
   Cloud Logging query on `jsonPayload.client` after a few visits from known addresses.
3. **Release check.** `make web-quality` must pass on the release commit (ADR 0042). It passed
   on `1147197` (see "Lighthouse"). The fix for item 1 changes only the deploy config, but the
   check must still be rerun on the commit that is labelled `v1.0.0`.

### Can follow `v1.0.0`

- No favicon: `/favicon.ico`, `/icon.svg`, `/icon.png` and `/apple-icon.png` all return 404 on the live
  site, so every page logs a console error (Lighthouse best practices).
- No demo video and no write-up. The README is done.
- `web quality (Lighthouse, axe, bundle size)` is not a required check yet (the ruleset requires
  the other six jobs).
- Sentry: no project, so no `MUTASCOPE_API_SENTRY_DSN`.
- Optional, under heavier load: move gzip and ETag of large bodies off the event loop
  (ADR 0036, section 6).
- Lighthouse noise: 3Dmol's chunk has no source map; about 130 kB of unused JavaScript (mostly
  3Dmol, fetched after idle) and about 13 kB of legacy polyfills.
- `global-error.tsx` always renders light.

### Weak

- **Landing page without a GPU.** Lighthouse budgets hold only with GPU rendering (ADR 0042's
  follow-up). On CI's runner, the Landing page's blocking time is about 140 s on mobile, so a
  visitor whose browser renders WebGL in software would likely find the page slow. Not
  investigated.
- **Cold start.** The site works with the API asleep, but the first live score waits for a cold
  start of about 20 s.

## Release

Versions are set only by release-please. The `v1.0.0` label is a release-please step taken by the
author, after the blockers above, and merging that release PR deploys the API.
