import { cn } from "@/lib/utils";

/**
 * The Fundproof mark: an ink-blue stamp with two ledger lines and a check ("figures, proofed").
 * Decorative: the product name is always written next to it.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7 shrink-0", className)} aria-hidden="true" focusable="false">
      <rect x="1.5" y="1.5" width="29" height="29" rx="7" fill="var(--accent)" />
      <path d="M8 10.5h10M8 15.5h6.5" stroke="var(--accent-ink)" strokeWidth="2.4" strokeLinecap="round" />
      <path
        d="M13.5 21.2l3.4 3.3 7.2-8"
        stroke="var(--accent-ink)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
