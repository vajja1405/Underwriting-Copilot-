"use client";

import type { ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/**
 * One labeled number input of the money step: a "$" or "%" adornment, a hint, an evidence chip that says what the
 * value will count as (USER_STATED when typed, ASSUMPTION when left empty), and an inline error.
 */
export function AmountField({
  id,
  label,
  hint,
  value,
  onChange,
  error,
  prefix,
  suffix,
  chip,
  inputMode = "decimal",
  placeholder,
  optional = false,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  prefix?: string;
  suffix?: string;
  chip?: ReactNode;
  inputMode?: "decimal" | "numeric";
  placeholder?: string;
  optional?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor={id}>
          {label}
          {optional ? <span className="font-normal text-muted-foreground"> (optional)</span> : null}
        </Label>
        {chip}
      </div>
      <div className="relative">
        {prefix ? (
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-muted-foreground" aria-hidden="true">
            {prefix}
          </span>
        ) : null}
        <Input
          id={id}
          inputMode={inputMode}
          autoComplete="off"
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn("figure-nums", prefix && "pl-7", suffix && "pr-16")}
        />
        {suffix ? (
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted-foreground" aria-hidden="true">
            {suffix}
          </span>
        ) : null}
      </div>
      {hint ? (
        <p id={hintId} className="text-xs leading-relaxed text-muted-foreground">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm font-medium text-conflicting">
          {error}
        </p>
      ) : null}
    </div>
  );
}
