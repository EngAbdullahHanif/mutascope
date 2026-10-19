import pin from "../../bundle.json";

/** Site-wide names and links; one place, so header, footer and pages agree. */
export const SITE = {
  name: "MutaScope",
  tagline: "Functional effects of every single amino-acid substitution, from DMS assays.",
  github: "https://github.com/EngAbdullahHanif/mutascope",
  huggingFace: `https://huggingface.co/datasets/${pin.repo}`,
  bundle: { tag: pin.tag, revision: pin.revision },
  /**
   * The live-scoring API (Cloud Run, ADR 0034), the only thing `/score` calls. Overridable at
   * build time for a local API; the API's CORS allows only the production origins.
   */
  api: process.env.NEXT_PUBLIC_MUTASCOPE_API_URL ?? "https://mutascope-api-2lvmztrsha-ew.a.run.app",
} as const;

export const NAV = [
  { href: "/explore", label: "Explorer" },
  { href: "/score", label: "Score" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/about", label: "About" },
] as const;
