import { SITE } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t text-sm text-muted-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 md:flex-row md:items-start md:justify-between">
        <p className="max-w-prose" data-testid="clinical-note">
          <strong className="font-medium text-foreground">Not for clinical use.</strong> MutaScope
          predicts functional effects measured in deep mutational scanning (DMS) assays. It does not
          predict pathogenicity and must not inform diagnosis or treatment.
        </p>
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
    </footer>
  );
}
