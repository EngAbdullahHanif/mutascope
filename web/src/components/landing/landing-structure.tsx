"use client";

import dynamic from "next/dynamic";
import { useRef } from "react";

import { useInView } from "@/lib/use-in-view";

// The viewer, 3Dmol.js and the structure are fetched only once the figure is on screen (and
// then when the browser is idle), so the Landing page's first paint carries none of them, and a
// reader who never scrolls to it never downloads them (ADR 0041 addendum).
// Each stage (placeholder, module loading, viewer) carries the same test id and its own
// `data-state` (waiting, loading, then the viewer's), so there is always exactly one.
const RotatingStructure = dynamic(() => import("./rotating-structure"), {
  ssr: false,
  loading: () => (
    <div
      className="h-64 w-full sm:h-80"
      data-testid="landing-structure"
      data-state="loading"
      data-spinning="false"
      aria-hidden
    />
  ),
});

export function LandingStructure({ assayId, name }: { assayId: string; name: string }) {
  const placeholder = useRef<HTMLDivElement>(null);
  const visible = useInView(placeholder);
  if (visible) return <RotatingStructure assayId={assayId} name={name} />;
  return (
    <div
      ref={placeholder}
      className="h-64 w-full sm:h-80"
      data-testid="landing-structure"
      data-state="waiting"
      data-spinning="false"
      aria-hidden
    />
  );
}
