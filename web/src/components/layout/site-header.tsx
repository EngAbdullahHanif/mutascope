"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { ThemeToggle } from "@/components/theme/theme-toggle";
import { Button } from "@/components/ui/button";
import { NAV, SITE } from "@/lib/site";
import { cn } from "@/lib/utils";

/**
 * Logo, navigation and theme toggle. Below `sm` the five links do not fit beside the logo (nor on
 * a 360 px line of their own), so they fold behind a disclosure button.
 */
export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl flex-wrap items-center gap-x-6 px-4 max-sm:h-auto max-sm:min-h-14 sm:flex-nowrap">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <Logo />
          {SITE.name}
        </Link>
        <nav
          id="main-nav"
          aria-label="Main"
          className={cn(
            "order-last -mx-2.5 w-full flex-col gap-1 pb-2 sm:order-none sm:mx-0 sm:flex sm:w-auto sm:flex-1 sm:flex-row sm:items-center sm:pb-0",
            open ? "flex" : "hidden",
          )}
        >
          {NAV.map(({ href, label }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                onClick={() => setOpen(false)}
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
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <Button
            variant="ghost"
            size="icon-sm"
            className="sm:hidden"
            aria-label="Menu"
            aria-expanded={open}
            aria-controls="main-nav"
            onClick={() => setOpen(!open)}
          >
            {open ? <X aria-hidden /> : <Menu aria-hidden />}
          </Button>
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
