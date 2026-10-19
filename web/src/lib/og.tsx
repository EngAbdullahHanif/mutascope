/**
 * Open Graph images (1200 × 630), one per page, rendered at build time by `next/og`.
 *
 * The image renderer reads no CSS, so the colours are written out here: the light theme's
 * background, foreground and muted ink (globals.css, converted from OKLCH) and the effect
 * scale's stops for the logo, the same glyph as the header's.
 */
import { ImageResponse } from "next/og";

import { EFFECT_STOPS } from "@/lib/color/diverging";

import { SITE } from "./site";

export const OG_SIZE = { width: 1200, height: 630 };
export const OG_CONTENT_TYPE = "image/png";

const INK = {
  background: "#fcfcfd", // --background, oklch(0.99 0.002 250)
  foreground: "#1d2129", // --foreground, oklch(0.22 0.012 260)
  muted: "#5d6370", // --muted-foreground, oklch(0.47 0.014 260)
  border: "#e1e4e9", // --border, oklch(0.91 0.006 250)
};

/** Logo cells: stops 1, 3, 5, 7, 9 of the effect scale (blue, light blue, white, light red, red). */
const LOGO = [EFFECT_STOPS[1], EFFECT_STOPS[3], EFFECT_STOPS[5], EFFECT_STOPS[7], EFFECT_STOPS[9]];

export function ogImage(title: string, subtitle: string): ImageResponse {
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 72,
        background: INK.background,
        color: INK.foreground,
        fontFamily: "sans-serif",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
        <div
          style={{
            display: "flex",
            border: `2px solid ${INK.border}`,
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          {LOGO.map((c) => (
            <div key={c} style={{ width: 18, height: 52, background: c }} />
          ))}
        </div>
        <div style={{ fontSize: 40, fontWeight: 600 }}>{SITE.name}</div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ fontSize: 68, fontWeight: 600, lineHeight: 1.1, letterSpacing: -1 }}>
          {title}
        </div>
        <div style={{ fontSize: 32, color: INK.muted, lineHeight: 1.35, maxWidth: 980 }}>
          {subtitle}
        </div>
      </div>
      <div style={{ display: "flex", fontSize: 24, color: INK.muted }}>
        {`${SITE.url.replace("https://", "")} · Not for clinical use`}
      </div>
    </div>,
    OG_SIZE,
  );
}
