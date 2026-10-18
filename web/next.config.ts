import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The bundle under public/data is fixed by its pinned commit (bundle.json), so its files can be
  // cached hard. A new bundle is a new deploy, and the browser revalidates after a day.
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
