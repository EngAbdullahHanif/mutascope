import type { MetadataRoute } from "next";

import { SITE } from "@/lib/site";

/** Everything may be crawled except the raw bundle files, which the pages already present. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/data/" },
    sitemap: new URL("/sitemap.xml", SITE.url).toString(),
    host: SITE.url,
  };
}
