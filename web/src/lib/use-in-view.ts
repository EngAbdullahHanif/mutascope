"use client";

import { useEffect, useState, type RefObject } from "react";

/**
 * True once the element has come within `rootMargin` of the viewport, and true from then on.
 *
 * The 3D views mount only when this turns true (ADR 0039 and 0041 addenda), so 3Dmol.js is
 * downloaded and its WebGL work runs only for readers who reach them. Latching matters: a viewer
 * scrolled away keeps its state, and the linked hover keeps working. A browser without
 * IntersectionObserver counts as in view.
 */
export function useInView(ref: RefObject<Element | null>, rootMargin = "0px"): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === "undefined") {
      queueMicrotask(() => setSeen(true));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, rootMargin, seen]);
  return seen;
}
