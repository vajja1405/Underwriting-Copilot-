import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Join class names and let later Tailwind classes win over earlier ones
 * (for example `cn("px-2", "px-4")` gives `"px-4"`). Used by every component.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
