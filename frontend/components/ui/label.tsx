"use client";

import { Label as LabelPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Form label (Radix Label: clicking it focuses the linked control). */
function Label({ className, ...props }: ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn("text-sm font-medium leading-none text-foreground peer-disabled:opacity-60", className)}
      {...props}
    />
  );
}

export { Label };
