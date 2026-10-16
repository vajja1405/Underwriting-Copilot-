"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** "Try again in 1 minute" style wording for a Retry-After value in seconds. */
function waitText(seconds: number): string {
  if (seconds <= 1) {
    return "Try again in a moment.";
  }
  if (seconds < 90) {
    return `Try again in ${seconds} seconds.`;
  }
  return `Try again in about ${Math.round(seconds / 60)} minutes.`;
}

/**
 * Shown when the server answered with an error (session status "error"): a rate limit (429, with Retry-After) or a
 * server error. Unlike ApiOfflineNotice, the server IS reachable; the last known sign-in is kept.
 */
export function SessionErrorNotice({ className }: { className?: string }) {
  const { error, me, refresh } = useSession();
  const [checking, setChecking] = useState(false);
  const rateLimited = error?.status === 429;

  async function tryAgain() {
    setChecking(true);
    try {
      await refresh();
    } finally {
      setChecking(false);
    }
  }

  return (
    <Alert variant="warning" role="alert" className={cn("gap-y-2", className)}>
      <AlertTriangle aria-hidden="true" />
      <AlertTitle>
        {rateLimited ? "Too many requests right now" : `The server answered with an error (HTTP ${error?.status ?? "?"})`}
      </AlertTitle>
      <AlertDescription className="text-foreground/85">
        <p>
          {rateLimited
            ? "The server limits how many requests one connection or account can send per minute."
            : error?.message || "Something went wrong on the server."}{" "}
          {rateLimited && error?.retryAfterS != null ? waitText(error.retryAfterS) : null}
        </p>
        {me.signed_in ? <p>You are still shown as signed in as {me.display_name || me.username}.</p> : null}
        <div className="pt-1">
          <Button type="button" variant="outline" size="sm" onClick={tryAgain} disabled={checking}>
            <RefreshCw className={cn(checking && "animate-spin")} aria-hidden="true" />
            {checking ? "Checking..." : "Try again"}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
