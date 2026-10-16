import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Text input. Always pair it with a <Label htmlFor=...>. */
function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-10 w-full min-w-0 rounded-md border border-input bg-card px-3 py-2 text-base text-foreground shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-55 aria-invalid:border-destructive sm:text-sm",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
