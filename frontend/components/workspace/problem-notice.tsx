"use client";

import { AlertTriangle, Clock, LogIn, RefreshCw, WifiOff } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { waitText, type Problem } from "@/lib/api-errors";
import { useCurrentPath } from "@/lib/location";
import { signInPageHref } from "@/lib/sign-in";
import { cn } from "@/lib/utils";

/**
 * A failed API call, in plain words, with the next step: "Try again" for busy / timeout / offline / server errors,
 * a sign-in link (that comes back to this page) for a signed-out session. No "Try again" for a refused input (422)
 * or a conflict (409, for example limited mode): the same request would get the same answer, so the page must say
 * what to change instead. Used by every feature section so failures look alike.
 */
const RETRYABLE: ReadonlySet<Problem["kind"]> = new Set(["offline", "busy", "timeout", "server"]);

export function ProblemNotice({
  problem,
  onRetry,
  retrying = false,
  className,
}: {
  problem: Problem;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}) {
  const currentPath = useCurrentPath();
  const Icon = problem.kind === "offline" ? WifiOff : problem.kind === "timeout" || problem.kind === "busy" ? Clock : AlertTriangle;
  const wait = waitText(problem.retryAfterS);
  const canRetry = onRetry !== undefined && RETRYABLE.has(problem.kind);

  return (
    <Alert variant={problem.kind === "invalid" || problem.kind === "server" ? "destructive" : "warning"} role="alert" className={cn("gap-y-2", className)}>
      <Icon aria-hidden="true" />
      <AlertTitle>{problem.title}</AlertTitle>
      <AlertDescription className="text-foreground/85">
        {problem.messages.length === 1 ? (
          <p>
            {problem.messages[0]} {wait}
          </p>
        ) : (
          <>
            <ul className="list-disc space-y-1 pl-5">
              {problem.messages.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
            {wait ? <p>{wait}</p> : null}
          </>
        )}
        {problem.kind === "signed_out" ? (
          <div className="pt-1">
            <Button asChild size="sm">
              <Link href={signInPageHref(currentPath)}>
                <LogIn aria-hidden="true" />
                Sign in
              </Link>
            </Button>
          </div>
        ) : null}
        {canRetry ? (
          <div className="pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
              <RefreshCw className={cn(retrying && "animate-spin")} aria-hidden="true" />
              {retrying ? "Trying..." : "Try again"}
            </Button>
          </div>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
