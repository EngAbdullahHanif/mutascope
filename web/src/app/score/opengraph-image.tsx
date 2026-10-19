import { OG_CONTENT_TYPE, OG_SIZE, ogImage } from "@/lib/og";

export const alt = "MutaScope: Score a sequence";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function Image() {
  return ogImage(
    "Score a sequence",
    "Zero-shot ESM-2 scores for every substitution of your own sequence, computed live.",
  );
}
