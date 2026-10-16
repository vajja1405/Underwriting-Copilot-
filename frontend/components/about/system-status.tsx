"use client";

import { useEffect, useState } from "react";

import { api, isAbortError, type HealthResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

type HealthState = { kind: "checking" } | { kind: "ok"; health: HealthResponse } | { kind: "unreachable" };

/** A one-line server status from GET /api/health (version and git commit when the server knows them). */
export function SystemStatus({ className }: { className?: string }) {
  const [state, setState] = useState<HealthState>({ kind: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    api
      .health(controller.signal)
      .then((health) => setState({ kind: "ok", health }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setState({ kind: "unreachable" });
        }
      });
    return () => controller.abort();
  }, []);

  let dotClass = "bg-unknown";
  let text = "Checking the server...";
  if (state.kind === "ok") {
    dotClass = state.health.status === "ok" ? "bg-verified" : "bg-stated";
    const sha = state.health.git_sha ? ` · commit ${state.health.git_sha.slice(0, 7)}` : "";
    text = `Server status: ${state.health.status} · version ${state.health.version}${sha}`;
  } else if (state.kind === "unreachable") {
    dotClass = "bg-stated";
    text = "Server not reachable: this is a static preview, or the server is starting.";
  }

  return (
    <p className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)} aria-live="polite">
      <span className={cn("size-2 shrink-0 rounded-full", dotClass)} aria-hidden="true" />
      {text}
    </p>
  );
}
