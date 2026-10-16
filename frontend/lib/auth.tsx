"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  api,
  ApiError,
  clearCsrfToken,
  isAbortError,
  isOfflineError,
  SIGNED_OUT,
  type ConfigResponse,
  type MeResponse,
  type Role,
} from "@/lib/api";
import { DISCLAIMER, PRODUCT_NAME } from "@/lib/config";

/**
 * Session context: who is signed in (GET /api/me) and the server's public settings (GET /api/config).
 *
 * status:
 *   "loading" - first request still in flight (also what the static HTML is pre-rendered with)
 *   "ready"   - the API answered; `me` and `config` are filled in
 *   "offline" - the API could not be reached (static preview, server starting, network down)
 *   "error"   - the API answered with an error (for example 429 "rate limit exceeded" or a 500). `error` holds it
 *               (with Retry-After when sent), and the last known `me` and `config` are KEPT: an error says nothing
 *               about who is signed in, so it never turns into "signed out" or "offline".
 *
 * Every page reads it with `useSession()`. Nothing here decides access: the server checks roles on every request.
 */

export type SessionStatus = "loading" | "ready" | "offline" | "error";

export interface SessionValue {
  status: SessionStatus;
  me: MeResponse;
  config: ConfigResponse | null;
  /** Product name and disclaimer: the server's values when known, else the built-in fallbacks. */
  productName: string;
  disclaimer: string;
  /** With status "error": what the server answered (for example a 429 with retryAfterS). Otherwise null. */
  error: ApiError | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  devLogin: (username: string, role: Role) => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

interface SessionState {
  status: SessionStatus;
  me: MeResponse;
  config: ConfigResponse | null;
  error: ApiError | null;
}

/** What one load of /api/config + /api/me found. */
type LoadOutcome =
  | { kind: "ready"; me: MeResponse; config: ConfigResponse }
  | { kind: "offline" }
  | { kind: "error"; error: ApiError; config: ConfigResponse | null };

/** Ask the server for its config and the current user. Never throws (except on abort). */
async function loadSession(signal?: AbortSignal): Promise<LoadOutcome> {
  let config: ConfigResponse;
  try {
    config = await api.config(signal);
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    if (error instanceof ApiError && !isOfflineError(error)) {
      return { kind: "error", error, config: null };
    }
    return { kind: "offline" };
  }

  try {
    const me = await api.me(signal);
    return { kind: "ready", me, config };
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    if (error instanceof ApiError && error.status === 401) {
      return { kind: "ready", me: SIGNED_OUT, config }; // 401 simply means "not signed in"
    }
    if (error instanceof ApiError && !isOfflineError(error)) {
      return { kind: "error", error, config };
    }
    return { kind: "offline" };
  }
}

/** The next session state. An error keeps the last known user and config (see "error" above). */
function applyOutcome(previous: SessionState, outcome: LoadOutcome): SessionState {
  if (outcome.kind === "ready") {
    return { status: "ready", me: outcome.me, config: outcome.config, error: null };
  }
  if (outcome.kind === "offline") {
    return { status: "offline", me: SIGNED_OUT, config: null, error: null };
  }
  return { status: "error", me: previous.me, config: outcome.config ?? previous.config, error: outcome.error };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({
    status: "loading",
    me: SIGNED_OUT,
    config: null,
    error: null,
  });

  const refresh = useCallback(async () => {
    const outcome = await loadSession();
    setState((previous) => applyOutcome(previous, outcome));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadSession(controller.signal)
      .then((outcome) => setState((previous) => applyOutcome(previous, outcome)))
      .catch(() => {
        // Aborted because the provider unmounted: nothing to update.
      });
    return () => controller.abort();
  }, []);

  const signOut = useCallback(async () => {
    await api.logout();
    clearCsrfToken();
    await refresh();
  }, [refresh]);

  const devLogin = useCallback(
    async (username: string, role: Role) => {
      await api.devLogin(username, role);
      clearCsrfToken();
      await refresh();
    },
    [refresh],
  );

  const value = useMemo<SessionValue>(
    () => ({
      ...state,
      productName: state.config?.product_name || PRODUCT_NAME,
      disclaimer: state.config?.disclaimer || DISCLAIMER,
      refresh,
      signOut,
      devLogin,
    }),
    [state, refresh, signOut, devLogin],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** Read the session. Must be used inside <SessionProvider> (the root layout provides it). */
export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession() must be used inside <SessionProvider>.");
  }
  return value;
}
