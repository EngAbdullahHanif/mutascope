"use client";

import { CircleAlert, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const STALE_DATA_MESSAGE = "This data is out of date. Reload the page.";

/**
 * Shown for a `StaleDataError`: a file's digest differed even past the browser cache, so the
 * page and its data come from different bundles. Reloading fetches the page that matches.
 */
export function StaleDataAlert({ className }: { className?: string }) {
  return (
    <div
      className={cn("flex flex-wrap items-center gap-3 text-sm", className)}
      role="alert"
      data-testid="stale-data"
    >
      <p className="flex items-center gap-2 text-destructive">
        <CircleAlert aria-hidden className="size-4 shrink-0" />
        {STALE_DATA_MESSAGE}
      </p>
      <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
        <RefreshCw aria-hidden />
        Reload
      </Button>
    </div>
  );
}
