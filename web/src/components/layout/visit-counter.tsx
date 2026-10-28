"use client";

import { usePathname } from "next/navigation";
import Script from "next/script";
import { useEffect, useRef } from "react";

import { GOATCOUNTER } from "@/lib/site";

type GoatCounter = { count?: (vars: { path: string }) => void };
declare global {
  interface Window {
    goatcounter?: GoatCounter;
  }
}

/**
 * Counts one visit per page shown (ADR 0044). GoatCounter's script counts only full page loads,
 * and most moves here are client-side navigations, so its own count is off (`no_onload`) and
 * this component counts instead: once when the script has loaded, then on every new path. Only
 * the path is sent, never the query string, so nothing a reader typed can reach GoatCounter.
 */
export function VisitCounter() {
  const pathname = usePathname();
  const latest = useRef(pathname);
  const counted = useRef<string | null>(null);

  const count = (path: string) => {
    const counter = window.goatcounter?.count;
    if (!counter || counted.current === path) return;
    counted.current = path;
    counter({ path });
  };

  useEffect(() => {
    latest.current = pathname;
    count(pathname);
  }, [pathname]);

  return (
    <Script
      src={GOATCOUNTER.script}
      integrity={GOATCOUNTER.integrity}
      crossOrigin="anonymous"
      strategy="afterInteractive"
      data-goatcounter={GOATCOUNTER.endpoint}
      data-goatcounter-settings='{"no_onload": true}'
      onLoad={() => count(latest.current)}
    />
  );
}
