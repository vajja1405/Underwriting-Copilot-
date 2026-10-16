import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { SkipLink } from "@/components/layout/skip-link";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SessionProvider } from "@/lib/auth";
import { DISCLAIMER, PRODUCT_NAME, TAGLINE } from "@/lib/config";

export const metadata: Metadata = {
  title: { default: `${PRODUCT_NAME}: ${TAGLINE}`, template: `%s · ${PRODUCT_NAME}` },
  description: `${TAGLINE} ${DISCLAIMER}`,
  applicationName: PRODUCT_NAME,
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f5ef" },
    { media: "(prefers-color-scheme: dark)", color: "#12151b" },
  ],
};

/** Root layout: skip link, header, <main id="main">, footer with the disclaimer. Wraps every page. */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-dvh flex-col">
        <SessionProvider>
          <TooltipProvider delayDuration={200}>
            <SkipLink />
            <SiteHeader />
            <main id="main" tabIndex={-1} className="flex-1 outline-none">
              {children}
            </main>
            <SiteFooter />
          </TooltipProvider>
        </SessionProvider>
      </body>
    </html>
  );
}
