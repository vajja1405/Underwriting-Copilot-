import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** The heading block at the top of each page: small eyebrow, serif title, one-paragraph description. */
export function PageIntro({
  eyebrow,
  title,
  description,
  children,
  className,
}: {
  eyebrow: string;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-col gap-3", className)}>
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-accent">{eyebrow}</p>
      <h1 className="font-serif text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl">{title}</h1>
      {description ? <div className="max-w-2xl text-base leading-relaxed text-muted-foreground">{description}</div> : null}
      {children}
    </header>
  );
}
