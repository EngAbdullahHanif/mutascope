"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme/theme-toggle";
import { NAV, SITE } from "@/lib/site";
import { cn } from "@/lib/utils";

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 px-4 py-2 sm:h-14 sm:flex-nowrap sm:py-0">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <Logo />
          {SITE.name}
        </Link>
        <nav
          aria-label="Main"
          className="order-last -mx-2.5 flex w-full items-center gap-1 pt-1 sm:order-none sm:mx-0 sm:w-auto sm:flex-1 sm:pt-0"
        >
          {NAV.map(({ href, label }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors",
                  active
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

/** Five cells of the effect scale: the product in one glyph. */
function Logo() {
  const cells = ["neg-4", "neg-2", "0", "pos-2", "pos-4"] as const;
  return (
    <span aria-hidden className="flex overflow-hidden rounded-sm border">
      {cells.map((c) => (
        <span
          key={c}
          className="block h-4 w-1.5"
          style={{ background: `var(--color-effect-${c})` }}
        />
      ))}
    </span>
  );
}
