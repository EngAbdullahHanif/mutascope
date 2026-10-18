import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";

export default function NotFound() {
  return (
    <PageHeader title="Page not found">
      <Link className="text-primary underline-offset-4 hover:underline" href="/">
        Back to the start page
      </Link>
    </PageHeader>
  );
}
