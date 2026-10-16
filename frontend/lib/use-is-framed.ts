"use client";

import { useSyncExternalStore } from "react";

/**
 * True when the page runs inside an iframe. A Hugging Face Space shows the app in an iframe on huggingface.co,
 * where some browsers block the sign-in cookie; the sign-in page then offers "open in a new tab".
 * Server/static render: false (so the pre-rendered HTML and the first client render match).
 */
function readIsFramed(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    // Accessing window.top can throw in some sandboxed frames: that also means we are framed.
    return true;
  }
}

function subscribe(): () => void {
  // Being framed never changes during the page's life, so there is nothing to subscribe to.
  return () => {};
}

export function useIsFramed(): boolean {
  return useSyncExternalStore(subscribe, readIsFramed, () => false);
}
