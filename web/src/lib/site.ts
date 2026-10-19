import pin from "../../bundle.json";

/** Site-wide names and links; one place, so header, footer and pages agree. */
export const SITE = {
  name: "MutaScope",
  /** The production origin: canonical URLs, Open Graph, sitemap and robots.txt. */
  url: "https://mutascope.abdullahhanif.me",
  tagline: "Functional effects of every single amino-acid substitution, from DMS assays.",
  github: "https://github.com/EngAbdullahHanif/mutascope",
  huggingFace: `https://huggingface.co/datasets/${pin.repo}`,
  modelRepo: "https://huggingface.co/engAbdullahHanif/mutascope-ridge-seq-struct",
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

/**
 * The Landing page's example proteins: well-known assays with a structure and the supervised
 * model. Ids only; the build fails if one is not in the manifest. The first is also the
 * rotating structure (a small file: GFP, 238 residues).
 */
export const EXAMPLES = [
  "GFP_AEQVI_Sarkisyan_2016",
  "BLAT_ECOLX_Stiffler_2015",
  "P53_HUMAN_Kotler_2018",
] as const;

/** A file in the repository on GitHub (main). */
export function repoFile(path: string): string {
  return `${SITE.github}/blob/main/${path}`;
}

/** The ADRs the content pages cite, by number. */
const ADRS: Record<number, string> = {
  3: "0003-structure-exceptions",
  10: "0010-split-reproduction-gate",
  11: "0011-benchmark-harness",
  12: "0012-esm2-scorer",
  14: "0014-model-sizes-wt-marginal-tracking",
  15: "0015-saprot-scorer",
  16: "0016-ensemble-and-figures",
  19: "0019-supervised-cv-harness",
  21: "0021-ridge-models",
  24: "0024-conformal-intervals",
  25: "0025-final-models",
  26: "0026-phase-3-close",
  27: "0027-phase-3-in-nextflow",
  28: "0028-website-bundle",
  29: "0029-onnx-esm2",
  31: "0031-live-scoring",
  34: "0034-ci-cd-and-deployment",
  41: "0041-content-pages",
};

export function adrUrl(n: number): string {
  const slug = ADRS[n];
  if (slug === undefined) throw new Error(`no ADR ${n} in the list`);
  return repoFile(`docs/adr/${slug}.md`);
}

/** The About page, exactly as the author supplied it (Phase 5 Task 5). */
export const ABOUT = {
  home: "http://abdullahhanif.me/",
  links: [
    { label: "GitHub", href: "https://github.com/EngAbdullahHanif/" },
    { label: "LinkedIn", href: "https://www.linkedin.com/in/engabdullahhanif/" },
  ],
  aiStatement:
    "MutaScope is my own project: the idea, the scientific design, the evaluation rules and every decision recorded in the ADRs are mine. I used Claude as an AI assistant for writing code and for reviewing my plans, and I review and test everything before it is merged.",
} as const;
