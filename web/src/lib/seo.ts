import type { Metadata } from "next";

import { SITE } from "./site";

/**
 * One page's metadata: title, description, canonical URL and Open Graph fields. Next merges a
 * page's `openGraph` over the layout's shallowly, so every page passes the full set here. The
 * image comes from the route's `opengraph-image.tsx`. URLs are relative to `metadataBase`
 * (`SITE.url`, set in the root layout).
 */
export function pageMetadata({
  title,
  description,
  path,
  absoluteTitle = false,
}: {
  title: string;
  description: string;
  path: string;
  absoluteTitle?: boolean;
}): Metadata {
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE.name,
      locale: "en_GB",
      title,
      description,
      url: path,
    },
    twitter: { card: "summary_large_image", title, description },
  };
}
