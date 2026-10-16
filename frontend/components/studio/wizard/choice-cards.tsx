"use client";

import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A group of large radio cards (city, business type). Real <input type="radio"> elements inside a <fieldset>, so
 * the arrow keys move between options and screen readers announce "1 of 6" with the legend.
 */
export interface ChoiceOption {
  value: string;
  title: string;
  description?: ReactNode;
  badge?: ReactNode;
}

export function ChoiceCards({
  name,
  legend,
  options,
  value,
  onChange,
  error,
  columns = 2,
}: {
  name: string;
  legend: string;
  options: ChoiceOption[];
  value: string;
  onChange: (value: string) => void;
  error?: string;
  columns?: 2 | 3;
}) {
  const errorId = `${name}-error`;
  return (
    <fieldset className="flex flex-col gap-3" aria-describedby={error ? errorId : undefined}>
      <legend className="sr-only">{legend}</legend>
      <div className={cn("grid gap-3", columns === 3 ? "sm:grid-cols-2 lg:grid-cols-3" : "sm:grid-cols-2")}>
        {options.map((option) => {
          const checked = option.value === value;
          const id = `${name}-${option.value}`;
          return (
            <label
              key={option.value}
              htmlFor={id}
              className={cn(
                "relative flex cursor-pointer flex-col gap-2 rounded-xl border bg-card p-4 transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring",
                checked ? "border-accent bg-accent-soft/60 shadow-[0_0_0_1px_var(--accent)]" : "border-border hover:border-rule-strong",
              )}
            >
              <input
                id={id}
                type="radio"
                name={name}
                value={option.value}
                checked={checked}
                onChange={() => onChange(option.value)}
                className="sr-only"
              />
              <span className="flex items-start justify-between gap-3">
                <span className="font-semibold leading-snug text-foreground">{option.title}</span>
                <span
                  className={cn(
                    "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
                    checked ? "border-accent bg-accent text-accent-ink" : "border-rule-strong bg-card",
                  )}
                  aria-hidden="true"
                >
                  {checked ? <Check className="size-3.5" strokeWidth={3} /> : null}
                </span>
              </span>
              {option.badge ? <span className="flex flex-wrap gap-1.5">{option.badge}</span> : null}
              {option.description ? <span className="text-sm leading-relaxed text-muted-foreground">{option.description}</span> : null}
            </label>
          );
        })}
      </div>
      {error ? (
        <p id={errorId} className="text-sm font-medium text-conflicting">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
