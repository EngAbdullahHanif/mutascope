# 0045 — Dark by default; no visit note on the About page

- Status: accepted
- Date: 2026-11-06
- Amends: ADR 0044 (decision 6), web/DESIGN.md (theme followed the system)

## Context

The site followed the system's light or dark setting until the reader picked one, so most
first visits opened light. The author wants the site to open dark. The About page also
carried a note on how visits are counted (ADR 0044, decision 6), which the author wants
removed from the page.

## Decision

1. **Dark by default.** next-themes runs with `defaultTheme="dark"` and `enableSystem={false}`:
   every reader starts dark, whatever the system says; the header toggle switches to light,
   and the choice is kept in local storage as before. Both token sets, and the axe checks in
   light and dark (ADR 0042), are unchanged.
2. **No visit note on About.** The paragraph is removed. Everything else in ADR 0044 holds:
   GoatCounter in production only, the pinned script, path only, no cookies, and a scored
   sequence never leaves the request body (ADR 0040). ADR 0044 still documents what is sent.

## Consequences

- The README GIF (`web/media/explorer-gif.spec.ts`) sets light in local storage, so it stays
  as it was.
- `e2e/site.spec.ts` checks that a light system still opens dark and that light survives a
  reload; the About test no longer checks the note.
