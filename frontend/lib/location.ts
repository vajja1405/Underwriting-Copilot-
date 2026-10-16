"use client";

import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";

/**
 * The current URL's query string, read in the browser.
 *
 * Why not Next's useSearchParams(): in a static export it forces the page to render only in the browser unless it
 * sits inside a <Suspense> boundary. Reading window.location after hydration keeps the pre-rendered HTML.
 * Server/static render: "" (so the pre-rendered HTML and the first client render match).
 */
function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function readSearch(): string {
  return window.location.search;
}

/** The current query string ("?next=%2Fstudio" or ""). */
export function useLocationSearch(): string {
  return useSyncExternalStore(subscribe, readSearch, () => "");
}

/** One query parameter of the current URL, or null when it is absent. */
export function useQueryParam(name: string): string | null {
  return new URLSearchParams(useLocationSearch()).get(name);
}

/** The current path plus query ("/lender/file/?id=3"), for coming back here after sign-in. */
export function useCurrentPath(): string {
  const pathname = usePathname() ?? "/";
  return `${pathname}${useLocationSearch()}`;
}
