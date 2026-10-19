import { OG_CONTENT_TYPE, OG_SIZE, ogImage } from "@/lib/og";

export const alt = "MutaScope: Benchmarks";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function Image() {
  return ogImage(
    "Benchmarks",
    "Zero-shot and supervised models on ProteinGym: paired differences with 95% intervals.",
  );
}
