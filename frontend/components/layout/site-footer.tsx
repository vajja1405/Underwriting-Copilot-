"use client";

import { Info } from "lucide-react";
import Link from "next/link";

import { useSession } from "@/lib/auth";
import { REPO_URL } from "@/lib/config";

/** Footer on EVERY page: the disclaimer (Studio addendum rule C), the prototype + SYNTHETIC notes, and links. */
export function SiteFooter() {
  const { productName, disclaimer } = useSession();

  return (
    <footer className="mt-auto border-t border-border bg-paper-sunken">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-8 sm:px-6">
        <p className="flex items-start gap-2.5 text-sm font-medium leading-relaxed text-foreground">
          <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
          <span>{disclaimer}</span>
        </p>
        <div className="flex flex-col gap-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <p>
            {productName} is a prototype. Demo files are SYNTHETIC. No payments.
          </p>
          <ul className="flex flex-wrap gap-x-5 gap-y-2">
            <li>
              <Link href="/about" className="underline-offset-4 hover:text-foreground hover:underline">
                About and data sources
              </Link>
            </li>
            <li>
              <a href={REPO_URL} className="underline-offset-4 hover:text-foreground hover:underline" rel="noopener noreferrer">
                Source code
              </a>
            </li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
