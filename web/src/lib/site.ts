import pin from "../../bundle.json";

/** Site-wide names and links; one place, so header, footer and pages agree. */
export const SITE = {
  name: "MutaScope",
  tagline: "Functional effects of every single amino-acid substitution, from DMS assays.",
  github: "https://github.com/EngAbdullahHanif/mutascope",
  huggingFace: `https://huggingface.co/datasets/${pin.repo}`,
  bundle: { tag: pin.tag, revision: pin.revision },
} as const;

export const NAV = [
  { href: "/explorer", label: "Explorer" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/about", label: "About" },
] as const;
