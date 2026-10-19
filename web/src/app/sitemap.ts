import type { MetadataRoute } from "next";

import { manifestFromDisk } from "@/lib/bundle/static-params";
import { NAV, SITE } from "@/lib/site";

/** Every page, and every protein in the pinned bundle (the same list the build prerenders). */
export default function sitemap(): MetadataRoute.Sitemap {
  const pages = ["/", ...NAV.map((n) => n.href)].map((path) => ({
    url: new URL(path, SITE.url).toString(),
    changeFrequency: "monthly" as const,
    priority: path === "/" ? 1 : 0.8,
  }));
  const proteins = manifestFromDisk().proteins.map((p) => ({
    url: new URL(`/explore/${p.assay_id}`, SITE.url).toString(),
    changeFrequency: "monthly" as const,
    priority: 0.5,
  }));
  return [...pages, ...proteins];
}
