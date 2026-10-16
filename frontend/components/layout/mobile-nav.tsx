"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { BrandMark } from "@/components/layout/brand-mark";
import { isActivePath, NAV_LINKS } from "@/components/layout/nav-links";
import { SessionControls } from "@/components/session/session-controls";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useSession } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** The menu button and slide-in panel used below the `md` breakpoint (phones, 375 px and up). */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const { productName } = useSession();
  const close = () => setOpen(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button type="button" variant="ghost" size="icon" className="md:hidden" aria-label="Open menu">
          <Menu className="size-5" aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="gap-5">
        <div className="flex items-center gap-2">
          <BrandMark />
          <SheetTitle className="font-serif text-xl">{productName}</SheetTitle>
        </div>
        <SheetDescription className="sr-only">Site navigation and sign-in</SheetDescription>
        <nav aria-label="Main">
          <ul className="flex flex-col gap-1">
            {NAV_LINKS.map((link) => {
              const active = isActivePath(pathname, link.href);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    onClick={close}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex flex-col gap-0.5 rounded-md border-l-2 px-3 py-2.5 hover:bg-muted",
                      active ? "border-accent bg-muted" : "border-transparent",
                    )}
                  >
                    <span className="text-[0.9375rem] font-semibold text-foreground">{link.label}</span>
                    <span className="text-xs text-muted-foreground">{link.description}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <Separator />
        <SessionControls layout="stacked" onNavigate={close} />
      </SheetContent>
    </Sheet>
  );
}
