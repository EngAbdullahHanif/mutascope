# 0042 — Website quality checks: Lighthouse, axe, 375 px, JavaScript budget

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0037 (website foundation), ADR 0038 (Explorer; addendum: prerendered text),
  ADR 0039 (3Dmol loaded after the heatmap), ADR 0041 (content pages)
- Follow-up (2026-10-19): CI's runners have no GPU. The Lighthouse budgets are asserted on a
  machine with GPU rendering (`make web-quality`); CI records its scores without asserting them,
  and the 3D views load only when on screen (ADR 0039 and 0041 addenda). See "Follow-up" at the
  end.

## Context

Phase 5 Task 6 is about the website's quality before launch: how fast and how accessible it is,
whether it works on a phone, and what happens when something breaks. Until now, the CI `web` job
built the site but did not run Playwright, and nothing measured performance or accessibility.

## Decisions

1. **Lighthouse CI against the production build.** `@lhci/cli` 0.15.1 (Lighthouse 12.6.1),
   pinned in `web/package.json`, runs against `next start`. It tests the Landing page and
   `/explore/GFP_AEQVI_Sarkisyan_2016` (the Landing page's first example: a structure, the
   supervised model, 238 residues). It does 3 runs each, mobile (Lighthouse's default profile)
   and desktop (`preset=desktop`). The budgets apply to the median run: performance ≥ 0.90,
   accessibility ≥ 0.95 (`web/lighthouserc.json`). They are asserted by `make web-quality` on a
   machine with GPU rendering, not in CI (see "Follow-up"). Reports stay where they were made
   (`filesystem` target; in CI, this run's artifact); nothing goes to LHCI's public storage.
   There is no Lighthouse GitHub Action: the CLI from the lockfile is the pinned version.
2. **The budgets were fixed before any fix and are not lowered.** The baseline missed them
   (Explorer, mobile: performance 0.81, accessibility 0.92). The site changed to meet them, and
   the budgets stayed as they were.
3. **axe in Playwright.** `@axe-core/playwright` 4.14.0 checks WCAG 2.2 A and AA plus axe's
   best practices, with zero violations allowed. It runs on `/`, `/explore`, one protein page
   (heatmap and structure drawn), `/score` (API mocked, ready), `/benchmarks`, `/how-it-works`,
   `/about` and the 404 page, each in light and dark (`e2e/quality.spec.ts`).
4. **No sideways scroll at 375 px**, on the same pages plus the largest protein (3,423
   residues). A wide heatmap scrolls inside its own box, never the page. The Explorer keeps its
   layout on a phone: no separate mobile view was needed.
5. **The header folds below 640 px.** The five links do not fit beside the logo, nor on a
   line of their own at 360 px. Below `sm` they sit behind a "Menu" disclosure button
   (`aria-expanded`, `aria-controls`), which closes when a link is followed.
6. **In-text links are underlined.** Teal against body text is about 1.2:1, so colour alone does
   not mark a link (WCAG 1.4.1). DESIGN.md records the rule.
7. **JavaScript budget per page** (`scripts/bundle-size.ts`, `web/bundle-budget.json`). A page's
   initial JavaScript is every script its prerendered HTML loads (`<script src>` and script
   preloads), measured as gzip. Turbopack prints no per-route sizes, so this script measures
   them. Budgets are main's measured baseline plus 10%, rounded up to 5 kB. The same script
   fails if 3Dmol is in the Landing page's initial JavaScript. It recognises 3Dmol by class
   names its minified build keeps (`GLViewer`, `GLModel`), and also fails if no chunk at all
   contains them, so the check cannot go blind.
8. **Error boundaries.** `app/error.tsx` keeps the header and footer, says the page failed and
   offers "Try again". It never shows the thrown message, which can carry server details in
   production; it shows a server error's digest. `app/global-error.tsx` replaces the root
   layout with its own minimal document. The 404 page gets its own title; Next.js already
   serves it with status 404 and `noindex`.
9. **No analytics**, and the error pages report nothing anywhere.
10. **A new CI job, not yet required**: `web quality (Lighthouse, axe, bundle size)`. It fails on
    the whole Playwright suite (axe, 375 px and the rest; the first time CI runs it) and on the
    JavaScript budget. It runs Lighthouse too, but only records and reports the scores, labelled
    "no-GPU runner" (see "Follow-up"). It uses only actions the repository already pins.
    `make web-quality` runs the same steps and asserts the Lighthouse budgets.

## Results (M4 Pro with GPU rendering, local `next start`, median of 3)

The budgets are met on the Mac, not in CI (see "Follow-up" for CI's numbers).

| Page                                | Before: mobile perf / a11y | Before: desktop | After: mobile    | After: desktop |
| ----------------------------------- | -------------------------- | --------------- | ---------------- | -------------- |
| `/`                                 | 0.94 / 0.96                | 1.00 / 0.96     | 0.94–0.95 / 1.00 | 1.00 / 1.00    |
| `/explore/GFP_AEQVI_Sarkisyan_2016` | 0.81 / 0.92                | 0.99 / 0.92     | 0.91–0.92 / 1.00 | 1.00 / 1.00    |

The "after" ranges are the medians of two separate measurements, before the 3D views were made
to load on sight. After that change (follow-up): Landing 0.95 / 1.00 mobile and 1.00 / 1.00
desktop; Explorer 0.95 / 1.00 mobile and 1.00 / 1.00 desktop.

Initial JavaScript, gzip, on main: 194–242 kB per page, of which about 160 kB is React and the
Next.js runtime. 3Dmol (162 kB gzip) loads only when a 3D view comes on screen: on protein pages
after the heatmap's first frame, and on the Landing page when the browser is also idle.

## Consequences

- The Lighthouse budgets are checked only when someone runs `make web-quality` on a machine with
  GPU rendering, for example before a release. CI shows the no-GPU scores on every PR, so a
  regression is visible there even though it does not fail the job.
- Make the CI job required once it has passed on a few PRs: its failing checks (axe, 375 px,
  bundle size, the end-to-end suite) do not depend on a GPU.
- Raising a JavaScript budget is a decision for the PR description, not a silent edit.
- A new page needs adding to the axe and 375 px lists in `e2e/quality.spec.ts`.

## Follow-up (2026-10-19): CI has no GPU

**What happened.** The first CI run scored far below the Mac, though accessibility was 1.00
everywhere:

| Page | Mobile: CI | Mobile: Mac | Desktop: CI | Desktop: Mac |
| --- | --- | --- | --- | --- |
| Landing | 0.67 | 0.94–0.95 | 0.70 | 1.00 |
| Explorer GFP | 0.72 | 0.91–0.92 | 0.88 | 1.00 |

**Why.** The runner's CPU benchmark (Lighthouse's `benchmarkIndex`) was 2,850–3,090, against
about 3,900 on the Mac, and first and largest paint matched the Mac's. What differed was total
blocking time:

- Landing: 7.3 s mobile and 1.7 s desktop, against 5–150 ms on the Mac;
- Explorer: 1.2 s mobile.

GitHub's runners have no GPU, so Chrome paints and runs WebGL in software on the main thread.
The Landing structure's turn redraws every frame (60–180 ms each, still running 40 s after
load), and setting up the Explorer's viewer was one task of up to 0.9 s. Forcing software
rendering on the Mac (`--use-angle=swiftshader`) reproduced it: desktop 0.83 (Landing) and 0.78
(Explorer).

**What changed.**

1. The 3D views load only when on screen (ADR 0039 and 0041 addenda). That helps every reader
   without GPU acceleration and saves 3Dmol for readers who never reach it.
2. The Lighthouse budgets are asserted on a machine with GPU rendering (`make web-quality`). CI
   records and reports its scores as "no-GPU runner" and does not fail on them. No GitHub-hosted
   runner without a GPU can meet the mobile budget on a protein page, even with no 3D at all: in
   software rendering on the Mac, with both 3D views switched off, mobile Explorer reached 0.87,
   because software painting delays the largest paint (3.9 s simulated against 2.9 s).
3. axe, 375 px, the end-to-end suite and the JavaScript budget still fail the CI job.

**With the change, in software rendering on the Mac** (the closest local stand-in for CI):
mobile Explorer about 0.87 (0.87 in all three runs), desktop Explorer 0.99. Landing scores 0.73
(mobile) and 0.90 (desktop): the figure is on screen at load in both of Lighthouse's windows, so
it still loads and turns there.

The budgets stay as they were. Neither lowering them for CI nor turning the turn off when WebGL
reports software rendering (`failIfMajorPerformanceCaveat`) was taken: the second is keyed to
exactly the condition CI runs in, too close to a measurement trick.
