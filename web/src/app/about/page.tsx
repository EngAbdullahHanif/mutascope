import type { Metadata } from "next";

import { BundleStatus } from "@/components/bundle-status";
import { PageHeader } from "@/components/layout/page-header";
import { pageMetadata } from "@/lib/seo";
import { ABOUT } from "@/lib/site";

const DESCRIPTION = "Who built MutaScope, and how.";

export const metadata: Metadata = pageMetadata({
  title: "About",
  description: DESCRIPTION,
  path: "/about",
});

const link = "text-primary underline underline-offset-4 hover:decoration-2";

export default function AboutPage() {
  return (
    <>
      <PageHeader title="About" />
      <div className="grid gap-6 md:grid-cols-2">
        <div className="flex max-w-prose flex-col gap-4">
          <p>
            <a className={link} href={ABOUT.home}>
              {ABOUT.home}
            </a>
          </p>
          <ul className="flex flex-wrap gap-4" data-testid="about-links">
            {ABOUT.links.map(({ label, href }) => (
              <li key={href}>
                <a className={link} href={href}>
                  {label}
                </a>
              </li>
            ))}
          </ul>
          <p data-testid="ai-statement">{ABOUT.aiStatement}</p>
        </div>
        <BundleStatus />
      </div>
    </>
  );
}
