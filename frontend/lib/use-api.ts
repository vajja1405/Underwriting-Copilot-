"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "@/lib/api";
import { describeProblem, type Problem } from "@/lib/api-errors";

/**
 * Load one API resource for a component.
 *
 * - `load(signal)` runs whenever `key` changes (and on `reload()`); pass `key: null` to wait (for example until the
 *   user is signed in or an id is known).
 * - While a new key or a retry loads, the previous data stays on screen (`refreshing` is true) instead of flashing a
 *   skeleton; pages dim it. A failure keeps its Problem visible while the retry runs.
 * - A failure becomes a `Problem` with plain wording (lib/api-errors.ts). An aborted request (the component went
 *   away or the key changed) is ignored.
 *
 * The status is derived from which request last finished, so the effect only sets state when a request settles.
 */
export type ResourceStatus = "idle" | "loading" | "ready" | "error";

export interface Resource<T> {
  status: ResourceStatus;
  data: T | null;
  problem: Problem | null;
  refreshing: boolean;
  reload: () => void;
  /** Replace the data without a request (for example with the answer of a PUT). */
  setData: (data: T) => void;
}

interface Settled<T> {
  /** The key and attempt of the request that produced this result ("key#attempt"). */
  token: string | null;
  key: string | null;
  data: T | null;
  problem: Problem | null;
}

export function useApiResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  action = "Loading",
): Resource<T> {
  const [attempt, setAttempt] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ token: null, key: null, data: null, problem: null });
  const token = key === null ? null : `${key}#${attempt}`;

  // Keep the latest loader without making it an effect dependency (callers pass inline functions).
  const loadRef = useRef(load);
  const tokenRef = useRef(token);
  useEffect(() => {
    loadRef.current = load;
    tokenRef.current = token;
  });

  useEffect(() => {
    if (token === null) {
      return;
    }
    const controller = new AbortController();
    loadRef
      .current(controller.signal)
      .then((value) => setSettled({ token, key, data: value, problem: null }))
      .catch((error: unknown) => {
        if (isAbortError(error)) {
          return;
        }
        setSettled((previous) => ({ token, key, data: previous.data, problem: describeProblem(error, action) }));
      });
    return () => controller.abort();
  }, [token, key, action]);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  const setData = useCallback(
    (value: T) => setSettled({ token: tokenRef.current, key: tokenRef.current?.replace(/#\d+$/, "") ?? null, data: value, problem: null }),
    [],
  );

  const current = token !== null && settled.token === token;
  const sameKey = key !== null && settled.key === key;
  const problem = sameKey ? settled.problem : null;
  const data = token === null ? null : settled.data;

  let status: ResourceStatus;
  if (token === null) {
    status = "idle";
  } else if (current) {
    status = problem ? "error" : "ready";
  } else if (problem) {
    status = "error";
  } else {
    status = data !== null ? "ready" : "loading";
  }

  return { status, data, problem, refreshing: token !== null && !current, reload, setData };
}
