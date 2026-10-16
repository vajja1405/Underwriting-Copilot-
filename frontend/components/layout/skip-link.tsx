/** First focusable element on every page: lets keyboard users jump past the header to <main id="main">. */
export function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only z-[60] rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground focus:not-sr-only focus:fixed focus:left-4 focus:top-3"
    >
      Skip to content
    </a>
  );
}
