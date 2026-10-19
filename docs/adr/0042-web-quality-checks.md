# 0042 — Website quality checks: Lighthouse, axe, 375 px, JavaScript budget

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0037 (website foundation), ADR 0038 (Explorer; addendum: prerendered text),
  ADR 0039 (3Dmol loaded after the heatmap), ADR 0041 (content pages)

## Context

Phase 5 Task 6 is about the website's quality before launch: how fast and how accessible it is,
whether it works on a phone, and what happens when something breaks. Until now, the CI `web` job
built the site but did not run Playwright, and nothing measured performance or accessibility.

## Decisions

1. **Lighthouse CI against the production build.** `@lhci/cli` 0.15.1 (Lighthouse 12.6.1),
   pinned in `web/package.json`, runs against `next start`. It tests the Landing page and
   `/explore/GFP_AEQVI_Sarkisyan_2016` (the Landing page's first example: a structure, the
   supervised model, 238 residues). It does 3 runs each, mobile (Lighthouse's default profile)
   and desktop (`preset=desktop`). It asserts on the median run: performance ≥ 0.90,
   accessibility ≥ 0.95 (`web/lighthouserc.json`). Reports stay on the runner (`filesystem`
   target, uploaded as this run's artifact); nothing goes to LHCI's public storage. There is no
   Lighthouse GitHub Action: the CLI from the lockfile is the pinned version.
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
10. **A new CI job, not yet required**: `web quality (Lighthouse, axe, bundle size)`. It runs the
    whole Playwright suite (the first time CI runs it), the JavaScript budget and Lighthouse. It
    uses only actions the repository already pins. `make web-quality` runs the same steps
    locally.

## Results (M4 Pro, local `next start`, median of 3)

| Page                                | Before: mobile perf / a11y | Before: desktop | After: mobile    | After: desktop |
| ----------------------------------- | -------------------------- | --------------- | ---------------- | -------------- |
| `/`                                 | 0.94 / 0.96                | 1.00 / 0.96     | 0.94–0.95 / 1.00 | 1.00 / 1.00    |
| `/explore/GFP_AEQVI_Sarkisyan_2016` | 0.81 / 0.92                | 0.99 / 0.92     | 0.91–0.92 / 1.00 | 1.00 / 1.00    |

The "after" ranges are the medians of two separate measurements.

Initial JavaScript, gzip, on main: 194–242 kB per page, of which about 160 kB is React and the
Next.js runtime. 3Dmol (162 kB gzip) loads only on protein pages, after the heatmap, and on the
Landing page when the browser is idle.

## Consequences

- The mobile Explorer score has little headroom (0.91–0.92 against 0.90). GitHub's runners are
  slower and noisier than the M4 Pro, which is one reason the job is not required yet. Make it
  required once it has passed on a few PRs.
- Raising a JavaScript budget is a decision for the PR description, not a silent edit.
- A new page needs adding to the axe and 375 px lists in `e2e/quality.spec.ts`.
