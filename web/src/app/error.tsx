"use client";

import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";

/**
 * A page that threw while rendering: the header and footer stay, the page says so and offers to
 * try again. Nothing is logged or sent anywhere (no analytics); a server error's digest is shown
 * so it can be matched with the server's own log.
 */
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  const digest = digestOf(error);
  return (
    <div role="alert">
      <PageHeader title="Something went wrong">
        This page failed to load. Trying again often works; the{" "}
        <Link
          className="text-primary underline underline-offset-4 hover:decoration-2"
          href="/explore"
        >
          Explorer
        </Link>{" "}
        and the other pages do not depend on this one.
      </PageHeader>
      <Button onClick={() => retry()}>Try again</Button>
      {digest ? (
        <p className="mt-4 text-xs text-muted-foreground">
          Error reference: <span className="font-mono">{digest}</span>
        </p>
      ) : null}
    </div>
  );
}

/** Next.js attaches a digest to errors from Server Components; anything else may be thrown. */
export function digestOf(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("digest" in error)) return null;
  return typeof error.digest === "string" ? error.digest : null;
}
