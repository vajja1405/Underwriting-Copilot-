"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { BrandMark } from "@/components/layout/brand-mark";
import { MobileNav } from "@/components/layout/mobile-nav";
import { isActivePath, NAV_LINKS } from "@/components/layout/nav-links";
import { SessionControls } from "@/components/session/session-controls";
import { useSession } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** Sticky header: product name, the two areas (Founder Studio, Lender Desk), About, and sign-in state. */
export function SiteHeader() {
  const pathname = usePathname();
  const { productName } = useSession();

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-paper/90 backdrop-blur supports-[backdrop-filter]:bg-paper/80">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center gap-3 px-4 sm:px-6">
        <MobileNav />
        <Link href="/" className="flex items-center gap-2 rounded-md" aria-label={`${productName} home`}>
          <BrandMark />
          <span className="font-serif text-xl font-semibold tracking-tight text-foreground">{productName}</span>
        </Link>
        <span className="hidden rounded-full border border-border px-2 py-0.5 text-[0.6875rem] font-medium uppercase tracking-[0.08em] text-muted-foreground sm:inline">
          Prototype
        </span>

        <nav aria-label="Main" className="ml-6 hidden md:block">
          <ul className="flex items-center gap-1">
            {NAV_LINKS.map((link) => {
              const active = isActivePath(pathname, link.href);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative inline-flex h-16 items-center px-3 text-sm font-medium transition-colors hover:text-foreground",
                      active
                        ? "text-foreground after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-accent"
                        : "text-muted-foreground",
                    )}
                  >
                    {link.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="ml-auto hidden md:block">
          <SessionControls />
        </div>
        <div className="ml-auto md:hidden">
          <SessionControls layout="compact" />
        </div>
      </div>
    </header>
  );
}
