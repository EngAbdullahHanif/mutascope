import type { ReactNode } from "react";

/** Title block shared by every page. */
export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-8 max-w-prose">
      <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
      {children ? <div className="mt-2 text-lg text-muted-foreground">{children}</div> : null}
    </div>
  );
}

/** Marks a page whose content lands in a later task. */
export function Placeholder({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed bg-muted/50 p-6 text-sm text-muted-foreground">
      {children}
    </div>
  );
}
