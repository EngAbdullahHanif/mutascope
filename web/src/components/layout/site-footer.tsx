import { SITE } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t text-sm text-muted-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 md:flex-row md:items-start md:justify-between">
        <div className="max-w-prose">
          <p data-testid="clinical-note">
            <strong className="font-medium text-foreground">Not for clinical use.</strong> MutaScope
            predicts functional effects measured in deep mutational scanning (DMS) assays. It does
            not predict pathogenicity and must not inform diagnosis or treatment.
          </p>
        </div>
        <ul className="flex shrink-0 gap-4">
          <li>
            <a
              className="underline-offset-4 hover:text-foreground hover:underline"
              href={SITE.github}
            >
              GitHub
            </a>
          </li>
          <li>
            <a
              className="underline-offset-4 hover:text-foreground hover:underline"
              href={SITE.huggingFace}
            >
              Hugging Face
            </a>
          </li>
        </ul>
      </div>
      <div className="mx-auto max-w-6xl px-4">
        <p className="border-t py-4 text-center" data-testid="copyright">
          © 2026 Abdullah Hanif
        </p>
      </div>
    </footer>
  );
}
