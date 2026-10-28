# 0044 — Visit counting with GoatCounter

- Status: accepted
- Date: 2026-10-28
- Amends: ADR 0042 (decision 9, "No analytics"), ADR 0040 (section 4: "the site has no
  analytics")

## Context

The site has counted nothing since it launched (ADR 0042, decision 9). Now that it is public,
the author wants to know how many people visit, which pages they read and where they come from.
The author's personal site (abdullahhanif.me) already uses GoatCounter, an open-source,
cookieless counter, and a local dashboard reads its API.

Two constraints from earlier decisions still hold. A sequence typed into `/score` must never
leave the request body to the API (ADR 0040, section 4). And the site must stay fast and
accessible (ADR 0042).

## Decision

1. **GoatCounter, as its own site** (`mutascope.goatcounter.com`), in the same account as the
   personal site, so one read-only API token covers both.
2. **Production only.** The root layout renders the counter only when `VERCEL_ENV` is
   `production` (`countsVisits` in `src/lib/site.ts`). Preview deployments, CI and local builds
   never load the script, so they neither count nor contact GoatCounter. GoatCounter's script
   also ignores `localhost` and private addresses.
3. **Pinned script.** `https://gc.zgo.at/count.v5.js`, GoatCounter's versioned file, with its
   published SHA-384 as `integrity`; the browser refuses a changed file. A new version is a
   change to `GOATCOUNTER` in `src/lib/site.ts`.
4. **One count per page shown, path only.** The script's own count on load is off
   (`no_onload`); `VisitCounter` counts once when the script loads and again on every new
   path, because most moves in this site are client-side navigations, which the script alone
   does not see. It sends `pathname` only, never the query string. The sequence on `/score`
   is never in the URL (ADR 0040), so it cannot reach GoatCounter either way.
5. **What GoatCounter receives:** the path, the page title, the referrer, the browser's
   user-agent and screen size; it derives the country from the IP address, which it does not
   store. No cookies, nothing in local storage.
6. **Said on the site.** The About page states how visits are counted, without cookies, and
   that a scored sequence is never sent.

## Alternatives

- **No counting** (ADR 0042): no idea who uses the site.
- **Vercel Web Analytics:** also cookieless, but its numbers stay in Vercel's dashboard, beside
  nothing else; GoatCounter's API feeds the same dashboard as the personal site.
- **Counting on the API (Cloud Run logs):** sees only live scoring, not who reads the
  Explorer or the benchmarks, which is most of the site.

## Consequences

- One more third-party request on each page in production: a ~9 KB script, loaded after the
  page is interactive, plus one small request per page shown. Lighthouse is asserted on local
  production builds, where the counter is off, so it does not measure this request.
- Counts start with the first production deploy after this change; there is no history before
  it.
- Tests: unit tests for `VisitCounter` (the pinned script, one count per new path, path only)
  and `countsVisits`; a Playwright test that a non-production build makes no request to
  GoatCounter; the About test checks the note.
