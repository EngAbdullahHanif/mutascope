import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The Explorer moved to /explore with the per-protein pages (ADR 0038); old links still work.
  async redirects() {
    return [{ source: "/explorer", destination: "/explore", permanent: true }];
  },
  // Browsers keep /data files for a day without asking. That is safe because the loader's URLs
  // name their content (src/lib/bundle/load.ts): `?v=<sha256>` for protein and structure files,
  // `?v=<bundle revision>` for the manifest and benchmarks, so a new bundle never reads a copy
  // cached from an old one. Unversioned URLs (as before) could mix bundles for up to a day.
  async headers() {
    return [
      {
        source: "/data/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=86400, must-revalidate" }],
      },
    ];
  },
};

export default config;
