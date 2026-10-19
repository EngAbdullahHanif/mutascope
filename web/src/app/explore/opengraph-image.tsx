import { OG_CONTENT_TYPE, OG_SIZE, ogImage } from "@/lib/og";

export const alt = "MutaScope: Explorer";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function Image() {
  return ogImage(
    "Explorer",
    "Every single substitution of a protein as a heatmap, linked to its AF2 structure.",
  );
}
