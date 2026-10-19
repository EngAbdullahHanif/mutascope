import { manifestFromDisk } from "@/lib/bundle/static-params";
import { OG_CONTENT_TYPE, OG_SIZE, ogImage } from "@/lib/og";

export const alt = "MutaScope: Mutation landscapes for ProteinGym assays";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function Image() {
  return ogImage(
    "Mutation landscapes for ProteinGym assays",
    `Predicted effect of every single amino-acid substitution in ${manifestFromDisk().proteins.length} ProteinGym DMS assays, with an honest benchmark.`,
  );
}
