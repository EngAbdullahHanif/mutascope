"use client";

import "./globals.css";

/**
 * The root layout itself failed, so this replaces it, `<html>` included: no header, footer or
 * theme provider, so it is always light. Kept to plain markup so it cannot fail the same way.
 */
export default function GlobalError({ retry }: { error: unknown; retry: () => void }) {
  return (
    <html lang="en">
      <body className="flex min-h-dvh flex-col items-start gap-4 bg-background px-4 py-10 text-foreground">
        <title>Something went wrong · MutaScope</title>
        <main role="alert" className="mx-auto w-full max-w-6xl">
          <h1 className="text-3xl font-semibold tracking-tight">Something went wrong</h1>
          <p className="mt-2 max-w-prose text-lg text-muted-foreground">
            MutaScope failed to load. Try again, or come back later.
          </p>
          <div className="mt-6 flex gap-4">
            <button
              type="button"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
              onClick={() => retry()}
            >
              Try again
            </button>
            {/* A full reload, not next/link: the router may be what failed. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a className="px-1 py-2 text-sm text-primary underline underline-offset-4" href="/">
              Start page
            </a>
          </div>
        </main>
      </body>
    </html>
  );
}
