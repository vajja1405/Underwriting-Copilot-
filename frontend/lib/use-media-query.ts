"use client";

import { useSyncExternalStore } from "react";

/**
 * True while a CSS media query matches (for example "(min-width: 640px)"). Used where a chart needs a different
 * layout on phones (narrower axis labels); CSS handles everything else. Server/static render: false.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
