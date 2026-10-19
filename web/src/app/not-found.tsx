import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";

// Served with status 404 and `noindex` by Next.js; the title says what happened.
export const metadata: Metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <PageHeader title="Page not found">
      <Link className="text-primary underline underline-offset-4 hover:decoration-2" href="/">
        Back to the start page
      </Link>
    </PageHeader>
  );
}
