import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** A raised paper panel with a hairline border. Compose with CardHeader / CardTitle / CardContent / CardFooter. */
function Card({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card"
      className={cn("rounded-xl border border-border bg-card text-card-foreground shadow-[0_1px_0_0_var(--rule)]", className)}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card-header" className={cn("flex flex-col gap-1.5 p-5 sm:p-6", className)} {...props} />;
}

type CardTitleLevel = "h1" | "h2" | "h3" | "h4";

/**
 * The card's heading. `as` picks the level so the page outline stays in order: h1 when the card is the page's main
 * content (the sign-in page), h2 for a card directly under the page's h1, h3 (default) inside a titled section.
 */
function CardTitle({ as: Heading = "h3", className, ...props }: ComponentProps<"h3"> & { as?: CardTitleLevel }) {
  return <Heading data-slot="card-title" className={cn("text-lg font-semibold leading-snug", className)} {...props} />;
}

function CardDescription({ className, ...props }: ComponentProps<"p">) {
  return <p data-slot="card-description" className={cn("text-sm text-muted-foreground", className)} {...props} />;
}

function CardContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card-content" className={cn("px-5 pb-5 sm:px-6 sm:pb-6", className)} {...props} />;
}

function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="card-footer" className={cn("flex items-center gap-3 px-5 pb-5 sm:px-6 sm:pb-6", className)} {...props} />
  );
}

export { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle };
