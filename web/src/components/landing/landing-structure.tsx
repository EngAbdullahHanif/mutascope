"use client";

import dynamic from "next/dynamic";

// The viewer, 3Dmol.js and the structure are fetched only after hydration (and then only when
// the browser is idle), so the Landing page's first paint carries none of them.
const RotatingStructure = dynamic(() => import("./rotating-structure"), {
  ssr: false,
  loading: () => <div className="h-64 w-full sm:h-80" aria-hidden />,
});

export function LandingStructure({ assayId, name }: { assayId: string; name: string }) {
  return <RotatingStructure assayId={assayId} name={name} />;
}
