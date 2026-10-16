"use client";

import { RefreshCw, WifiOff } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * The friendly "server not reachable" state. Shown instead of an error when the API cannot be reached:
 * a static preview of the site, a Hugging Face Space that is still waking up, or a network problem.
 */
export function ApiOfflineNotice({ className }: { className?: string }) {
  const { refresh, productName } = useSession();
  const [checking, setChecking] = useState(false);

  async function tryAgain() {
    setChecking(true);
    try {
      await refresh();
    } finally {
      setChecking(false);
    }
  }

  return (
    <Alert variant="warning" className={cn("gap-y-2", className)}>
      <WifiOff aria-hidden="true" />
      <AlertTitle>The {productName} server is not reachable right now</AlertTitle>
      <AlertDescription className="text-foreground/85">
        <p>
          You may be viewing a static preview, or the server may be waking up (a sleeping Hugging Face Space needs a
          moment). Pages still explain each area; sign-in and saved work need the server.
        </p>
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
